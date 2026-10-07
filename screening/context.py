"""What a screener script gets to work with.

    def screen(ctx: ScreenContext, params: dict) -> pd.DataFrame:
        pool = ctx.universe(params)                   # stock pool after the common filters
        bars = ctx.bars(lookback=120, symbols=pool)   # qfq daily bars, last 120 trading days
        ...
        return df[["symbol", ...]]                    # one row per selected stock

All data comes from the local lake; nothing is downloaded during a screen.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd

from data.query import Adjust, MarketData


class ScreenContext:
    def __init__(self, md: MarketData, as_of: date | None = None) -> None:
        self.md = md
        latest = md.latest_date()
        if latest is None:
            raise RuntimeError("行情库为空，先在数据管理里完成数据下载")
        self.as_of: date = min(as_of, latest) if as_of else latest
        self._cache: dict = {}

    # ---- calendar ------------------------------------------------------------
    def trading_days(self, n: int) -> list[date]:
        """The last ``n`` trading days up to and including ``as_of`` (from stored bars)."""
        key = ("days", n)
        if key not in self._cache:
            df = self.md.sql(
                "SELECT DISTINCT date FROM daily WHERE date <= ? ORDER BY date DESC LIMIT ?", [self.as_of, int(n)])
            self._cache[key] = sorted(pd.to_datetime(df["date"]).dt.date)
        return self._cache[key]

    # ---- data access ---------------------------------------------------------
    def st_symbols(self) -> set[str]:
        """Symbols under risk warning (ST / *ST) on ``as_of``."""
        return self.md.st_history().on(self.as_of)

    def stocks(self) -> pd.DataFrame:
        if "stocks" not in self._cache:
            self._cache["stocks"] = self.md.stocks()
        return self._cache["stocks"]

    def bars(self, lookback: int, symbols: Iterable[str] | pd.DataFrame | None = None,
             adjust: Adjust = "qfq") -> pd.DataFrame:
        """Daily bars for the last ``lookback`` trading days, sorted by symbol and date.

        qfq is relative to the latest stored bar, so price levels match what
        you see in a charting tool today.
        """
        days = self.trading_days(lookback)
        if not days:
            return pd.DataFrame()
        if isinstance(symbols, pd.DataFrame):
            symbols = symbols["symbol"].tolist()
        syms = list(symbols) if symbols is not None else None
        key = ("bars", days[0], adjust)
        if key not in self._cache:
            df = self.md.daily(None, start=days[0], end=self.as_of, adjust=adjust)
            df["date"] = pd.to_datetime(df["date"])
            self._cache[key] = df
        df = self._cache[key]
        if syms is not None:
            df = df[df["symbol"].isin(syms)]
        if len(days) < lookback or df.empty:
            return df.reset_index(drop=True)
        return df[df["date"] >= pd.Timestamp(days[0])].reset_index(drop=True)

    def universe(self, params: dict | None = None) -> pd.DataFrame:
        """Listed stocks trading on ``as_of``, after the COMMON_FILTERS present in ``params``."""
        p = params or {}
        st = self.stocks()
        st = st[st["status"] == "listed"].copy()
        today = self.md.sql("SELECT symbol FROM daily WHERE date = ?", [self.as_of])
        st = st[st["symbol"].isin(today["symbol"])]  # drop suspended stocks
        if p.get("exclude_st", False):
            st = st[~st["symbol"].isin(self.md.st_history().on(self.as_of))]
        if p.get("exclude_bj", False):
            st = st[st["exchange"] != "BJ"]
        if p.get("boards"):
            st = st[st["board"].isin(p["boards"])]
        if p.get("min_list_days"):
            n = int(p["min_list_days"])
            counts = self.md.sql("SELECT symbol, count(*) AS n FROM daily WHERE date <= ? GROUP BY 1", [self.as_of])
            ok = set(counts.loc[counts["n"] >= n, "symbol"])
            st = st[st["symbol"].isin(ok)]
        if p.get("min_amount"):
            bars = self.bars(20, st, adjust="none")
            avg = bars.groupby("symbol")["amount"].mean()
            ok = set(avg[avg >= float(p["min_amount"]) * 1e8].index)
            st = st[st["symbol"].isin(ok)]
        return st.reset_index(drop=True)


# ---- indicator helpers screeners can import ----------------------------------
def by_symbol(df: pd.DataFrame, col: str):
    return df.groupby("symbol", sort=False)[col]


def sma(df: pd.DataFrame, col: str, n: int) -> pd.Series:
    return by_symbol(df, col).transform(lambda s: s.rolling(n, min_periods=n).mean())


def rsi(df: pd.DataFrame, n: int = 14, col: str = "close") -> pd.Series:
    def _rsi(s: pd.Series) -> pd.Series:
        d = s.diff()
        up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
        dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
        rs = up / dn.replace(0, np.nan)
        return 100 - 100 / (1 + rs)
    return by_symbol(df, col).transform(_rsi)


def last_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Latest row per symbol."""
    return df.groupby("symbol", sort=False).tail(1).reset_index(drop=True)


def limit_pct(symbol: str, name: str = "", day: date | None = None, is_st: bool | None = None) -> float:
    """Daily price limit on ``day`` (default today).

    Main board 10% (ST 5% before 2026-07-06), ChiNext 20% (10% before 2020-08-24),
    STAR 20%, BSE 30%. ``is_st`` overrides the name-based guess; pass
    ``ctx.md.st_history().is_st(symbol, day)`` for historical accuracy.
    """
    from strategy.rules import limit_pct as _rule
    st = is_st if is_st is not None else "ST" in (name or "").upper()
    pct = _rule(symbol, day or date.today(), st)
    return 0.10 if pct == float("inf") else pct
