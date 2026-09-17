# L2L Trending Dashboard

## What's here

- `backend/` -- a FastAPI service that wraps L2L's weekly/daily reporting
  endpoints, normalizes their field names into one metric catalog, and
  exposes it as a clean trending API.
- `dashboard.html` -- a single-file, dependency-free dashboard (no CDN,
  no build step) that consumes that API.
- `backend/test_api.py` -- tests against the two sample payloads you
  provided, with no real network calls.

## Running it

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env      # then edit .env and put your real L2L_API_KEY in it
cd ..
uvicorn backend.main:app --reload --port 8000
```

(Run `uvicorn` from this directory, one level *above* `backend/` -- not
from inside `backend/`. `main.py` works either way now, but running it as
`backend.main` from here is the more standard way to launch a FastAPI
app laid out as a package.)

Then open `dashboard.html` in a browser (double-click it, or serve it with
any static file server). It talks to `http://localhost:8000` by default.
If your backend is somewhere else, you don't need to edit the file: click
the &#9881; (settings) button in the top bar and paste the real address --
it's remembered on that browser from then on. (You can also permanently
change the default for everyone by editing `DEFAULT_API_BASE` near the top
of the `<script>` block, or hand someone a link like
`dashboard.html?api=https://your-backend-url` to set it for them automatically.)

Run the tests any time you change the backend:

```bash
pip install pytest anyio httpx
pytest backend/test_api.py -v
```

## Deploying so it works for everyone (not just your machine)

Right now, whoever runs `uvicorn` is the only person who can use the
dashboard -- it's only reachable from that machine. To make it work for
everyone, two things need to each live somewhere that's always on:

**1. Put the code on GitHub** (you'll point both of the next two steps at it):

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

`.env` is excluded by `.gitignore` and was never generated in this
download, so there's nothing to accidentally leak here -- just never
`git add -f` it later.

**2. Deploy the backend somewhere that stays running.** A `render.yaml` is
included so this is close to one-click on [Render](https://render.com)
(free tier): New -> Blueprint -> pick this GitHub repo -> Render reads
`render.yaml` and asks you to paste in `L2L_API_KEY` (kept as a secret,
never committed). When it finishes you'll have a permanent URL like
`https://l2l-trending-api.onrender.com`. (A `Procfile` is also included if
you'd rather use Railway or another buildpack-based host instead.)

Free-tier note: Render's free web services spin down after periods of
inactivity and take ~30-60 seconds to wake back up on the next request --
fine for an internal tool, worth knowing so the first load of the day
isn't mistaken for it being broken. Paid tiers avoid that.

**3. Host `dashboard.html` somewhere everyone can open it.** Easiest
option since the code's already on GitHub: repo Settings -> Pages ->
Deploy from a branch -> `main` / `(root)`. You'll get a URL like
`https://<you>.github.io/<repo>/dashboard.html`.

**4. Point the dashboard at the deployed backend.** Edit `DEFAULT_API_BASE`
near the top of `dashboard.html`'s `<script>` block to the Render URL from
step 2, commit, and push -- GitHub Pages picks it up automatically. Now
anyone who opens the Pages URL gets a working dashboard with no setup on
their end.

**5. Tighten CORS.** In Render's environment variables, set
`CORS_ALLOW_ORIGINS` to your actual GitHub Pages origin (e.g.
`https://<you>.github.io`) instead of leaving it at `*`, so the API only
answers requests from your dashboard.

After that, GitHub involvement is done -- it hosted the code, Render runs
the backend continuously, and GitHub Pages serves the page. Nothing about
this needs a terminal open on anyone's machine anymore.

## API

- `GET /api/lines` -- the configured line codes (BP-LINE1..6).
- `GET /api/metrics?granularity=day|week` -- the metric catalog: id,
  label, category, unit, decimals, higher_is_better, which
  granularities it's available in. The dashboard builds its tile grid
  entirely from this, so if L2L starts returning a new field, it shows
  up automatically (auto-labeled) instead of being silently dropped.
- `GET /api/trending?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&granularity=day|week&linecodes=BP-LINE1,BP-LINE2`
  -- trending series. Omit `linecodes` to get all 6 lines plus a
  `Plant Total` (ALL_LINES) series that combines them.

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
- **7-day API window respected.** Both weekly and daily trending loop one
  L2L call per line per period (matching how your original script pulled
  weekly data), run concurrently, and are cached briefly so re-opening a
  tile doesn't refetch. Fully-elapsed periods are cached much longer than
  the current/in-progress one.
- **No external dependencies in the dashboard.** The chart, tooltip,
  crosshair, and sparklines are hand-rolled SVG/JS rather than pulled from
  a CDN, since this is meant to run on a production-floor kiosk that may
  not have reliable internet access.
- **Security note:** the L2L API key now lives only in `backend/.env`
  (gitignore it), read server-side -- it's never sent to the browser. Keep
  it that way; don't move it back into client-side JS.

## Known gaps / things to revisit

- The daily-record aggregation's weighted-average approach is an
  approximation (see above) -- validate it against a report you trust if
  the numbers need to be audit-grade.
- CORS is wide open (`*`) by default for easy local testing; tighten
  `CORS_ALLOW_ORIGINS` in `.env` once you know where the dashboard will
  actually be hosted.
- There's no auth on the FastAPI service itself -- fine on a trusted
  internal network, not fine if it's ever exposed more broadly.
