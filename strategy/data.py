"""Point-in-time market data for the backtest engine.

Bars are loaded from the lake in chunks of trading days (plus a warm-up
window), so a ten-year backtest over the whole market never holds all bars in
memory at once.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from data.query import MarketData

CHUNK_DAYS = 120
BAR_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "turnover", "adj_factor"]


class BarChunk:
    """Raw daily bars (+ hfq factor) for ``[days[lo], days[hi]]``, sorted by symbol, date."""

    def __init__(self, df: pd.DataFrame, lo: int, hi: int) -> None:
        self.lo, self.hi = lo, hi
        self.df = df
        self.dates = df["date"].to_numpy()
        # Row positions sorted by date, so one day's cross-section is a contiguous slice.
        self._order = np.argsort(self.dates, kind="stable")
        self._sorted_dates = self.dates[self._order]

    def day(self, d: date) -> pd.DataFrame:
        """Cross-section of one day, indexed by symbol."""
        t = np.datetime64(pd.Timestamp(d))
        a, b = np.searchsorted(self._sorted_dates, [t, t + np.timedelta64(1, "D")])
        return self.df.iloc[self._order[a:b]][DAY_COLUMNS].set_index("symbol")


DAY_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "prev_close", "adj_factor", "bar_no"]


class MarketHistory:
    """Trading calendar, stock list and chunked bar access for one backtest."""

    def __init__(self, md: MarketData, start: date, end: date, warmup: int = 260) -> None:
        self.md = md
        days = md.sql("SELECT DISTINCT date FROM daily WHERE date <= ? ORDER BY date", [end])["date"]
        self.all_days: list[date] = [pd.Timestamp(x).date() for x in days]
        if not self.all_days:
            raise RuntimeError("行情库为空，先在数据管理里完成数据下载")
        self.index = {d: i for i, d in enumerate(self.all_days)}
        self.days = [d for d in self.all_days if start <= d <= end]
        if not self.days:
            raise ValueError(f"{start} ~ {end} 之间没有交易日数据")
        self.warmup = max(int(warmup), 30)
        st = md.stocks()
        for c in ("list_date", "delist_date"):
            st[c] = pd.to_datetime(st[c], errors="coerce")
        first = md.sql("SELECT symbol, min(date) AS first_date FROM daily GROUP BY 1")
        first["first_date"] = pd.to_datetime(first["first_date"])
        self.stocks = st.merge(first, on="symbol", how="left")
        self.names = self.stocks.set_index("symbol")["name"].fillna("").to_dict()
        self.st = md.st_history()  # historical ST where downloaded, current name otherwise
        last = md.sql("SELECT symbol, max(date) AS last_date FROM daily GROUP BY 1")
        self.last_date = {r.symbol: pd.Timestamp(r.last_date).date() for r in last.itertuples()}
        self.first_date = self.stocks.set_index("symbol")["first_date"].to_dict()
        self.first_index = {s: self.index.get(pd.Timestamp(d).date()) for s, d in self.first_date.items()
                            if pd.notna(d)}
        self.delisted = set(self.stocks.loc[self.stocks["status"] == "delisted", "symbol"])
        self.chunk: BarChunk | None = None

    # ---- chunks --------------------------------------------------------------
    def _load(self, lo: int, hi: int) -> BarChunk:
        d0, d1 = self.all_days[lo], self.all_days[hi]
        df = self.md.sql("""
            SELECT symbol, date, open, high, low, close, volume, amount, turnover, adj_factor
            FROM daily_hfq WHERE date BETWEEN ? AND ? ORDER BY symbol, date
        """, [d0, d1])
        df["date"] = pd.to_datetime(df["date"])
        df["symbol"] = df["symbol"].astype("category")  # ~2M rows per chunk; strings would dominate memory
        g = df.groupby("symbol", sort=False, observed=True)
        prev_close = g["close"].shift(1)
        prev_f = g["adj_factor"].shift(1)
        # Limit reference price: yesterday's close moved onto today's share basis (ex-rights reference).
        df["prev_close"] = prev_close * prev_f / df["adj_factor"]
        # Trading days since the stock's first bar (0 = listing day).
        first_i = df["symbol"].map(self.first_index).astype("float64")
        day_pos = pd.Series(np.arange(lo, hi + 1, dtype="float64"), index=pd.to_datetime(self.all_days[lo:hi + 1]))
        day_i = df["date"].map(day_pos)
        df["bar_no"] = (day_i - first_i).fillna(10_000).astype("int64")
        return BarChunk(df, lo, hi)

    def ensure(self, i: int, lookback: int = 0) -> BarChunk:
        """Chunk that covers trading day index ``i`` and ``lookback`` days before it."""
        need_lo = max(0, i - max(lookback, 1))
        c = self.chunk
        if c is None or i > c.hi or need_lo < c.lo:
            lo = max(0, i - max(self.warmup, lookback + 5))
            hi = min(len(self.all_days) - 1, i + CHUNK_DAYS)
            self.chunk = None  # free the old chunk before loading the next one
            self.chunk = self._load(lo, hi)
        return self.chunk

    def window(self, i: int, lookback: int) -> pd.DataFrame:
        """Bars of the last ``lookback`` trading days ending at day index ``i`` (raw + adj_factor)."""
        c = self.ensure(i, lookback)
        d0 = np.datetime64(pd.Timestamp(self.all_days[max(0, i - lookback + 1)]))
        d1 = np.datetime64(pd.Timestamp(self.all_days[i]))
        m = (c.dates >= d0) & (c.dates <= d1)
        return c.df.loc[m, BAR_COLUMNS]

    def day(self, i: int) -> pd.DataFrame:
        return self.ensure(i).day(self.all_days[i])

    # ---- benchmark -----------------------------------------------------------
    def benchmark(self, spec: str) -> pd.Series | None:
        """Daily benchmark returns over ``self.days`` (index = date)."""
        d0, d1 = self.days[0], self.days[-1]
        if spec in ("", "none"):
            return None
        if spec == "equal":
            df = self.md.sql("""
                WITH r AS (
                    SELECT date, close * adj_factor / lag(close * adj_factor) OVER (PARTITION BY symbol ORDER BY date) - 1 AS ret
                    FROM daily_hfq WHERE date BETWEEN ? AND ?
                )
                SELECT date, avg(ret) AS ret FROM r WHERE ret IS NOT NULL AND abs(ret) < 1 GROUP BY 1 ORDER BY 1
            """, [self.all_days[max(0, self.index[d0] - 1)], d1])
        else:
            df = self.md.sql("""
                SELECT date, close * adj_factor / lag(close * adj_factor) OVER (ORDER BY date) - 1 AS ret
                FROM daily_hfq WHERE symbol = ? AND date BETWEEN ? AND ? ORDER BY date
            """, [spec, self.all_days[max(0, self.index[d0] - 1)], d1])
            if df.empty:
                raise ValueError(f"基准 {spec} 在回测区间没有行情")
        s = pd.Series(df["ret"].to_numpy(), index=[pd.Timestamp(x).date() for x in df["date"]])
        return s.reindex(self.days).fillna(0.0)
