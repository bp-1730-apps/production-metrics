"""
Configuration, loaded from environment variables (see .env.example).

The L2L API key must never be hardcoded here or committed to source
control -- it grants read/write access to your L2L site. Set it via a
real environment variable or a local .env file that is gitignored.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; env vars can be set any other way


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable {name} is not set. "
            f"Copy .env.example to .env and fill it in, or export it directly."
        )
    return value


# `or default` rather than `.get(name, default)`: a GitHub Actions secret
# referenced in a workflow's `env:` block that doesn't exist evaluates to an
# empty string, not "unset" -- os.environ would then contain the key with
# value "", which `.get(name, default)` treats as present and returns as-is
# (breaking int() below). `or` correctly falls through on empty string too.
L2L_BASE_URL = os.environ.get("L2L_BASE_URL") or "https://lakeviewfarms.leading2lean.com/api/1.0"
L2L_API_KEY = _require_env("L2L_API_KEY")

# --- Site/line resolution -------------------------------------------------
#
# Earlier versions of this project hardcoded a LINECODE_DICT mapping BP-LINE1
# ..6 straight to guessed L2L "linecode" strings (and a guessed site number).
# Those guesses didn't match this account's real records, which is exactly
# the class of bug L2L's own API surfaces badly: an unmatched linecode is a
# *hard error* on the weekly reporting endpoint ("No lines found"), but is
# silently *ignored* on the daily endpoint, which then quietly falls back to
# unfiltered, whole-plant totals for every line you asked for -- looking
# exactly like "every line reports identical numbers" with no error at all.
#
# Instead of hand-copying values here and hoping they're still right, this
# app now resolves the real site code and real per-line "code" values at
# refresh time, straight from L2L's own read-only master-data endpoints
# (/api/1.0/sites/ and /api/1.0/lines/ -- see backend/directory.py). What
# you configure below are *hints* to match against those real records, not
# the final values themselves:
#
#   - SITE_HINTS: matched case-insensitively as a substring against each
#     site's `description` field. The first site that matches any hint wins.
#   - LINE_HINTS: for each of our display codes (BP-LINE1..6), a
#     comma-separated list of hints tried in order. Each hint is first tried
#     as an exact (case-insensitive) match against a line's real `code`
#     field -- if your hint already *is* the real code, this matches
#     immediately, same as the old hardcoded dict did when it was right.
#     Failing that, it's tried as a substring match against the line's
#     `description`, `externalid`, and `areacode` fields, so a hint like
#     "Line 1" or a product name works too.
#
# If a hint doesn't match anything, that line is skipped for this run (never
# sent to L2L as a bogus filter) and `data/trending-*.json`'s `errors` field
# -- and scripts/refresh_data.py's own stdout, visible right in the GitHub
# Actions log -- gets a message listing every real site/line your API key
# can actually see, so a wrong hint is self-diagnosing without any separate
# script or manual API poking.
#
# Setting the L2L_SITE_NUMBER environment variable / GitHub secret to a
# known-correct numeric site code skips site matching entirely and uses that
# value directly (useful once you know it, or if no site description hint
# will ever match reliably).
_site_number_env = os.environ.get("L2L_SITE_NUMBER")
L2L_SITE_NUMBER_OVERRIDE = int(_site_number_env) if _site_number_env else None

SITE_HINTS = [h.strip() for h in (os.environ.get("L2L_SITE_HINT") or "1730,Buena Park,Novus").split(",") if h.strip()]

# display code -> comma-separated hint(s), tried in order (see docstring above)
LINE_HINTS = {
    "BP-LINE1": "2A",
    "BP-LINE2": "PXM6",
    "BP-LINE3": "MP4",
    "BP-LINE4": "PL1",
    "BP-LINE5": "PL2",
    "BP-LINE6": "2E",
}

# How long to trust a cached period before re-fetching it from L2L.
# A period that has fully elapsed (e.g. last week, yesterday) is cached
# much longer than the current/in-progress period, since only the latter
# still changes as new production data comes in.
CACHE_TTL_CURRENT_SECONDS = int(os.environ.get("CACHE_TTL_CURRENT_SECONDS") or "120")
CACHE_TTL_HISTORICAL_SECONDS = int(os.environ.get("CACHE_TTL_HISTORICAL_SECONDS") or "21600")  # 6h

# CORS: restrict this in production to the origin the dashboard is served
# from. "*" is convenient for local development only.
CORS_ALLOW_ORIGINS = (os.environ.get("CORS_ALLOW_ORIGINS") or "*").split(",")

MAX_CONCURRENT_L2L_REQUESTS = int(os.environ.get("MAX_CONCURRENT_L2L_REQUESTS") or "8")
