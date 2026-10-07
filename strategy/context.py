"""What a strategy script gets to work with.

    def rebalance(ctx: StrategyContext, params: dict) -> dict[str, float] | None:
        pool = ctx.universe(params)                     # tradable stocks today, after common filters
        bars = ctx.bars(lookback=60, symbols=pool)      # qfq bars up to and including today
        ...
        return {"600000.SH": 0.5, "000001.SZ": 0.5}     # target weights of total equity

``rebalance`` is called after the close of every rebalance day. It only sees
data up to that day; the engine fills the orders at the next open (or at the
same close, depending on the backtest settings).
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd

from strategy.data import MarketHistory


class StrategyContext:
    def __init__(self, hist: MarketHistory, portfolio) -> None:
        self._hist = hist
        self._pf = portfolio
        self._i = 0
        self._cache: dict = {}
        self.state: dict = {}  # free-form storage that persists between rebalance calls
        self.logs: list[str] = []

    # ---- time ----------------------------------------------------------------
    def _set_day(self, i: int) -> None:
        self._i = i
        self._cache.clear()

    @property
    def today(self) -> date:
        """The signal day: data up to this day's close is visible."""
        return self._hist.all_days[self._i]

    def is_period_end(self, unit: str) -> bool:
        """Whether today is the last trading day of its ISO week ("week") or month ("month").

        The last day of the data counts as a period end. Looking at tomorrow's date is not look-ahead:
        the trading calendar is public in advance.
        """
        days = self._hist.all_days
        if self._i + 1 >= len(days):
            return True
        a, b = days[self._i], days[self._i + 1]
        if unit == "week":
            return a.isocalendar()[:2] != b.isocalendar()[:2]
        if unit == "month":
            return (a.year, a.month) != (b.year, b.month)
        raise ValueError("unit 只能是 week / month")

    def day_number(self) -> int:
        """Index of today in the trading calendar (increases by one per trading day)."""
        return self._i

    def trading_days(self, n: int) -> list[date]:
        """The last ``n`` trading days up to and including today."""
        return self._hist.all_days[max(0, self._i - n + 1): self._i + 1]

    # ---- data ----------------------------------------------------------------
    @property
    def md(self):
        """Underlying MarketData. Its queries are NOT limited to ``today``; avoid look-ahead yourself."""
        return self._hist.md

    def stocks(self) -> pd.DataFrame:
        """Stock list (symbol, name, board, list_date, delist_date, status, first_date).

        name/status are today's values; use ``is_st`` / ``st_symbols`` for point-in-time ST.
        """
        return self._hist.stocks

    def is_st(self, symbol: str) -> bool:
        """Whether ``symbol`` was under risk warning (ST / *ST) on ``today``."""
        return self._hist.st.is_st(symbol, self.today)

    def st_symbols(self) -> set[str]:
        """All symbols under risk warning on ``today``."""
        return self._hist.st.on(self.today)

    def bars(self, lookback: int, symbols: Iterable[str] | pd.DataFrame | None = None,
             adjust: str = "qfq") -> pd.DataFrame:
        """Daily bars for the last ``lookback`` trading days ending today, sorted by symbol and date.

        adjust: ``qfq`` (forward-adjusted to today's price level), ``hfq`` or ``none``.
        Columns: symbol, date, open, high, low, close, volume, amount, turnover.
        """
        if adjust not in ("qfq", "hfq", "none"):
            raise ValueError("adjust 只能是 qfq / hfq / none")
        key = ("bars", int(lookback), adjust)
        if key not in self._cache:
            df = self._hist.window(self._i, int(lookback)).copy()
            df["symbol"] = df["symbol"].astype(str)
            if adjust != "none":
                f = df["adj_factor"]
                if adjust == "qfq":
                    f = f / df.groupby("symbol", sort=False)["adj_factor"].transform("last")
                for c in ("open", "high", "low", "close"):
                    df[c] = (df[c] * f).round(4)
            self._cache[key] = df.drop(columns="adj_factor").reset_index(drop=True)
        df = self._cache[key]
        if symbols is not None:
            syms = symbols["symbol"].tolist() if isinstance(symbols, pd.DataFrame) else list(symbols)
            df = df[df["symbol"].isin(syms)].reset_index(drop=True)
        return df

    def history(self, field: str, lookback: int, symbols=None, adjust: str = "qfq") -> pd.DataFrame:
        """Wide table: index = date, columns = symbol, values = ``field`` (e.g. close)."""
        df = self.bars(lookback, symbols, adjust)
        return df.pivot(index="date", columns="symbol", values=field)

    def today_bars(self) -> pd.DataFrame:
        """Today's raw bar per symbol: open, high, low, close, prev_close, adj_factor, bar_no (index = symbol)."""
        if "today_bars" not in self._cache:
            d = self._hist.day(self._i).copy()
            d.index = d.index.astype(str)
            self._cache["today_bars"] = d
        return self._cache["today_bars"]

    def universe(self, params: dict | None = None) -> pd.DataFrame:
        """Stocks with a bar today (not suspended), after the COMMON_FILTERS present in ``params``.

        Only stocks already listed today are included, delisted ones too while
        they were trading, so the pool is free of survivorship bias. The ST
        filter uses the stock's ST status on ``today`` (downloaded history;
        stocks without history fall back to their current name).
        """
        p = params or {}
        key = ("universe", repr(sorted(p.items())))
        if key in self._cache:
            return self._cache[key]
        today = self._hist.day(self._i)
        st = self._hist.stocks
        st = st[st["symbol"].isin(today.index.astype(str))].copy()
        if p.get("exclude_st", False):
            st = st[~st["symbol"].isin(self._hist.st.on(self.today))]
        if p.get("exclude_bj", False):
            st = st[st["exchange"] != "BJ"]
        if p.get("boards"):
            st = st[st["board"].isin(p["boards"])]
        if p.get("min_list_days"):
            n = int(p["min_list_days"])
            bar_no = today["bar_no"]
            ok = set(bar_no[bar_no >= n].index.astype(str))
            st = st[st["symbol"].isin(ok)]
        if p.get("min_amount"):
            bars = self.bars(20, st, adjust="none")
            avg = bars.groupby("symbol")["amount"].mean()
            ok = set(avg[avg >= float(p["min_amount"]) * 1e8].index)
            st = st[st["symbol"].isin(ok)]
        out = st.reset_index(drop=True)
        self._cache[key] = out
        return out

    # ---- portfolio -----------------------------------------------------------
    @property
    def cash(self) -> float:
        return self._pf.cash

    @property
    def equity(self) -> float:
        """Total value at today's close."""
        return self._pf.equity()

    def positions(self) -> pd.DataFrame:
        """Current holdings: symbol, shares, price, value, weight, cost, pnl_pct, entry_date, days_held."""
        return self._pf.positions_frame(self.today)

    def weights(self) -> dict[str, float]:
        """Current weight of each holding in total equity."""
        eq = self._pf.equity()
        return {s: v / eq for s, v in self._pf.values().items()} if eq > 0 else {}

    # ---- misc ----------------------------------------------------------------
    def log(self, *args) -> None:
        """Write a line to the backtest log (shown on the report page)."""
        if len(self.logs) < 5000:
            self.logs.append(f"{self.today} " + " ".join(str(a) for a in args))


def top_n(score: pd.Series, n: int) -> dict[str, float]:
    """Equal weights for the ``n`` highest scores (NaN ignored)."""
    s = score.replace([np.inf, -np.inf], np.nan).dropna().sort_values(ascending=False).head(int(n))
    return {str(k): 1.0 / len(s) for k in s.index} if len(s) else {}
