"""
Exercises the API end-to-end against the two sample payloads the user
provided, without making any real network calls. Run with:

    pytest backend/test_api.py -v
"""

import copy
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import os
os.environ.setdefault("L2L_API_KEY", "test-key-not-real")

from backend import directory, l2l_client, metrics, trending  # noqa: E402
from backend import main as main_module  # noqa: E402
from backend.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture
def mock_resolution(monkeypatch):
    """Bypass backend.directory's real L2L /sites//lines/ lookups for tests
    that exercise the FastAPI app end-to-end -- these tests shouldn't
    depend on real network access or a real API key, and should see a
    fixed, known line mapping regardless of what any real L2L account
    currently contains."""
    mapping = {
        "BP-LINE1": "2A", "BP-LINE2": "PXM6", "BP-LINE3": "MP4",
        "BP-LINE4": "PL1", "BP-LINE5": "PL2", "BP-LINE6": "2E",
    }

    async def fake_resolve_site():
        return directory.SiteResolution(site_code=1730, matched_description="Plant 1730 - Buena Park (test)")

    async def fake_resolve_lines(site):
        return {
            dc: directory.LineResolution(dc, code, code, matched_description=f"{dc} (test)")
            for dc, code in mapping.items()
        }

    monkeypatch.setattr(directory, "resolve_site", fake_resolve_site)
    monkeypatch.setattr(directory, "resolve_lines", fake_resolve_lines)
    main_module._cached_line_resolutions = None  # force re-resolution through the fakes above
    yield mapping
    main_module._cached_line_resolutions = None  # don't leak a mocked resolution into later tests

WEEKLY_SAMPLE = {
    "area_id": 251, "line_id": 2642, "line_categories": None,
    "demand": "0", "average_demand": "0E-20", "actual": "49593", "scrap": "1",
    "reject_percent": "0", "planned_production_minutes": "6750.00",
    "downtime_minutes": "0.00", "operator_count": "0.00", "average_oee": "59",
    "operational_availability": "100", "peff": None, "theoretical_parts": "77545",
    "theoretical_parts_production_time": "77545", "scrap_percent": "0",
    "lmpu": "0", "lhpu": "0", "plmpu": "0", "run_rate": "8", "past_due": "49593",
    "yield": "100", "ppmh": None, "pph": "441", "teff": "59", "ppp": "58.995",
    "tpu": "0", "npm": "0", "earned_hours": "0.00", "labor_efficiency": "0",
    "labor_efficiency_ideal_cycle_time": "60", "labor_efficiency_standard_time": "0",
    "pitch_pace": "0", "pitch_count": 116,
    "start_date": "2026-09-07T06:00:00", "end_date": "2026-09-14T06:00:00",
}

DAILY_SAMPLE = {
    "actual": "3903.00", "area": "Area 1", "area_id": 1, "changeover_actual": "0",
    "changeover_duration": "0", "demand": "3709.00", "downtime_minutes": "50.00",
    "earned_hours": "8", "labor_efficiency": "105", "line": "line-a", "line_id": 16,
    "line_categories": [1], "lhpu": "0", "lmpu": "0", "plmpu": "0",
    "nonproduction_minutes": "0.00", "npm": "103", "operational_availability": "90",
    "operator_count": "2.04375", "overall_equipment_effectiveness": "89",
    "past_due": "194.00", "peff": "105", "pitch_count": 8,
    "planned_production_minutes": "480.00", "theoretical_parts": "946.6666666666667",
    "theoretical_parts_production_time": "946.6666666666667", "pph": "488",
    "ppmh": "239", "ppp": "100", "product": "Widget A", "product_id": 1,
    "product_category_id": 35, "product_category_code": "Category 2", "products": 1,
    "scrap": "4.00", "scrap_percent": "0", "shift": "Day Shift", "shift_id": 1,
    "shift_start": "2021-01-04T07:00:00", "shifts": 1, "site": 23,
    "start_date": "2021-01-11T23:00:00", "end_date": "2021-01-12T23:00:00",
    "run_rate": "7", "takt_time": "8", "teff": "89", "tpu": "0.225", "yield": "100",
    "pitch_pace": "1",
}


def make_daily_variant(overrides):
    row = copy.deepcopy(DAILY_SAMPLE)
    row.update(overrides)
    return row


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def clear_cache():
    l2l_client._cache._store.clear()
    yield
    l2l_client._cache._store.clear()


