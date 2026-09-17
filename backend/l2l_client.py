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
from datetime import date, datetime, timedelta
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


def _cache_ttl_for(period_end: date) -> int:
    """Historical (fully elapsed) periods are cached much longer than a
    period that includes today, since only the latter is still changing."""
    if period_end >= date.today():
        return config.CACHE_TTL_CURRENT_SECONDS
    return config.CACHE_TTL_HISTORICAL_SECONDS


async def weekly_summary(day: date, linecode: str) -> list[dict]:
    """One row (list of len 0 or 1) for the L2L week containing `day`."""
    cache_key = f"week:{linecode}:{day.isoformat()}"
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
        "site": config.L2L_SITE_NUMBER,
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

    period_end = date.today()
    if rows and rows[0].get("end_date"):
        try:
            period_end = datetime.fromisoformat(rows[0]["end_date"]).date()
        except ValueError:
            pass

    _cache.set(cache_key, rows, _cache_ttl_for(period_end))
    return rows


async def daily_summary(day: date, linecode: str) -> list[dict]:
    """All rows L2L returns for this line on this day (may be more than one
    -- e.g. split by shift/product -- see metrics.aggregate_records)."""
    cache_key = f"day:{linecode}:{day.isoformat()}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    path = "reporting/production/daily_summary_data_by_line"
    period_end_exclusive = day + timedelta(days=1)
    params = {
        "site": config.L2L_SITE_NUMBER,
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
