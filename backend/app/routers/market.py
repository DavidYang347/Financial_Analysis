"""Market-data endpoints (read-only)."""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.deps import market_data
from data import symbols as sym
from data.query import MarketData

router = APIRouter(prefix="/api/v1", tags=["market"])

MAX_ROWS = 200_000


def _records(df):
    df = df.copy()
    for c in df.columns:
        if str(df[c].dtype).startswith("datetime"):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
    return df.astype(object).where(df.notna(), None).to_dict("records")


@router.get("/stocks")
def list_stocks(status: Literal["listed", "delisted"] | None = None,
                keyword: str | None = Query(None, max_length=32),
                limit: int | None = Query(None, ge=1, le=10000),
                md: MarketData = Depends(market_data)):
    df = md.stocks(status=status, keyword=keyword)
    total = len(df)
    if keyword and limit:
        # Exact code / name-prefix matches first, so "600000" or "浦发" lands on top.
        k = keyword.strip().upper()
        rank = (~(df["code"].str.startswith(k) | df["symbol"].str.startswith(k) |
                  df["name"].fillna("").str.startswith(keyword.strip()))).astype(int)
        df = df.assign(_r=rank).sort_values(["_r", "symbol"]).drop(columns="_r")
    if limit:
        df = df.head(limit)
    return {"count": total, "items": _records(df)}


@router.get("/stocks/{symbol}")
def get_stock(symbol: str, md: MarketData = Depends(market_data)):
    s = _norm(symbol)
    df = md.sql("SELECT * FROM stocks WHERE symbol = ?", [s])
    if df.empty:
        raise HTTPException(404, f"unknown symbol {s}")
    return _records(df)[0]


@router.get("/daily/{symbol}")
def daily_bars(symbol: str, start: date | None = None, end: date | None = None,
               adjust: Literal["none", "qfq", "hfq"] = "none",
               md: MarketData = Depends(market_data)):
    s = _norm(symbol)
    df = md.daily(s, start=start, end=end, adjust=adjust)
    if len(df) > MAX_ROWS:
        raise HTTPException(413, "too many rows; narrow the date range")
    return {"symbol": s, "adjust": adjust, "count": len(df), "items": _records(df.drop(columns=["symbol"]))}


@router.get("/daily")
def daily_multi(symbols: str = Query(..., description="comma-separated, max 50"),
                start: date | None = None, end: date | None = None,
                adjust: Literal["none", "qfq", "hfq"] = "none",
                md: MarketData = Depends(market_data)):
    syms = [_norm(x) for x in symbols.split(",") if x.strip()]
    if not syms or len(syms) > 50:
        raise HTTPException(400, "provide 1-50 symbols")
    df = md.daily(syms, start=start, end=end, adjust=adjust)
    if len(df) > MAX_ROWS:
        raise HTTPException(413, "too many rows; narrow the date range")
    return {"adjust": adjust, "count": len(df), "items": _records(df)}


@router.get("/cross-section/{day}")
def cross_section(day: date, adjust: Literal["none", "qfq", "hfq"] = "none",
                  md: MarketData = Depends(market_data)):
    df = md.cross_section(day, adjust=adjust)
    return {"date": day.isoformat(), "count": len(df), "items": _records(df)}


@router.get("/adj-factor/{symbol}")
def adj_factor(symbol: str, md: MarketData = Depends(market_data)):
    s = _norm(symbol)
    return {"symbol": s, "items": _records(md.adj_factors(s))}


@router.get("/calendar")
def calendar(start: date | None = None, end: date | None = None, md: MarketData = Depends(market_data)):
    days = md.calendar(start, end)
    return {"count": len(days), "items": [d.isoformat() for d in days]}


def _norm(symbol: str) -> str:
    try:
        return sym.normalize(symbol)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
