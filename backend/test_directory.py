"""
Tests for backend/directory.py -- the site/line hint-matching logic that
replaced a hardcoded LINECODE_DICT (see config.py's SITE_HINTS/LINE_HINTS
docstring for the full story of why). These are the cases that matter
most: a hint that's already the real value, a hint that only matches a
description, a hint that matches nothing (and what the resulting error
looks like), and the explicit site-number override.
"""
import os

os.environ.setdefault("L2L_API_KEY", "test-key-not-real")

import pytest  # noqa: E402

from backend import config, directory, l2l_client  # noqa: E402

SITES = [
    {"site": 1730, "id": 3, "description": "Novus Foods - Plant 1730 (Buena Park)", "active": True},
    {"site": 99, "id": 9, "description": "Sandbox", "active": True},
]

LINES = [
    {"code": "2A", "description": "Line 1 - Salsa", "externalid": None, "areacode": "SALSA", "site": 1730, "active": True},
    {"code": "PXM6", "description": "Line 2 - Layered", "externalid": "ext-2", "areacode": "LAYER", "site": 1730, "active": True},
    {"code": "OTHER-SITE-LINE", "description": "Unrelated line at another site", "externalid": None, "areacode": None, "site": 99, "active": True},
]


@pytest.fixture(autouse=True)
def restore_config():
    """config.SITE_HINTS/LINE_HINTS/L2L_SITE_NUMBER_OVERRIDE are module-level
    values tests below mutate directly; put them back afterwards so tests
    in other files aren't affected by test ordering."""
    site_hints, line_hints, override = config.SITE_HINTS, dict(config.LINE_HINTS), config.L2L_SITE_NUMBER_OVERRIDE
    yield
    config.SITE_HINTS, config.LINE_HINTS, config.L2L_SITE_NUMBER_OVERRIDE = site_hints, line_hints, override


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_resolve_site_matches_by_description_hint(monkeypatch):
    monkeypatch.setattr(l2l_client, "list_sites", lambda: _async_return(SITES))
    config.SITE_HINTS = ["1730"]

    result = await directory.resolve_site()

    assert result.site_code == 1730
    assert result.error is None
    assert len(result.candidates) == 2  # every site seen is kept for diagnostics


@pytest.mark.anyio
async def test_resolve_site_no_match_lists_real_candidates_in_error(monkeypatch):
    monkeypatch.setattr(l2l_client, "list_sites", lambda: _async_return(SITES))
    config.SITE_HINTS = ["nonexistent-plant-name"]

    result = await directory.resolve_site()

    assert result.site_code is None
    assert "nonexistent-plant-name" in result.error
    # every real site's description should be visible in the error so it's
    # self-diagnosing without a separate script
    assert "Novus Foods - Plant 1730 (Buena Park)" in result.error
    assert "Sandbox" in result.error


@pytest.mark.anyio
async def test_resolve_site_override_skips_lookup_entirely(monkeypatch):
    def _boom():
        raise AssertionError("list_sites should not be called when L2L_SITE_NUMBER_OVERRIDE is set")

    monkeypatch.setattr(l2l_client, "list_sites", _boom)
    config.L2L_SITE_NUMBER_OVERRIDE = 4242

    result = await directory.resolve_site()

    assert result.site_code == 4242
    assert result.error is None


@pytest.mark.anyio
async def test_resolve_lines_exact_code_match(monkeypatch):
    monkeypatch.setattr(l2l_client, "list_lines", lambda: _async_return(LINES))
    config.LINE_HINTS = {"BP-LINE1": "2A"}
    site = directory.SiteResolution(site_code=1730)

    result = await directory.resolve_lines(site)

    assert result["BP-LINE1"].l2l_code == "2A"
    assert result["BP-LINE1"].error is None


@pytest.mark.anyio
async def test_resolve_lines_fuzzy_description_match(monkeypatch):
    monkeypatch.setattr(l2l_client, "list_lines", lambda: _async_return(LINES))
    # "Layered" isn't a real code, but it IS a substring of that line's
    # description -- this is the case a plain hardcoded LINECODE_DICT could
    # never handle.
    config.LINE_HINTS = {"BP-LINE2": "Layered"}
    site = directory.SiteResolution(site_code=1730)

    result = await directory.resolve_lines(site)

    assert result["BP-LINE2"].l2l_code == "PXM6"


@pytest.mark.anyio
async def test_resolve_lines_unmatched_hint_reports_real_candidates(monkeypatch):
    monkeypatch.setattr(l2l_client, "list_lines", lambda: _async_return(LINES))
    config.LINE_HINTS = {"BP-LINE3": "totally-wrong-hint"}
    site = directory.SiteResolution(site_code=1730)

    result = await directory.resolve_lines(site)

    res = result["BP-LINE3"]
    assert res.l2l_code is None
    assert "totally-wrong-hint" in res.error
    # the real lines at this site should be named in the error
    assert "2A" in res.error
    assert "PXM6" in res.error
    # the unrelated line at a *different* site should not be offered as a
    # candidate once site-scoping successfully narrowed the pool
    assert "OTHER-SITE-LINE" not in res.error


@pytest.mark.anyio
async def test_resolve_lines_falls_back_to_all_lines_when_site_scope_is_empty(monkeypatch):
    monkeypatch.setattr(l2l_client, "list_lines", lambda: _async_return(LINES))
    config.LINE_HINTS = {"BP-LINE_X": "OTHER-SITE-LINE"}
    # A site code that matches none of LINES' "site" values -- scoping finds
    # nothing, so resolution should fall back to searching every line
    # rather than reporting zero candidates.
    site = directory.SiteResolution(site_code=555555)

    result = await directory.resolve_lines(site)

    assert result["BP-LINE_X"].l2l_code == "OTHER-SITE-LINE"


async def _async_return(value):
    return value
