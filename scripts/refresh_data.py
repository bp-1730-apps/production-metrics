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

from backend import config, l2l_client, metrics, rolling  # noqa: E402

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


async def main():
    DATA_DIR.mkdir(exist_ok=True)
    line_pairs = list(config.LINECODE_DICT.items())  # [(display_code, l2l_linecode), ...]

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
        )
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
        [{"code": code, "l2l_linecode": lc} for code, lc in config.LINECODE_DICT.items()],
        indent=2,
    ) + "\n")

    await l2l_client.close_client()


if __name__ == "__main__":
    asyncio.run(main())
