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


L2L_BASE_URL = os.environ.get("L2L_BASE_URL", "https://lakeviewfarms.leading2lean.com/api/1.0")
L2L_API_KEY = _require_env("L2L_API_KEY")
L2L_SITE_NUMBER = int(os.environ.get("L2L_SITE_NUMBER", "2"))

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
CACHE_TTL_CURRENT_SECONDS = int(os.environ.get("CACHE_TTL_CURRENT_SECONDS", "120"))
CACHE_TTL_HISTORICAL_SECONDS = int(os.environ.get("CACHE_TTL_HISTORICAL_SECONDS", "21600"))  # 6h

# CORS: restrict this in production to the origin the dashboard is served
# from. "*" is convenient for local development only.
CORS_ALLOW_ORIGINS = os.environ.get("CORS_ALLOW_ORIGINS", "*").split(",")

MAX_CONCURRENT_L2L_REQUESTS = int(os.environ.get("MAX_CONCURRENT_L2L_REQUESTS", "8"))
