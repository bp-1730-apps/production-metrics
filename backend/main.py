"""
FastAPI backend that exposes L2L production trending data to the
dashboard. Run with:

    uvicorn backend.main:app --reload --port 8000

Endpoints:
    GET /api/lines                          -> configured production lines
    GET /api/metrics?granularity=day|week   -> metric catalog (labels/units)
    GET /api/trending?linecodes=...&start_date=...&end_date=...&granularity=...
                                             -> trending series
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from . import config, directory, l2l_client, metrics, trending
except ImportError:  # running as `uvicorn main:app` from inside backend/
    import config
    import directory
    import l2l_client
    import metrics
    import trending


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    await l2l_client.close_client()


# Module-level rather than app.state: resolution is deliberately lazy (see
# _get_resolution below) and shouldn't depend on whether/when Starlette's
# lifespan startup actually ran for a given app instance -- tests that use
# TestClient(app) without a `with` block don't trigger it.
_resolution_lock = asyncio.Lock()
_cached_resolution: Optional[tuple[int, dict]] = None  # (site_code, {display_code: LineResolution})


async def _get_resolution() -> tuple[Optional[int], dict[str, "directory.LineResolution"]]:
    """Resolve BP-LINE1..6 (and the site they live under) to real L2L
    values (backend/directory.py) once per process, caching the result --
    rather than trusting a hardcoded dict; see config.py's SITE_HINTS/
    LINE_HINTS docstring for why. A resolution failure (e.g. L2L briefly
    unreachable) is never cached, so the next request simply tries again
    instead of the app being stuck in a failed state until restarted."""
    global _cached_resolution
    if _cached_resolution is not None:
        return _cached_resolution

    async with _resolution_lock:
        if _cached_resolution is not None:  # resolved while we waited for the lock
            return _cached_resolution
        try:
            site = await directory.resolve_site()
            resolutions = await directory.resolve_lines(site)
        except Exception as exc:  # network error, L2L outage, etc.
            # Every configured display code, all unresolved, all pointing at
            # the same underlying error -- so callers don't need a separate
            # code path for "resolution never even ran" vs. "ran and found
            # nothing", and the next request retries rather than being
            # stuck here for the life of the process.
            return None, {
                dc: directory.LineResolution(dc, hint, None, error=f"Could not reach L2L to resolve lines: {exc}")
                for dc, hint in config.LINE_HINTS.items()
            }
        _cached_resolution = (site.site_code, resolutions)
        return _cached_resolution


app = FastAPI(
    title="L2L Trending API",
    description="Wraps Leading2Lean's reporting endpoints with a normalized, "
                "granularity-independent trending API for Plant 1730.",
    version="1.0.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _parse_date(value: str, field_name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field_name} must be YYYY-MM-DD, got {value!r}")


class LineOut(BaseModel):
    code: str
    l2l_linecode: Optional[str]
    resolved: bool
    matched_description: Optional[str] = None
    error: Optional[str] = None


class MetricOut(BaseModel):
    id: str
    label: str
    category: str
    unit: str
    decimals: int
    higher_is_better: Optional[bool]
    available_in: list


@app.get("/api/lines", response_model=list[LineOut])
async def get_lines():
    _site, line_resolutions = await _get_resolution()
    return [
        {
            "code": display_code,
            "l2l_linecode": res.l2l_code,
            "resolved": res.l2l_code is not None,
            "matched_description": res.matched_description,
            "error": res.error,
        }
        for display_code, res in line_resolutions.items()
    ]


@app.get("/api/metrics", response_model=list[MetricOut])
def get_metrics(granularity: Optional[str] = Query(None, pattern="^(day|week)$")):
    out = []
    for m in metrics.KNOWN_METRICS.values():
        if granularity and granularity not in m.available_in:
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
    return out


@app.get("/api/trending")
async def get_trending(
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    granularity: str = Query("day", pattern="^(day|week)$"),
    linecodes: Optional[str] = Query(
        None, description="Comma-separated line codes, e.g. BP-LINE1,BP-LINE2. Omit for all lines."
    ),
):
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    if start > end:
        raise HTTPException(status_code=400, detail="start_date must be on or before end_date")

    site, line_resolutions = await _get_resolution()
    resolved = {dc: res.l2l_code for dc, res in line_resolutions.items() if res.l2l_code}

    if linecodes:
        requested = [c.strip() for c in linecodes.split(",") if c.strip()]
        unknown = [c for c in requested if c not in line_resolutions]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown line code(s): {', '.join(unknown)}")
        codes = requested
    else:
        codes = list(line_resolutions.keys())

    unresolved = [c for c in codes if c not in resolved]
    resolved_codes = [c for c in codes if c in resolved]

    l2l_codes = [resolved[c] for c in resolved_codes]
    # trending.fetch_trending works in terms of L2L linecodes internally but
    # the dashboard should only ever see our friendly BP-LINEn codes, so we
    # fetch keyed by L2L code and then relabel the result.
    raw = await trending.fetch_trending(l2l_codes, start, end, granularity, site) if l2l_codes and site is not None else {
        "granularity": granularity, "start_date": start.isoformat(), "end_date": end.isoformat(),
        "lines": [], "series": {}, "errors": {},
    }

    reverse_map = {lc: dc for dc, lc in resolved.items()}
    relabeled_series = {}
    for key, series in raw["series"].items():
        label = reverse_map.get(key, key)  # "ALL_LINES" passes through unchanged
        relabeled_series[label] = series
    relabeled_errors = {reverse_map.get(k, k): v for k, v in raw["errors"].items()}

    # Lines that never resolved to a real L2L code (see /api/lines for why)
    # show up here too, as a single diagnostic entry, rather than silently
    # vanishing from the response.
    for dc in unresolved:
        relabeled_errors.setdefault(dc, []).append(
            {"period_start": start.isoformat(), "error": line_resolutions[dc].error}
        )

    return {
        "granularity": raw["granularity"],
        "start_date": raw["start_date"],
        "end_date": raw["end_date"],
        "lines": codes,
        "series": relabeled_series,
        "errors": relabeled_errors,
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}
