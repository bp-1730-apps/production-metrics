# L2L Trending Dashboard

## What's here

- `index.html` -- a single-file, dependency-free dashboard. By
  default it reads the static `data/*.json` files sitting next to it --
  no backend needs to be running anywhere for that.
- `data/` -- generated, not hand-written. `scripts/refresh_data.py`
  writes it; a scheduled GitHub Action runs that script every 30 minutes
  and commits the result. This is what makes the dashboard "just work"
  for anyone who opens it, with no setup on their end. It won't exist
  until the first refresh runs (see setup below).
- `backend/` -- the L2L client, field-normalization, and aggregation
  logic. Used two ways: `scripts/refresh_data.py` imports it directly to
  build `data/`, and it can optionally also run as a live FastAPI service
  (`uvicorn backend.main:app`) for local development.
- `.github/workflows/refresh-data.yml` -- the scheduled job that keeps
  `data/` fresh.
- `render.yaml` / `Procfile` -- only needed for the *optional* live-backend
  path described near the bottom; skip them for the default setup.
- `backend/test_api.py`, `backend/test_rolling.py` -- tests against the
  sample payloads you provided and the incremental refresh logic, no real
  network calls.

## Setup (GitHub-only, no server to run)

1. **Push this to GitHub**, if you haven't already:

   ```bash
   cd l2l-trending-dashboard
   git init
   git add .
   git status              # confirm .env is NOT listed -- only .env.example should be
   git commit -m "Initial commit: L2L trending dashboard"
   git remote add origin <your-repo-url>
   git branch -M main
   git push -u origin main
   ```

2. **Add your L2L key as a GitHub Actions secret** (never committed to the
   repo): repo **Settings -> Secrets and variables -> Actions -> New
   repository secret**, name it `L2L_API_KEY`, paste your real key.

3. **Run the refresh once manually** to create `data/` for the first
   time: **Actions** tab -> **Refresh L2L trending data** -> **Run
   workflow**. It takes a minute or two; when it finishes, check that a
   new commit ("Refresh trending data...") appears with a `data/` folder
   in it.

4. **Enable GitHub Pages**: **Settings -> Pages** -> Source: **Deploy from
   a branch** -> branch `main`, folder `/ (root)`. Save, wait about a
   minute for the URL to appear.

5. **Open the Pages URL**, e.g. `https://<you>.github.io/<repo>/` --
   because the file is named `index.html`, GitHub Pages serves it at that
   root URL automatically, no filename needed. It reads `data/` directly
   from the same site, nothing to configure.

From here, the workflow keeps `data/` refreshed automatically every 30
minutes on its own. If the dashboard ever looks stale, check the
**Actions** tab for failed runs before assuming something's broken in the
dashboard itself.

## Troubleshooting: "No lines found" / every line shows identical numbers

Both mean the same underlying thing: `backend/config.py`'s `L2L_SITE_NUMBER`
and/or `LINECODE_DICT` values don't match real records in your L2L account.
"No lines found" is L2L's hard error when a *required* line filter matches
nothing (the weekly endpoint); identical values across every line is what
happens on the daily endpoint instead, since its line filter is optional --
an unmatched value is silently ignored rather than rejected, so it quietly
falls back to unfiltered, plant-wide totals for every "line" you ask for.

Don't guess new values by hand -- ask L2L's own API which ones are real:

```bash
cd l2l-trending-dashboard
export L2L_API_KEY=your-real-key
python3 scripts/discover_lines.py
```

This prints every Site record your key can see (its real `site` code --
that's what `L2L_SITE_NUMBER` should be) and every Line record (its real
`code` field -- that's what `LINECODE_DICT`'s values should be), straight
from L2L's `/sites/` and `/lines/` master-data endpoints. Match the site
whose description looks like Plant 1730 / Buena Park, then match its six
lines by description to BP-LINE1..6, and update `backend/config.py`
accordingly (or set the `L2L_SITE_NUMBER` GitHub Actions secret if only the
site number was wrong).

## Local development

To test against the exact static files that'll ship to GitHub Pages:

```bash
pip install -r backend/requirements.txt
export L2L_API_KEY=your-real-key
python3 scripts/refresh_data.py      # writes data/*.json locally
python3 -m http.server 8000          # serve this folder
# open http://localhost:8000/index.html
```

Run the tests any time you touch `backend/`:

```bash
pip install pytest anyio httpx
pytest backend/ -v
```

## Alternative: a live backend instead of scheduled snapshots

If a 15-30 minute refresh lag is a real problem and you want on-demand,
always-current data instead, the original live FastAPI backend is still
here and still works:

- Locally: `uvicorn backend.main:app --reload --port 8000`
- Deployed: `render.yaml` is a Render Blueprint (New -> Blueprint -> pick
  this repo -> it asks for `L2L_API_KEY`); `Procfile` works the same way
  on Railway or similar buildpack hosts. Render's free tier sleeps after
  inactivity and takes ~30-60s to wake up on the first request.

