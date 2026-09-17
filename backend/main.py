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

from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from . import config, l2l_client, metrics, trending
except ImportError:  # running as `uvicorn main:app` from inside backend/
    import config
    import l2l_client
    import metrics
    import trending


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    await l2l_client.close_client()


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
    l2l_linecode: str


class MetricOut(BaseModel):
    id: str
    label: str
    category: str
    unit: str
    decimals: int
    higher_is_better: Optional[bool]
    available_in: list


@app.get("/api/lines", response_model=list[LineOut])
def get_lines():
    return [{"code": code, "l2l_linecode": lc} for code, lc in config.LINECODE_DICT.items()]


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

    if linecodes:
        requested = [c.strip() for c in linecodes.split(",") if c.strip()]
        unknown = [c for c in requested if c not in config.LINECODE_DICT]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown line code(s): {', '.join(unknown)}")
        codes = requested
    else:
        codes = list(config.LINECODE_DICT.keys())

    l2l_codes = [config.LINECODE_DICT[c] for c in codes]
    # trending.fetch_trending works in terms of L2L linecodes internally but
    # the dashboard should only ever see our friendly BP-LINEn codes, so we
    # fetch keyed by L2L code and then relabel the result.
    raw = await trending.fetch_trending(l2l_codes, start, end, granularity)

    reverse_map = {lc: code for code, lc in config.LINECODE_DICT.items()}
    relabeled_series = {}
    for key, series in raw["series"].items():
        label = reverse_map.get(key, key)  # "ALL_LINES" passes through unchanged
        relabeled_series[label] = series
    relabeled_errors = {reverse_map.get(k, k): v for k, v in raw["errors"].items()}

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
