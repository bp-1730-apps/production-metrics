"""
Resolves the human-friendly line codes this project uses (BP-LINE1..6) to
whatever real "linecode" strings actually exist in your L2L account, by
querying L2L's own /api/1.0/sites/ and /api/1.0/lines/ record areas --
documented, read-only, paginated master-data endpoints -- instead of
hand-copying values into config.py and hoping they're still right.

Why this exists: config.py used to hardcode a LINECODE_DICT with
real-looking values that turned out not to match this account. L2L's
reporting endpoints hide that kind of mistake badly: an unmatched linecode
is a hard error on the weekly endpoint ("No lines found"), but is silently
*ignored* on the daily endpoint, which then falls back to unfiltered,
whole-plant totals for every line you asked for -- indistinguishable from
"every line reports identical numbers" unless you already know to suspect
the linecode itself.

This module removes the guessing: every call here returns not just what
matched, but the full list of real site/line records it saw, so a wrong
hint is self-diagnosing straight from data/trending-*.json's `errors`
field (and scripts/refresh_data.py's own stdout) -- no separate diagnostic
script or manual API poking required.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Optional

try:
    from . import config, l2l_client
except ImportError:  # running as `uvicorn main:app` from inside backend/
    import config
    import l2l_client


@dataclass
class SiteResolution:
    site_code: Optional[int]
    matched_description: Optional[str] = None
    candidates: list = dc_field(default_factory=list)  # every site this key can see, for diagnostics
    error: Optional[str] = None


@dataclass
class LineResolution:
    display_code: str
    hint: str
    l2l_code: Optional[str]
    matched_description: Optional[str] = None
    error: Optional[str] = None


def _matches(hint: str, *fields: Optional[str]) -> bool:
    hint_l = hint.strip().lower()
    if not hint_l:
        return False
    for f in fields:
        if f and hint_l in str(f).strip().lower():
            return True
    return False


async def resolve_site() -> SiteResolution:
    """Find the real L2L "site" code for this plant, matching SITE_HINTS
    against each site's description -- unless L2L_SITE_NUMBER_OVERRIDE is
    set, in which case that value is used directly and no API call is made."""
    if config.L2L_SITE_NUMBER_OVERRIDE is not None:
        return SiteResolution(
            site_code=config.L2L_SITE_NUMBER_OVERRIDE,
            matched_description="(explicit L2L_SITE_NUMBER override, no matching performed)",
        )

    sites = await l2l_client.list_sites()
    candidates = [
        {"site": s.get("site"), "id": s.get("id"), "description": s.get("description"), "active": s.get("active")}
        for s in sites
    ]

    for hint in config.SITE_HINTS:
        for s in sites:
            if _matches(hint, s.get("description")):
                return SiteResolution(
                    site_code=s.get("site"),
                    matched_description=s.get("description"),
                    candidates=candidates,
                )

    candidate_text = "; ".join(f"site={c['site']} desc={c['description']!r}" for c in candidates) or "(none returned)"
    return SiteResolution(
        site_code=None,
        candidates=candidates,
        error=(
            f"No site description matched any of {config.SITE_HINTS!r} (config.py's SITE_HINTS). "
            f"Real sites this API key can see: {candidate_text}. Either add a hint that matches one "
            f"of those descriptions, or set the L2L_SITE_NUMBER GitHub secret to the correct site "
            f"code directly to skip this matching."
        ),
    )


async def resolve_lines(site: SiteResolution) -> dict[str, LineResolution]:
    """Resolve every configured LINE_HINTS entry to a real L2L line `code`."""
    lines = await l2l_client.list_lines()
    candidates_all = [
        {
            "code": l.get("code"),
            "description": l.get("description"),
            "externalid": l.get("externalid"),
            "areacode": l.get("areacode"),
            "site": l.get("site"),
            "active": l.get("active"),
        }
        for l in lines
    ]

    # Prefer lines that look like they belong to the resolved site. The API
    # docs don't spell out whether a Line's "site" field serializes as the
    # Site's internal id or its "site" code when read back from /lines/, so
    # try matching either one -- and if that scoping finds nothing at all
    # (e.g. this account only has one site, or the representation doesn't
    # match), fall back to searching every line rather than returning
    # nothing just because the optional scoping step didn't pan out.
    scoped = []
    if site.site_code is not None:
        scoped = [c for c in candidates_all if c["site"] == site.site_code]
    pool = scoped or candidates_all
    pool_is_scoped = bool(scoped)

    def candidate_text(items: list[dict]) -> str:
        return "; ".join(f"code={c['code']!r} desc={c['description']!r}" for c in items) or "(none returned)"

    out: dict[str, LineResolution] = {}
    for display_code, hint_field in config.LINE_HINTS.items():
        hints = [h.strip() for h in hint_field.split(",") if h.strip()]
        found = None
        for hint in hints:
            # Exact, case-insensitive match against the real `code` field
            # first -- if the hint already *is* the real code, this matches
            # immediately, same as the old hardcoded dict did when it was
            # actually right.
            exact = next(
                (c for c in pool if c["code"] and c["code"].strip().lower() == hint.strip().lower()), None
            )
            if exact:
                found = (exact, hint)
                break
            fuzzy = next(
                (c for c in pool if _matches(hint, c["description"], c["externalid"], c["areacode"])), None
            )
            if fuzzy:
                found = (fuzzy, hint)
                break
        if found:
            match, hint = found
            out[display_code] = LineResolution(display_code, hint, match["code"], match["description"])
        else:
            scope_note = f"for site {site.site_code}" if pool_is_scoped else "(site scoping unavailable -- showing every line this key can see)"
            out[display_code] = LineResolution(
                display_code,
                hint_field,
                None,
                error=(
                    f"No line matched hint(s) {hints!r} for {display_code} (config.py's LINE_HINTS). "
                    f"Real lines {scope_note}: {candidate_text(pool)}."
                ),
            )
    return out