Then open the dashboard with `?live=<backend-url>` appended, e.g.
`index.html?live=https://your-backend.onrender.com`. This is a
per-visit override (not a saved setting), meant for testing one backend
against the dashboard -- it does not change what anyone else sees when
they open the plain URL. If you want live mode to be the default for
everyone, that's a small code change (swap which branch `refresh()` takes
in `index.html`) -- ask if you want that instead of the static setup.

## Why a 30-minute schedule doesn't hammer L2L's API

A naive "refetch everything every 30 minutes" would mean roughly 900 L2L
calls per run (6 lines x 120 days of daily data, plus weekly) -- most of
it re-fetching numbers that haven't changed since the last run.
`backend/rolling.py` avoids that: each run loads the previously-saved
`data/trending-*.json`, and only fetches (a) periods that are newly in
range since last time, and (b) a short "tail" of the most recent 2-3
periods, which may still be updating (today's numbers, this week's
numbers). Everything older is reused verbatim. In testing, a same-day
re-run costs about 30 calls total instead of 900. `scripts/refresh_data.py`
controls the window size (`DAY_WINDOW_DAYS`, `WEEK_WINDOW_DAYS`) and tail
length (`DAY_TAIL_PERIODS`, `WEEK_TAIL_PERIODS`) if you want to tune either.

## Data files (what `index.html` actually reads)

- `data/lines.json` -- the configured line codes (BP-LINE1..6).
- `data/metrics-day.json`, `data/metrics-week.json` -- the metric
  catalog: id, label, category, unit, decimals, higher_is_better, which
  granularities it applies to. The dashboard builds its tile grid
  entirely from this, so a new L2L field shows up automatically
  (auto-labeled) instead of being silently dropped.
- `data/trending-day.json`, `data/trending-week.json` -- the rolling
  window itself: `{ granularity, start_date, end_date, generated_at,
  lines, series: { "BP-LINE1": [...], ..., "ALL_LINES": [...] }, errors }`.
  The dashboard fetches each of these once per granularity and filters
  them client-side to whatever date range you pick -- changing dates
  never triggers a new network request.

(The live backend's `GET /api/lines`, `/api/metrics`, `/api/trending`
endpoints return the same shapes, for the `?live=` path above.)

## Notable design decisions

- **OEE field-name mismatch fixed centrally.** L2L's weekly endpoint
  calls it `average_oee`; the daily endpoint calls it
  `overall_equipment_effectiveness`. `backend/metrics.py` maps both to
  one canonical id (`oee`) so nothing downstream has to know which
  granularity it's looking at.
- **Daily multi-record aggregation.** If the daily endpoint returns more
  than one row for a line/day (e.g. split by shift or product), counts
  (actual, scrap, downtime minutes, etc.) are summed and rates/percentages
  (OEE, availability, yield, etc.) are combined as a weighted average by
  planned production minutes. This is a reasonable dashboard-grade
  approximation, not a claim that it reproduces L2L's own OEE formula
  bit-for-bit -- worth keeping in mind if you ever reconcile against a
  report pulled straight from L2L's UI.
- **Incremental rolling refresh**, see above -- the whole reason a
  schedule this frequent is practical at all.
- **No external dependencies in the dashboard.** The chart, tooltip,
  crosshair, and sparklines are hand-rolled SVG/JS rather than pulled from
  a CDN, since this may end up running on a production-floor kiosk that
  can't rely on outside internet access.
- **Security note:** the L2L API key lives only as a GitHub Actions
  secret (or in `backend/.env` locally, gitignored) -- it's read
  server-side by the refresh script or the live backend, never sent to
  the browser.

## Known gaps / things to revisit

- The daily-record aggregation's weighted-average approach is an
  approximation (see above) -- validate it against a report you trust if
  the numbers need to be audit-grade.
- **Repo size grows slowly over time.** Every refresh that finds new data
  commits an updated `data/trending-*.json` (a few hundred KB). Git
  compresses this well, but if the repo's history size ever bothers you,
  periodically squashing history (or dropping `data/` from history and
  keeping only the latest commit) is a reasonable cleanup -- the files are
  regenerated from L2L, not something you need history for.
- **GitHub Actions schedules can slip.** GitHub explicitly reserves the
  right to delay scheduled runs under load, and disables a schedule
  entirely if the repo sees no other activity for 60 days. If the
  dashboard looks stale, check the Actions tab and use "Run workflow" to
  fire it manually.
- Switching everyone to live mode by default (instead of `?live=` being a
  per-visit opt-in) is a small, deliberate code change, not a runtime
  setting -- see the "Alternative" section above.
- There's no auth on the live FastAPI service, if you do deploy it --
  fine on a trusted internal network, not fine if it's ever exposed more
  broadly. `CORS_ALLOW_ORIGINS` in Render's environment variables should
  be narrowed to your actual Pages origin.
