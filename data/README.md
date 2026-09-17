This folder is generated, not hand-written.

`scripts/refresh_data.py` writes `trending-day.json`, `trending-week.json`,
`metrics-day.json`, `metrics-week.json`, and `lines.json` here. A scheduled
GitHub Action (`.github/workflows/refresh-data.yml`) runs that script every
30 minutes and commits whatever changed.

If you don't see those `.json` files yet, the workflow hasn't run
successfully yet -- go to the repo's **Actions** tab, open **Refresh L2L
trending data**, and click **Run workflow** to trigger the first one
manually. See the main README's "Setup" section for the full walkthrough.
