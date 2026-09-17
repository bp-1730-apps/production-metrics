#!/usr/bin/env python3
"""
One-off diagnostic: prints your L2L account's REAL site codes and line
codes, straight from L2L's own master-data endpoints -- no more guessing
values by hand in the L2L web UI.

Why this exists: the values in backend/config.py's LINECODE_DICT (and the
L2L_SITE_NUMBER default) were assumed from an earlier draft of this
project's original script and turned out not to match this account's
actual site/line records, which silently breaks trending data (the
reporting endpoints return "No lines found" or, worse, quietly fall back
to unfiltered plant-wide totals for every line -- see README's
"Known gaps" section).

Run it locally with your real key:

    cd l2l-trending-dashboard
    export L2L_API_KEY=your-real-key
    export L2L_BASE_URL=https://lakeviewfarms.leading2lean.com/api/1.0   # or your real base URL
    python3 scripts/discover_lines.py

It prints every Site record your key can see (its real "site" code --
this is what backend/config.py's L2L_SITE_NUMBER should be set to) and
every Line record (its real "code" field -- this is what
backend/config.py's LINECODE_DICT values should be set to), so you can
match "Plant 1730" / "BP-LINE1..6" to the correct real values and update
config.py accordingly.
"""
import asyncio
import os
import sys

import httpx

BASE_URL = os.environ.get("L2L_BASE_URL", "https://lakeviewfarms.leading2lean.com/api/1.0").rstrip("/")
API_KEY = os.environ.get("L2L_API_KEY")

if not API_KEY:
    print("Set L2L_API_KEY (and optionally L2L_BASE_URL) before running this script.", file=sys.stderr)
    sys.exit(1)


async def list_all(client: httpx.AsyncClient, path: str, fields: str) -> list[dict]:
    """Paginate through a generic L2L list endpoint (limit/offset), per the
    API docs' standard pagination pattern."""
    out = []
    offset = 0
    limit = 200
    while True:
        resp = await client.get(
            f"{BASE_URL}/{path}/",
            params={"auth": API_KEY, "limit": limit, "offset": offset, "fields": fields},
        )
        resp.raise_for_status()
        reply = resp.json()
        if reply.get("success") is not True:
            print(f"L2L reported an error for {path}: {reply.get('error')}", file=sys.stderr)
            break
        page = reply.get("data") or []
        out.extend(page)
        if len(page) < limit:
            break
        offset += limit
    return out


async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"Base URL: {BASE_URL}\n")

        print("=" * 70)
        print("SITES  (the \"site\" field is what L2L_SITE_NUMBER should be)")
        print("=" * 70)
        sites = await list_all(client, "sites", "id,site,description,active")
        if not sites:
            print("  (none returned -- check your API key and base URL)")
        for s in sites:
            print(f"  site={s.get('site')!r:>6}  id={s.get('id')!r:>6}  "
                  f"active={s.get('active')!r:<6}  description={s.get('description')!r}")

        print()
        print("=" * 70)
        print("LINES  (the \"code\" field is what LINECODE_DICT values should be)")
        print("=" * 70)
        lines = await list_all(client, "lines", "id,code,description,site,area,areacode,active")
        if not lines:
            print("  (none returned -- check your API key and base URL)")
        for l in lines:
            print(f"  code={l.get('code')!r:<12}  site={l.get('site')!r:>6}  "
                  f"id={l.get('id')!r:>6}  active={l.get('active')!r:<6}  "
                  f"area={l.get('areacode') or l.get('area')!r:<10}  description={l.get('description')!r}")

        print()
        print("Match the site whose description looks like Plant 1730 / Buena Park to")
        print("get the real L2L_SITE_NUMBER, then match that site's six lines (by")
        print("description) to BP-LINE1..6 to get the real LINECODE_DICT values.")


if __name__ == "__main__":
    asyncio.run(main())
