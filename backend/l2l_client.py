"""
Async client for the L2L reporting API, adapted from the original
synchronous prototype script:

  - get_l2l_data() -> now async, uses a shared httpx.AsyncClient
  - weekly_summary()/daily_summary() -> thin wrappers over the two
    reporting endpoints
  - a small in-memory TTL cache so expanding the same tile twice (or
    two tiles that need the same line/period) doesn't re-hit L2L
  - a bounded semaphore so a wide date range doesn't fire dozens of
    concurrent requests at L2L at once
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta
from typing import Optional

import httpx

try:
    from . import config
except ImportError:  # running as `uvicorn main:app` from inside backend/
    import config


class L2LAPIError(RuntimeError):
    """Raised when L2L returns success=False or an unusable response."""


class _TTLCache:
    def __init__(self):
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        entry = self._store.get(key)
        if not entry:
            return None
        expires_at, value = entry
        if time.time() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value, ttl_seconds: int):
        self._store[key] = (time.time() + ttl_seconds, value)


_cache = _TTLCache()
_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_L2L_REQUESTS)
_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def close_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def get_l2l_data(path: str, extra_params: dict) -> dict:
    params = {"auth": config.L2L_API_KEY}
    params.update(extra_params)
    url = f"{config.L2L_BASE_URL}/{path}/"

    client = get_client()
    async with _semaphore:
        response = await client.get(url, params=params)

    try:
        reply = response.json()
    except ValueError as exc:
        raise L2LAPIError(
            f"L2L did not return valid JSON (HTTP {response.status_code}) "
            f"for {path}: {response.text[:200]}"
        ) from exc

    if reply.get("success") is not True:
        raise L2LAPIError(reply.get("error", f"L2L reported the request failed for {path}."))

    return reply


async def list_all(path: str, fields: str, extra_params: Optional[dict] = None) -> list[dict]:
    """Paginate through one of L2L's generic, read-only master-data record
    areas (e.g. sites/, lines/) using the limit/offset pattern documented
    under "HTTP GET - View Records". Used by backend/directory.py to
    resolve real site/line values instead of hardcoding guesses."""
    out: list[dict] = []
    offset = 0
    limit = 200
    while True:
        params = {"limit": limit, "offset": offset, "fields": fields}
        if extra_params:
            params.update(extra_params)
        reply = await get_l2l_data(path, params)
        page = reply.get("data") or []
        out.extend(page)
        if len(page) < limit:
            break
        offset += limit
    return out


async def list_sites() -> list[dict]:
    return await list_all("sites", "id,site,description,active")


async def list_lines() -> list[dict]:
    return await list_all("lines", "id,code,description,externalid,site,area,areacode,active")


def _cache_ttl_for(period_end: date) -> int:
    """Historical (fully elapsed) periods are cached much longer than a
    period that includes today, since only the latter is still changing."""
    if period_end >= date.today():
        return config.CACHE_TTL_CURRENT_SECONDS
    return config.CACHE_TTL_HISTORICAL_SECONDS


async def weekly_summary(day: date, linecode: str, site: int) -> list[dict]:
    """One row (list of len 0 or 1) for the L2L week containing `day`.

    `site` is the real L2L site code resolved by backend/directory.py
    (or the L2L_SITE_NUMBER override) -- passed in explicitly rather than
    read from config directly, since config no longer holds a single
    trusted site number by itself (see config.py's SITE_HINTS docstring)."""
    cache_key = f"week:{site}:{linecode}:{day.isoformat()}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    path = "reporting/production/weekly_summary_data_by_line"
    # Per L2L's API docs, this endpoint takes a single "date" (any date
    # falling within the target production week) -- NOT a start/end range.
    # That's different from the daily endpoint just below, which does
    # require start/end. Easy to conflate the two; the docs spell out both
    # explicitly under "Reporting Method: Production: Weekly/Daily Summary
    # Data by Line".
    params = {
        "site": site,
        "date": day.strftime("%Y-%m-%d %H:%M"),
        "linecode": linecode,
    }
    reply = await get_l2l_data(path, params)
    data = reply.get("data") or []
    # The prototype script showed this endpoint wrapping its single row in
    # an extra list layer (a list containing a list containing the dict);
    # normalize either shape to a flat list of row dicts.
    rows = []
    for item in data:
        if isinstance(item, list):
            rows.extend(item)
        else:
            rows.append(item)

    # Per the real API docs' example payload, the weekly record has no
    # "end_date" field to read back (an earlier version of this function
    # assumed one, which meant this always fell through to date.today() and
    # never got the long historical-cache TTL below). The week always runs
    # 7 days from its start, so just compute it instead of guessing at a
    # field that isn't there.
    period_end = day + timedelta(days=6)
    _cache.set(cache_key, rows, _cache_ttl_for(period_end))
    return rows


async def daily_summary(day: date, linecode: str, site: int) -> list[dict]:
    """All rows L2L returns for this line on this day (may be more than one
    -- e.g. split by shift/product -- see metrics.aggregate_records).

    See weekly_summary()'s docstring for why `site` is passed in rather
    than read from config directly."""
    cache_key = f"day:{site}:{linecode}:{day.isoformat()}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    path = "reporting/production/daily_summary_data_by_line"
    period_end_exclusive = day + timedelta(days=1)
    params = {
        "site": site,
        "start": day.strftime("%Y-%m-%d %H:%M"),
        "end": period_end_exclusive.strftime("%Y-%m-%d %H:%M"),
        "linecode": linecode,
    }
    reply = await get_l2l_data(path, params)
    data = reply.get("data") or []
    rows = data if isinstance(data, list) else [data]
    # Same defensive flattening as weekly_summary, in case a nested list
    # shape shows up here too.
    flat = []
    for item in rows:
        if isinstance(item, list):
            flat.extend(item)
        else:
            flat.append(item)

    _cache.set(cache_key, flat, _cache_ttl_for(day))
    return flat
