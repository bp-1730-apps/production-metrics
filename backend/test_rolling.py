"""
Tests for the incremental rolling-window merge logic in backend/rolling.py
-- the piece that keeps scheduled-refresh L2L call volume small by only
re-fetching new/recent periods instead of the whole window every run.
"""
import os
from datetime import date, timedelta

os.environ.setdefault("L2L_API_KEY", "test-key-not-real")

import pytest  # noqa: E402

from backend import l2l_client, rolling, trending  # noqa: E402

LINE_PAIRS = [("BP-LINE1", "2A"), ("BP-LINE2", "PXM6")]


def make_row(period_start: date, value: float):
    return {
        "period_start": period_start.isoformat(),
        "period_end": period_start.isoformat(),
        "metrics": {"oee": value, "actual": value * 100},
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_first_run_fetches_every_period(monkeypatch):
    calls = []

    async def fake_fetch_one_period(linecode, period_start, granularity, site):
        calls.append((linecode, period_start))
        return make_row(period_start, 50.0)

    monkeypatch.setattr(trending, "_fetch_one_period", fake_fetch_one_period)

    today = date(2026, 9, 17)
    result = await rolling.update_rolling_series(
        existing=None, line_pairs=LINE_PAIRS, granularity="day",
        window_days=5, tail_periods=1, site=1730,
    )
    # window_days=5 -> 6 periods (inclusive stepping), x2 lines
    assert len(result["series"]["BP-LINE1"]) == 6
    assert len(calls) == 12
    assert "ALL_LINES" in result["series"]
    assert result["series"]["ALL_LINES"][-1]["metrics"]["oee"] == pytest.approx(50.0)


@pytest.mark.anyio
async def test_second_run_only_refetches_new_and_tail_periods(monkeypatch):
    existing = {
        "start_date": "2026-09-12",
        "series": {
            "BP-LINE1": [make_row(date(2026, 9, 12) + timedelta(days=i), 10.0 + i) for i in range(6)],
            "BP-LINE2": [make_row(date(2026, 9, 12) + timedelta(days=i), 20.0 + i) for i in range(6)],
        },
    }

    calls = []

    async def fake_fetch_one_period(linecode, period_start, granularity, site):
        calls.append((linecode, period_start))
        return make_row(period_start, 999.0)

    monkeypatch.setattr(trending, "_fetch_one_period", fake_fetch_one_period)

    # "Today" has advanced by 1 day relative to the existing data's last
    # period (2026-09-17) to 2026-09-18 -- exactly one new period.
    import backend.rolling as rolling_mod

    class FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 18)

    monkeypatch.setattr(rolling_mod, "date", FixedDate)

    result = await rolling.update_rolling_series(
        existing=existing, line_pairs=LINE_PAIRS, granularity="day",
        window_days=5, tail_periods=1, site=1730,
    )

    # Only the new period (09-18) + the 1-period tail should have been
    # fetched, per line -- not the whole 6-period window again.
    fetched_dates = sorted(d for _, d in calls)
    assert fetched_dates == [date(2026, 9, 18)] * 1 or len(calls) <= 2 * 2  # at most new+tail per line
    assert len(result["series"]["BP-LINE1"]) == 6  # window size unchanged
    # Oldest period (09-12) should have dropped off, newest (09-18) present
    starts = [row["period_start"] for row in result["series"]["BP-LINE1"]]
    assert "2026-09-12" not in starts
    assert "2026-09-18" in starts


@pytest.mark.anyio
async def test_failed_fetch_with_no_prior_data_yields_placeholder(monkeypatch):
    async def failing_fetch(linecode, period_start, granularity, site):
        raise RuntimeError("L2L unreachable")

    monkeypatch.setattr(trending, "_fetch_one_period", failing_fetch)

    result = await rolling.update_rolling_series(
        existing=None, line_pairs=[("BP-LINE1", "2A")], granularity="day",
        window_days=2, tail_periods=1, site=1730,
    )
    rows = result["series"]["BP-LINE1"]
    assert all(row["metrics"] == {} for row in rows)
    assert len(result["errors"]["BP-LINE1"]) == len(rows)


@pytest.mark.anyio
async def test_existing_row_reused_without_refetch_outside_tail(monkeypatch):
    existing = {
        "start_date": "2026-09-10",
        "series": {
            "BP-LINE1": [make_row(date(2026, 9, 10) + timedelta(days=i), 42.0) for i in range(8)],
        },
    }

    call_count = {"n": 0}

    async def fake_fetch_one_period(linecode, period_start, granularity, site):
        call_count["n"] += 1
        return make_row(period_start, 1.0)

    monkeypatch.setattr(trending, "_fetch_one_period", fake_fetch_one_period)

    import backend.rolling as rolling_mod

    class FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 17)  # same as existing's last period -- nothing new

    monkeypatch.setattr(rolling_mod, "date", FixedDate)

    result = await rolling.update_rolling_series(
        existing=existing, line_pairs=[("BP-LINE1", "2A")], granularity="day",
        window_days=7, tail_periods=1, site=1730,
    )
    # Only the 1 tail period should be refetched; the rest reused verbatim.
    assert call_count["n"] == 1
    non_tail_values = [row["metrics"]["oee"] for row in result["series"]["BP-LINE1"][:-1]]
    assert all(v == 42.0 for v in non_tail_values)


def make_empty_row(period_start: date):
    return {"period_start": period_start.isoformat(), "period_end": period_start.isoformat(), "metrics": {}}


@pytest.mark.anyio
async def test_periods_with_empty_metrics_are_retried_every_run(monkeypatch):
    """A period saved with no metrics (e.g. from a prior bug or a transient
    L2L failure) must keep being retried on later runs, not get frozen as
    permanently empty just because it already exists in the saved window."""
    existing = {
        "start_date": "2026-09-10",
        "series": {
            # All 8 periods previously failed and were saved as empty.
            "BP-LINE1": [make_empty_row(date(2026, 9, 10) + timedelta(days=i)) for i in range(8)],
        },
    }

    call_count = {"n": 0}

    async def fake_fetch_one_period(linecode, period_start, granularity, site):
        call_count["n"] += 1
        return make_row(period_start, 7.0)  # now succeeds with real data

    monkeypatch.setattr(trending, "_fetch_one_period", fake_fetch_one_period)

    import backend.rolling as rolling_mod

    class FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 17)  # same as existing's last period -- nothing "new"

    monkeypatch.setattr(rolling_mod, "date", FixedDate)

    result = await rolling.update_rolling_series(
        existing=existing, line_pairs=[("BP-LINE1", "2A")], granularity="day",
        window_days=7, tail_periods=1, site=1730,
    )
    # Every previously-empty period should have been retried, not just the tail.
    assert call_count["n"] == 8
    assert all(row["metrics"]["oee"] == 7.0 for row in result["series"]["BP-LINE1"])
