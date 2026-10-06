"""Unified schemas shared by all sources and the storage layer."""
from __future__ import annotations

import pandas as pd
import pyarrow as pa

# Daily bars are stored UNADJUSTED. Units are normalized across sources:
#   prices: CNY, volume: shares, amount: CNY, turnover: percent (may be null)
DAILY_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "turnover", "source"]

DAILY_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("date", pa.date32()),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("volume", pa.int64()),
    ("amount", pa.float64()),
    ("turnover", pa.float64()),
    ("source", pa.string()),
])

# Cumulative backward-adjustment (hfq) factor, stored at change points only.
# factor(t) = value of the latest row with date <= t; 1.0 before the first row.
ADJ_COLUMNS = ["symbol", "date", "adj_factor", "source"]
ADJ_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("date", pa.date32()),
    ("adj_factor", pa.float64()),
    ("source", pa.string()),
])

STOCK_COLUMNS = ["symbol", "code", "exchange", "name", "board", "list_date", "delist_date", "status", "sources"]
STOCK_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("code", pa.string()),
    ("exchange", pa.string()),
    ("name", pa.string()),
    ("board", pa.string()),
    ("list_date", pa.date32()),
    ("delist_date", pa.date32()),
    ("status", pa.string()),  # listed | delisted
    ("sources", pa.string()),
])


def empty_daily() -> pd.DataFrame:
    return DAILY_SCHEMA.empty_table().to_pandas()


def normalize_daily(df: pd.DataFrame, symbol: str, source: str) -> pd.DataFrame:
    """Coerce a source frame (with at least date/open/high/low/close) to DAILY_COLUMNS."""
    if df is None or df.empty:
        return empty_daily()
    out = pd.DataFrame({
        "symbol": symbol,
        "date": pd.to_datetime(df["date"]).dt.date,
    })
    for col in ("open", "high", "low", "close", "amount", "turnover"):
        out[col] = pd.to_numeric(df[col], errors="coerce") if col in df else float("nan")
    vol = pd.to_numeric(df["volume"], errors="coerce") if "volume" in df else pd.Series(float("nan"), index=df.index)
    out["volume"] = vol.round().astype("Int64")
    out["source"] = source
    # Drop rows without a usable price (suspension placeholders, bad parses).
    out = out[(out["close"] > 0) & (out["open"] > 0) & (out["high"] > 0) & (out["low"] > 0)]
    out = out.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    return out[DAILY_COLUMNS]


def to_arrow_daily(df: pd.DataFrame) -> pa.Table:
    df = df[DAILY_COLUMNS].copy()
    df["volume"] = df["volume"].astype("Int64")
    return pa.Table.from_pandas(df, schema=DAILY_SCHEMA, preserve_index=False)
