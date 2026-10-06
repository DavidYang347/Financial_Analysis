"""Central configuration for the data module.

All settings can be overridden with environment variables or a `.env` file in
the project root (see `.env.example`).
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional at runtime
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")


def _env_list(name: str, default: str) -> list[str]:
    return [s.strip() for s in os.getenv(name, default).split(",") if s.strip()]


# Storage root: data/lake by default (git-ignored).
LAKE_DIR = Path(os.getenv("QUANT_LAKE_DIR", PROJECT_ROOT / "data" / "lake")).expanduser().resolve()
# Staging area for the initial full download. Can live on a faster local disk.
STAGING_DIR = Path(os.getenv("QUANT_STAGING_DIR", LAKE_DIR / "_staging")).expanduser().resolve()

# Fallback order for daily bars (as requested: lowest IP-ban risk first).
DAILY_SOURCE_ORDER = _env_list("DAILY_SOURCE_ORDER", "tencent,mootdx,eastmoney,baostock,akshare,tushare")
# Fallback order for adjustment factors (only these sources provide them).
FACTOR_SOURCE_ORDER = _env_list("FACTOR_SOURCE_ORDER", "mootdx,baostock,tushare")
# Sources queried (and merged) to build the stock universe.
UNIVERSE_SOURCE_ORDER = _env_list("UNIVERSE_SOURCE_ORDER", "baostock,mootdx,eastmoney,akshare,tushare")
# Sources for the trading calendar.
CALENDAR_SOURCE_ORDER = _env_list("CALENDAR_SOURCE_ORDER", "akshare,baostock,tushare,tencent")

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "").strip()
# Extra TDX (mootdx) servers to try first, "ip:port,ip:port".
TDX_SERVERS = _env_list("TDX_SERVERS", "")

WORKERS = int(os.getenv("DATA_WORKERS", "8"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))
# A source is disabled for the rest of a run after this many consecutive errors.
SOURCE_MAX_CONSECUTIVE_ERRORS = int(os.getenv("SOURCE_MAX_CONSECUTIVE_ERRORS", "8"))

DUCKDB_MEMORY_LIMIT = os.getenv("DUCKDB_MEMORY_LIMIT", "1GB")
DUCKDB_THREADS = int(os.getenv("DUCKDB_THREADS", "4"))

# Daily bars are considered final after this local (Asia/Shanghai) time.
MARKET_TZ = "Asia/Shanghai"
MARKET_CLOSE_FINAL = os.getenv("MARKET_CLOSE_FINAL", "15:30")

# First trading day of the Shanghai exchange.
EARLIEST_DATE = date(1990, 12, 19)
