"""Stock universe and trading calendar."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from data import config
from data import symbols as sym
from data.schema import STOCK_COLUMNS
from data.sources import SourceChain

log = logging.getLogger(__name__)


def build_universe(chain: SourceChain, order: list[str] | None = None) -> tuple[pd.DataFrame, dict[str, str]]:
    """Union of every available source's stock list.

    No single free source has everything: baostock has delisted SH/SZ stocks
    with IPO/delist dates but no BSE; TDX/eastmoney/akshare have current
    listings including BSE. We merge them, preferring the richest metadata.
    """
    frames: list[tuple[str, pd.DataFrame]] = []
    errors: dict[str, str] = {}
    for name in order or config.UNIVERSE_SOURCE_ORDER:
        res = chain.run(lambda s: s.list_stocks(), order=[name])
        if res.data is not None:
            df = res.data.copy()
            df["src"] = name
            frames.append((name, df))
            log.info("universe: %s -> %d symbols", name, len(df))
        errors.update(res.errors)
    if not frames:
        raise RuntimeError(f"no source could provide a stock list: {errors}")

    allf = pd.concat([f for _, f in frames], ignore_index=True)
    for c in ("list_date", "delist_date", "status"):
        if c not in allf:
            allf[c] = None
    allf["list_date"] = pd.to_datetime(allf["list_date"], errors="coerce")
    allf["delist_date"] = pd.to_datetime(allf["delist_date"], errors="coerce")
    priority = {n: i for i, (n, _) in enumerate(frames)}
    allf["prio"] = allf["src"].map(priority)
    allf = allf.sort_values(["symbol", "prio"])

    # Sources that list only currently trading stocks.
    current_sources = {n for n, f in frames if f.get("status") is None or f["status"].isna().all()}

    rows = []
    for symbol, g in allf.groupby("symbol", sort=True):
        name = next((n for n in g["name"] if isinstance(n, str) and n.strip()), "")
        list_date = g["list_date"].dropna().min() if g["list_date"].notna().any() else pd.NaT
        delist_date = g["delist_date"].dropna().max() if g["delist_date"].notna().any() else pd.NaT
        statuses = set(g["status"].dropna())
        listed_now = bool(set(g["src"]) & current_sources)
        if "delisted" in statuses and not listed_now:
            status = "delisted"
        elif "delisted" in statuses and pd.notna(delist_date) and delist_date.date() <= date.today():
            status = "delisted"
        else:
            status = "listed"
            delist_date = pd.NaT if status == "listed" else delist_date
        code, ex = sym.split(symbol)
        rows.append({
            "symbol": symbol, "code": code, "exchange": ex, "name": name, "board": sym.board(symbol),
            "list_date": list_date, "delist_date": delist_date, "status": status,
            "sources": ",".join(sorted(set(g["src"]))),
        })
    out = pd.DataFrame(rows, columns=STOCK_COLUMNS)
    return out, errors


def build_calendar(chain: SourceChain) -> tuple[list[date], str | None]:
    res = chain.run(lambda s: s.trade_calendar(), order=config.CALENDAR_SOURCE_ORDER)
    if res.data is None:
        raise RuntimeError(f"no source could provide a trading calendar: {res.errors}")
    days = sorted(d for d in res.data if d >= config.EARLIEST_DATE)
    return days, res.source


def latest_complete_trading_day(calendar: list[date], now: datetime | None = None) -> date:
    """Most recent trading day whose daily bar is final."""
    tz = ZoneInfo(config.MARKET_TZ)
    now = (now or datetime.now(tz)).astimezone(tz)
    hh, mm = (int(x) for x in config.MARKET_CLOSE_FINAL.split(":"))
    today = now.date()
    cutoff = today if now.time() >= time(hh, mm) else today - timedelta(days=1)
    past = [d for d in calendar if d <= cutoff]
    if not past:
        raise RuntimeError("calendar has no trading day before today")
    return past[-1]
