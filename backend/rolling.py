"""
Maintains a rolling window of trending data across repeated runs. Used by
scripts/refresh_data.py, which a GitHub Actions schedule re-runs every
15-30 minutes so the dashboard has fresh data without needing a live
backend running anywhere.

Naively re-fetching a wide window from L2L on every run would mean ~900
API calls every time (6 lines x 120 days, plus weekly), most of which
would return data that hasn't changed since the last run. Instead, this
keeps whatever the previous run already saved, and only fetches:

  - periods that have newly entered the window (time has passed since the
    last run), and
  - a short "tail" of the most recent periods, which may still be
    updating (today's numbers, this week's numbers).

Anything older than the tail is treated as settled and is never re-fetched
again, which keeps each run's L2L call volume small regardless of how
wide the overall window is.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Optional

from . import trending


def _window_period_starts(anchor: date, today: date, step_days: int, max_periods: int) -> list[date]:
    """Every period start from `anchor` through `today` (reusing
    trending._period_starts' simple day-stepping), trimmed to the most
    recent `max_periods` -- this is what actually makes the window
    "roll forward" and drop old periods as time passes."""
    granularity = "week" if step_days == 7 else "day"
    periods = trending._period_starts(anchor, today, granularity)
    return periods[-max_periods:] if max_periods else periods


def _extract_existing(existing: Optional[dict], key: str) -> dict:
    """period_start (ISO string) -> saved row, for one series key (a
    display line code or ALL_LINES), from a previously-written
    trending-*.json payload."""
    if not existing:
        return {}
    series = (existing.get("series") or {}).get(key) or []
    return {row["period_start"]: row for row in series if "period_start" in row}


async def update_rolling_series(
    existing: Optional[dict],
    line_pairs: list[tuple[str, str]],  # (display_code, l2l_linecode)
    granularity: str,
    window_days: int,
    tail_periods: int,
    site: int,
) -> dict:
    step_days = 7 if granularity == "week" else 1
    max_periods = window_days // step_days + 1
    today = date.today()

    # Anchor on wherever the previous run's window started, so period
    # boundaries stay on the same grid across runs instead of shifting
    # every time. First run ever: start far enough back to fill the window.
    anchor = today - timedelta(days=window_days)
    if existing and existing.get("start_date"):
        try:
            anchor = date.fromisoformat(existing["start_date"])
        except ValueError:
            pass

    periods = _window_period_starts(anchor, today, step_days, max_periods)
    tail_set = set(periods[-tail_periods:]) if tail_periods else set()

    series_out: dict[str, list[dict]] = {}
    errors_out: dict[str, list[dict]] = {}

    async def resolve_line(display_code: str, l2l_code: str):
        existing_rows = _extract_existing(existing, display_code)

        def _needs_fetch(p: date) -> bool:
            iso = p.isoformat()
            if iso not in existing_rows or p in tail_set:
                return True
            # A period saved with no metrics means every prior fetch attempt
            # for it failed (or L2L genuinely had nothing for that
            # line/period). Keep retrying it on every run rather than
            # freezing it as permanently empty -- once the real cause is
            # fixed (bad linecode, wrong site, a transient L2L outage,
            # etc.) this is what lets old periods heal without anyone
            # having to delete data/*.json and force a full refetch.
            return not existing_rows[iso].get("metrics")

        to_fetch = {
            p: trending._fetch_one_period(l2l_code, p, granularity, site)
            for p in periods
            if _needs_fetch(p)
        }

        fetched = {}
        if to_fetch:
            results = await asyncio.gather(*to_fetch.values(), return_exceptions=True)
            for p, result in zip(to_fetch.keys(), results):
                if isinstance(result, Exception):
                    errors_out.setdefault(display_code, []).append({
                        "period_start": p.isoformat(), "error": str(result),
                    })
                else:
                    fetched[p.isoformat()] = result

        rows = []
        for p in periods:
            iso = p.isoformat()
            if iso in fetched:
                rows.append(fetched[iso])
            elif iso in existing_rows:
                rows.append(existing_rows[iso])
            else:
                # Fetch failed this run and we have no prior data for it --
                # keep a placeholder so every line's list stays index-aligned
                # with `periods`. The dashboard already renders gaps as
                # "no data" rather than erroring.
                rows.append({"period_start": iso, "period_end": iso, "metrics": {}})
        series_out[display_code] = rows

    await asyncio.gather(*[resolve_line(dc, lc) for dc, lc in line_pairs])

    if len(line_pairs) > 1:
        combined = []
        for i, p in enumerate(periods):
            rows_here = []
            period_end = p.isoformat()
            for dc, _ in line_pairs:
                line_series = series_out.get(dc) or []
                if i < len(line_series):
                    rows_here.append(line_series[i]["metrics"])
                    period_end = line_series[i]["period_end"]
            combined.append({
                "period_start": p.isoformat(),
                "period_end": period_end,
                "metrics": trending._combine_line_rows(rows_here),
            })
        series_out["ALL_LINES"] = combined

    return {
        "granularity": granularity,
        "start_date": periods[0].isoformat() if periods else today.isoformat(),
        "end_date": today.isoformat(),
        "lines": [dc for dc, _ in line_pairs],
        "series": series_out,
        "errors": errors_out,
    }
