#!/usr/bin/env python3
"""
Standalone diagnostic: prints your L2L account's real sites and lines, and
what backend/directory.py's hint-matching (config.py's SITE_HINTS/
LINE_HINTS) currently resolves them to.

This used to have its own duplicate copy of the "read L2L_BASE_URL/
L2L_API_KEY from the environment" logic, which had the same empty-string-
secret bug that config.py fixes elsewhere in this project: a GitHub Actions
secret referenced in a workflow's `env:` block that doesn't exist evaluates
to an empty string, not "unset" -- `os.environ.get(name, default)` treats
that as present and returns "" instead of the intended default, which is
exactly what silently broke this script (empty base URL -> a relative
request URL -> a low-level connection error with no obvious explanation in
a quick glance at the Actions summary page). Importing backend.config
instead of re-implementing that logic here means this script can no longer
have its own, separate version of that bug.

As of this version, this script is a convenience/redundant check --
scripts/refresh_data.py (the one the scheduled GitHub Action actually
runs) does this same resolution on every run and prints it to that run's
own log, and writes any resolution failures into data/lines.json and
data/trending-*.json's `errors` field. Run this file only if you want to
look at the raw account data without touching those files.

Run locally:

    cd l2l-trending-dashboard
    export L2L_API_KEY=your-real-key
    python3 scripts/discover_lines.py

Or via the "Discover real L2L site/line codes" GitHub Action (Actions tab
-> that workflow -> Run workflow), which sets L2L_API_KEY from the same
repository secret refresh-data.yml already uses.
"""
import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main():
    # Imported inside main() (after the path insert above, and inside a
    # try/except at the bottom of this file) rather than at module level,
    # so that a missing/misconfigured L2L_API_KEY -- which backend.config
    # raises on at import time -- produces a clear, captioned error instead
    # of a bare traceback pointing at an import statement.
    from backend import config, directory, l2l_client  # noqa: E402

    masked_key = (config.L2L_API_KEY[:4] + "..." + config.L2L_API_KEY[-2:]) if len(config.L2L_API_KEY) > 8 else "(short key, not shown)"
    print(f"Base URL   : {config.L2L_BASE_URL}")
    print(f"API key    : {masked_key}")
    print(f"Site hints : {config.SITE_HINTS}")
    if config.L2L_SITE_NUMBER_OVERRIDE is not None:
        print(f"Site override active: L2L_SITE_NUMBER={config.L2L_SITE_NUMBER_OVERRIDE} (site hints above are ignored)")
    print()

    print("=" * 78)
    print("SITES this API key can see")
    print("=" * 78)
    sites = await l2l_client.list_sites()
    if not sites:
        print("  (none returned -- double check the API key and L2L_BASE_URL)")
    for s in sites:
        print(f"  site={s.get('site')!r:>6}  id={s.get('id')!r:>6}  "
              f"active={s.get('active')!r:<6}  description={s.get('description')!r}")

    print()
    print("=" * 78)
    print("LINES this API key can see")
    print("=" * 78)
    lines = await l2l_client.list_lines()
    if not lines:
        print("  (none returned -- double check the API key and L2L_BASE_URL)")
    for l in lines:
        print(f"  code={l.get('code')!r:<14} site={l.get('site')!r:>6}  id={l.get('id')!r:>6}  "
              f"active={l.get('active')!r:<6}  area={(l.get('areacode') or l.get('area'))!r:<12}  "
              f"description={l.get('description')!r}")

    print()
    print("=" * 78)
    print("What backend/directory.py currently resolves config.py's hints to")
    print("=" * 78)
    site = await directory.resolve_site()
    if site.error:
        print(f"  SITE: {site.error}")
    else:
        print(f"  site -> {site.site_code}  (matched description: {site.matched_description!r})")

    resolutions = await directory.resolve_lines(site)
    for display_code, res in resolutions.items():
        if res.l2l_code:
            print(f"  {display_code} -> code={res.l2l_code!r}  (hint {res.hint!r} matched {res.matched_description!r})")
        else:
            print(f"  {display_code}: {res.error}")

    print()
    print("To fix an unresolved line above, edit config.py's LINE_HINTS for that")
    print("display code to a hint that matches one of the real lines listed above")
    print("(its code, description, externalid, or areacode). Same idea for SITE_HINTS")
    print("if the site itself didn't resolve.")

    await l2l_client.close_client()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print("\nFAILED with an unhandled exception -- full traceback below:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
