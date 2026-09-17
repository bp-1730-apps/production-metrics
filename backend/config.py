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
L2L_SITE_NUMBER = int(os.environ.get("L2L_SITE_NUMBER") or "2")

# code -> L2L linecode
LINECODE_DICT = {
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