def test_metrics_normalization_unifies_oee_field_name():
    weekly_norm = metrics.normalize_record(WEEKLY_SAMPLE)
    daily_norm = metrics.normalize_record(DAILY_SAMPLE)
    assert weekly_norm["oee"] == "59"
    assert daily_norm["oee"] == "89"
    assert "average_oee" not in weekly_norm
    assert "overall_equipment_effectiveness" not in daily_norm
    # identifier fields must be stripped
    assert "line_id" not in daily_norm
    assert "product" not in daily_norm


def test_aggregate_records_sums_counts_and_weight_averages_rates():
    shift1 = metrics.normalize_record(make_daily_variant({
        "actual": "1000", "planned_production_minutes": "480", "oee_placeholder": None
    }))
    shift2 = metrics.normalize_record(make_daily_variant({
        "actual": "2000", "planned_production_minutes": "480", "overall_equipment_effectiveness": "50"
    }))
    combined = metrics.aggregate_records([shift1, shift2])
    assert combined["actual"] == 3000.0  # summed
    # oee: shift1=89 (default), shift2=50, equal weights -> average of 89 and 50
    assert combined["oee"] == pytest.approx((89 + 50) / 2)


@pytest.mark.anyio
async def test_weekly_summary_client(monkeypatch):
    async def fake_get_l2l_data(path, params):
        assert path == "reporting/production/weekly_summary_data_by_line"
        return {"success": True, "data": [WEEKLY_SAMPLE]}

    monkeypatch.setattr(l2l_client, "get_l2l_data", fake_get_l2l_data)
    rows = await l2l_client.weekly_summary(date(2026, 9, 7), "2A", 1730)
    assert len(rows) == 1
    assert rows[0]["average_oee"] == "59"


@pytest.mark.anyio
async def test_daily_summary_client_handles_multiple_rows(monkeypatch):
    async def fake_get_l2l_data(path, params):
        assert path == "reporting/production/daily_summary_data_by_line"
        return {"success": True, "data": [DAILY_SAMPLE, make_daily_variant({"shift": "Night Shift"})]}

    monkeypatch.setattr(l2l_client, "get_l2l_data", fake_get_l2l_data)
    rows = await l2l_client.daily_summary(date(2026, 9, 7), "2E", 1730)
    assert len(rows) == 2


@pytest.mark.anyio
async def test_fetch_trending_multi_line_and_all_lines(monkeypatch):
    async def fake_weekly(day, linecode, site):
        return [WEEKLY_SAMPLE]

    monkeypatch.setattr(l2l_client, "weekly_summary", fake_weekly)
    start = date(2026, 8, 24)
    end = date(2026, 9, 7)
    result = await trending.fetch_trending(["2A", "2E"], start, end, "week", 1730)
    assert result["lines"] == ["2A", "2E"]
    assert len(result["series"]["2A"]) == 3  # 3 weekly periods in the range
    assert "ALL_LINES" in result["series"]
    assert result["series"]["ALL_LINES"][0]["metrics"]["actual"] == pytest.approx(49593 * 2)


def test_l2l_error_surfaces_as_400_or_500(monkeypatch, mock_resolution):
    async def failing_get_l2l_data(path, params):
        return {"success": False, "error": "auth failed"}

    with patch.object(l2l_client, "get_l2l_data", AsyncMock(side_effect=lambda p, params: failing_get_l2l_data(p, params))):
        resp = client.get("/api/trending", params={
            "start_date": "2026-09-01", "end_date": "2026-09-07", "granularity": "week",
        })
        # Errors are captured per-line/period, not raised -- request should
        # still succeed with an "errors" section instead of a 500.
        assert resp.status_code == 200
        body = resp.json()
        assert any(body["errors"].values())


def test_lines_and_metrics_endpoints(mock_resolution):
    resp = client.get("/api/lines")
    assert resp.status_code == 200
    body = resp.json()
    codes = {row["code"] for row in body}
    assert codes == {"BP-LINE1", "BP-LINE2", "BP-LINE3", "BP-LINE4", "BP-LINE5", "BP-LINE6"}
    assert all(row["resolved"] for row in body)

    resp = client.get("/api/metrics", params={"granularity": "day"})
    assert resp.status_code == 200
    metric_ids = {m["id"] for m in resp.json()}
    assert "oee" in metric_ids
    assert "average_demand" not in metric_ids  # week-only metric

    resp = client.get("/api/metrics", params={"granularity": "week"})
    metric_ids = {m["id"] for m in resp.json()}
    assert "average_demand" in metric_ids
    assert "takt_time" not in metric_ids  # day-only metric


@pytest.fixture
def anyio_backend():
    return "asyncio"
