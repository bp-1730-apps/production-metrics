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
any static file server). It talks to `http://localhost:8000` by default --
change `CONFIG.API_BASE` near the top of the `<script>` block in
`dashboard.html` if you run the backend somewhere else (e.g. a server on
the plant network instead of your own machine).

Run the tests any time you change the backend:

```bash
pip install pytest anyio httpx
pytest backend/test_api.py -v
```

## Pushing to GitHub

A `.gitignore` is included (it excludes `.env`, `__pycache__/`, `.pytest_cache/`).
`backend/.env` was never generated in this download, so there's nothing to
accidentally commit -- just don't `git add -f` it later.

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

Two things GitHub itself does *not* do for you, worth remembering after you push:

- It doesn't run the backend. Something (your machine, a plant-network
  server, a host like Railway/Render/Fly.io) still has to run
  `uvicorn backend.main:app`.
- It doesn't change `CONFIG.API_BASE` in `dashboard.html`, which is
  hardcoded to `http://localhost:8000`. Whoever opens the dashboard needs
  that value to point at wherever the backend is actually reachable from
  them -- update it (and `CORS_ALLOW_ORIGINS` in `.env`) if that's not
  localhost.

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
