"""
Builds trending series for one or more lines over a date range, at either
day or week granularity. This is the async replacement for the prototype's
trending_data() loop, extended to: run requests concurrently instead of
one-by-one, support an explicit end date (not just "until now"), and
aggregate multi-row daily responses into one row per line per day.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Literal

try:
    from . import l2l_client, metrics
except ImportError:  # running as `uvicorn main:app` from inside backend/
    import l2l_client
    import metrics

Granularity = Literal["day", "week"]


def _period_starts(start: date, end: date, granularity: Granularity) -> list[date]:
    if start > end:
        start, end = end, start
    step = timedelta(days=7) if granularity == "week" else timedelta(days=1)
    periods = []
    cursor = start
    while cursor <= end:
        periods.append(cursor)
        cursor += step
    return periods


async def _fetch_one_period(linecode: str, period_start: date, granularity: Granularity, site: int) -> dict:
    if granularity == "week":
        rows = await l2l_client.weekly_summary(period_start, linecode, site)
        row = metrics.normalize_record(rows[0]) if rows else {}
        period_end = period_start + timedelta(days=6)
    else:
        rows = await l2l_client.daily_summary(period_start, linecode, site)
        normalized_rows = [metrics.normalize_record(r) for r in rows]
        row = metrics.aggregate_records(normalized_rows)
        period_end = period_start

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "metrics": row,
    }


async def fetch_line_series(linecode: str, start: date, end: date, granularity: Granularity, site: int) -> dict:
    periods = _period_starts(start, end, granularity)
    results = await asyncio.gather(
        *[_fetch_one_period(linecode, p, granularity, site) for p in periods],
        return_exceptions=True,
    )

    series = []
    errors = []
    for period_start, result in zip(periods, results):
        if isinstance(result, Exception):
            errors.append({"period_start": period_start.isoformat(), "error": str(result)})
        else:
            series.append(result)

    return {"linecode": linecode, "series": series, "errors": errors}


def _combine_line_rows(rows_by_line: list[dict]) -> dict:
    """Combine one metrics dict per line (same period) into a plant-level
    total, using the same sum/weighted-average rules as multi-shift daily
    aggregation."""
    non_empty = [r for r in rows_by_line if r]
    return metrics.aggregate_records(non_empty)


async def fetch_trending(
    linecodes: list[str],
    start: date,
    end: date,
    granularity: Granularity,
    site: int,
    include_all_lines: bool = True,
) -> dict:
    per_line = await asyncio.gather(*[
        fetch_line_series(lc, start, end, granularity, site) for lc in linecodes
    ])

    series_by_line = {r["linecode"]: r["series"] for r in per_line}
    errors_by_line = {r["linecode"]: r["errors"] for r in per_line if r["errors"]}

    result = {
        "granularity": granularity,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "lines": linecodes,
        "series": series_by_line,
        "errors": errors_by_line,
    }

    if include_all_lines and len(linecodes) > 1:
        periods = _period_starts(start, end, granularity)
        combined_series = []
        for idx, period_start in enumerate(periods):
            row_period_end = None
            rows = []
            for lc in linecodes:
                line_series = series_by_line.get(lc) or []
                if idx < len(line_series):
                    rows.append(line_series[idx]["metrics"])
                    row_period_end = line_series[idx]["period_end"]
            combined_series.append({
                "period_start": period_start.isoformat(),
                "period_end": row_period_end or period_start.isoformat(),
                "metrics": _combine_line_rows(rows),
            })
        result["series"]["ALL_LINES"] = combined_series

    return result
