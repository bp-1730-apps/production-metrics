#!/usr/bin/env python3
"""
Pulls fresh data from L2L and writes it out as the static JSON files
index.html reads directly. Run on a schedule by
.github/workflows/refresh-data.yml -- no live backend needs to be running
anywhere once these files are committed and served (e.g. by GitHub Pages).

Also safe to run locally, for testing or a one-off manual refresh:

    cd l2l-trending-dashboard
    export L2L_API_KEY=your-real-key
    python3 scripts/refresh_data.py

Each run only re-fetches periods that are new since the last run plus a
short "tail" of the most recent periods (see backend/rolling.py for why) --
so running this every 15-30 minutes stays cheap even though the dashboard
shows months of history.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, directory, l2l_client, metrics, rolling  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# How wide a window to keep, and how many of the most recent periods to
# treat as "still updating" and re-check every run. Widen DAY_WINDOW_DAYS/
# WEEK_WINDOW_DAYS if you add a longer preset to the dashboard's UI.
DAY_WINDOW_DAYS = 120
DAY_TAIL_PERIODS = 3
WEEK_WINDOW_DAYS = 210
WEEK_TAIL_PERIODS = 2


def _load_existing(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _write_metrics_catalog(granularity: str, path: Path):
    out = []
    for m in metrics.KNOWN_METRICS.values():
        if granularity not in m.available_in:
            continue
        out.append({
            "id": m.id,
            "label": m.label,
            "category": m.category,
            "unit": m.unit,
            "decimals": m.decimals,
            "higher_is_better": m.higher_is_better,
            "available_in": list(m.available_in),
        })
    out.sort(key=lambda m: (m["category"], m["label"]))
    path.write_text(json.dumps(out, indent=2) + "\n")


async def _resolve_lines():
    """Resolve BP-LINE1..6 to real L2L site/line values (backend/directory.py)
    instead of trusting a hardcoded dict, and print a plain-language summary
    to stdout -- this is what shows up directly in the GitHub Actions log,
    so a bad hint in config.py's SITE_HINTS/LINE_HINTS is visible on every
    scheduled run without needing a separate diagnostic script.

    Note: this makes real network calls to L2L and deliberately does NOT
    catch exceptions -- a transient failure here (L2L briefly unreachable,
    DNS hiccup, etc.) should crash this script *before* any data/*.json
    file is touched, so a bad run shows up as a failed GitHub Actions run
    rather than silently overwriting good historical data with an empty
    result. See main()'s docstring note below for the same reasoning
    applied to a resolved-but-empty result, which is different (a
    persistent config problem, not a transient one) and does get written."""
    site = await directory.resolve_site()
    if site.error:
        print(f"[resolve] SITE: {site.error}")
    else:
        print(f"[resolve] site -> {site.site_code} (matched description: {site.matched_description!r})")

    resolutions = await directory.resolve_lines(site)
    line_pairs = []
    resolution_errors: dict[str, str] = {}
    for display_code, res in resolutions.items():
        if res.l2l_code:
            print(f"[resolve] {display_code} -> {res.l2l_code!r} (matched on hint {res.hint!r}: {res.matched_description!r})")
            line_pairs.append((display_code, res.l2l_code))
        else:
            print(f"[resolve] {display_code}: {res.error}")
            resolution_errors[display_code] = res.error

    if site.error:
        resolution_errors["_site"] = site.error
        if line_pairs:
            # Individual lines can still fuzzy-match by description even
            # when the site itself didn't resolve (see directory.py's
            # site-scoping fallback) -- but without a real site code there
            # is no valid value to send as the reporting API's required
            # "site" parameter, so don't attempt to fetch anything; surface
            # this plainly instead of sending a request that can only fail.
            print(f"[resolve] NOTE: {len(line_pairs)} line(s) above matched by name, but "
                  f"can't be fetched without a resolved site code. Fix SITE_HINTS first.")
        line_pairs = []

    return line_pairs, resolution_errors, resolutions, site


async def main():
    DATA_DIR.mkdir(exist_ok=True)

    # Deliberately not wrapped in try/except: see _resolve_lines()'s
    # docstring -- a transient failure here should crash this script before
    # any data file is written, not produce an empty one.
    line_pairs, resolution_errors, resolutions, site = await _resolve_lines()
    if not line_pairs:
        print("[resolve] WARNING: no lines resolved at all -- every tile will show "
              "no data until config.py's SITE_HINTS/LINE_HINTS match your real L2L "
              "account. See the [resolve] lines above for exactly what L2L returned. "
              "Writing data/*.json anyway (with these errors recorded in them) rather "
              "than leaving the dashboard on stale data from a previous, possibly "
              "different misconfiguration.")

    for granularity, window_days, tail in [
        ("day", DAY_WINDOW_DAYS, DAY_TAIL_PERIODS),
        ("week", WEEK_WINDOW_DAYS, WEEK_TAIL_PERIODS),
    ]:
        out_path = DATA_DIR / f"trending-{granularity}.json"
        existing = _load_existing(out_path)

        result = await rolling.update_rolling_series(
            existing=existing,
            line_pairs=line_pairs,
            granularity=granularity,
            window_days=window_days,
            tail_periods=tail,
            site=site.site_code,
        )
        # Surface unresolved lines/site as errors too, so they show up in
        # the dashboard's "N error(s) during the last refresh" banner and
        # in the raw JSON, not just in this run's now-gone stdout.
        for display_code, err in resolution_errors.items():
            result["errors"].setdefault(display_code, []).append({
                "period_start": result.get("start_date"), "error": err,
            })
        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        out_path.write_text(json.dumps(result, indent=2) + "\n")

        row_count = sum(len(v) for v in result["series"].values())
        err_count = sum(len(v) for v in result["errors"].values())
        print(f"[{granularity}] wrote {out_path.name}: {row_count} rows across "
              f"{len(result['series'])} series, {err_count} fetch errors, "
              f"window {result['start_date']} to {result['end_date']}")

        _write_metrics_catalog(granularity, DATA_DIR / f"metrics-{granularity}.json")

    lines_path = DATA_DIR / "lines.json"
    lines_path.write_text(json.dumps(
        [
            {
                "code": display_code,
                "l2l_linecode": res.l2l_code,
                "resolved": res.l2l_code is not None,
                "matched_description": res.matched_description,
                "error": res.error,
            }
            for display_code, res in resolutions.items()
        ],
        indent=2,
    ) + "\n")

    await l2l_client.close_client()


if __name__ == "__main__":
    asyncio.run(main())
