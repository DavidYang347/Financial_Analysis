"""Point-in-time access to the built fundamental tables.

Every method takes the signal day and only returns rows whose public date
(``ann_date`` / ``date``) is on or before it. The signal is produced after the
close, so an announcement dated the same day is visible.
"""
from __future__ import annotations

from datetime import date
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from data import config

FUND_DIR = config.LAKE_DIR / "fundamentals"
PLEDGE_LAG_DAYS = 7


class PIT:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else FUND_DIR
        self._cache: dict = {}

    def _read(self, name: str) -> pd.DataFrame:
        p = self.root / f"{name}.parquet"
        if not p.exists():
            raise FileNotFoundError(f"{p} 不存在；先运行 `make fundamentals`（下载并构建基本面数据）")
        return pd.read_parquet(p)

    @cached_property
    def fin(self) -> pd.DataFrame:
        return self._read("fin")

    @cached_property
    def forecast(self) -> pd.DataFrame:
        return self._read("forecast")

    @cached_property
    def pledge(self) -> pd.DataFrame:
        return self._read("pledge")

    @cached_property
    def holder(self) -> pd.DataFrame:
        return self._read("holder")

    @cached_property
    def repurchase(self) -> pd.DataFrame:
        return self._read("repurchase")

    @cached_property
    def unlock(self) -> pd.DataFrame:
        return self._read("unlock")

    @cached_property
    def notices(self) -> pd.DataFrame:
        return self._read("notices")

    # ---- financial statements ------------------------------------------------------
    def fin_visible(self, day) -> pd.DataFrame:
        """All statement rows public by ``day`` with single-quarter columns added."""
        mask = self.fin["ann_date"] <= pd.Timestamp(day)
        key = ("fin", int(mask.sum()))  # the visible set only changes when a new report becomes public
        if key in self._cache:
            return self._cache[key]
        self._cache.clear()
        f = self.fin[mask].sort_values(["symbol", "period"]).copy()
        f["year"] = f["period"].dt.year
        f["month"] = f["period"].dt.month
        g = f.groupby(["symbol", "year"], sort=False)
        for col, q in (("ocf", "ocf_q"), ("np_parent", "np_q"), ("revenue", "rev_q")):
            prev = g[col].shift(1)
            prev_month = g["month"].shift(1)
            ok = (f["month"] == 3) | (prev_month == f["month"] - 3)
            f[q] = np.where(f["month"] == 3, f[col], np.where(ok, f[col] - prev, np.nan))
        self._cache[key] = f
        return f

    def latest_fin(self, day) -> pd.DataFrame:
        """Most recent statement per symbol (any period), indexed by symbol."""
        f = self.fin_visible(day)
        return f.groupby("symbol", sort=False).tail(1).set_index("symbol")

    def annual(self, day, n: int = 5) -> pd.DataFrame:
        """Up to ``n`` latest annual (December) reports per symbol."""
        f = self.fin_visible(day)
        a = f[f["month"] == 12]
        return a.groupby("symbol", sort=False).tail(n)

    def prior_quarters(self, day, k: int = 2) -> pd.DataFrame:
        """Last ``k`` single-quarter rows per symbol (ocf_q / np_q / rev_q)."""
        f = self.fin_visible(day)
        return f.groupby("symbol", sort=False).tail(k)

    # ---- events ----------------------------------------------------------------------
    def events(self, day, days_back: int, tags: list[str] | None = None) -> pd.DataFrame:
        n = self.notices
        d1 = pd.Timestamp(day)
        m = (n["ann_date"] <= d1) & (n["ann_date"] > d1 - pd.Timedelta(days=days_back)) & (n["event"] != "")
        if tags:
            m &= n["event"].isin(tags)
        return n[m]

    def forecasts(self, day, days_back: int) -> pd.DataFrame:
        f = self.forecast
        d1 = pd.Timestamp(day)
        return f[(f["ann_date"] <= d1) & (f["ann_date"] > d1 - pd.Timedelta(days=days_back))]

    def holder_moves(self, day, days_back: int, direction: str | None = None) -> pd.DataFrame:
        h = self.holder
        d1 = pd.Timestamp(day)
        m = (h["ann_date"] <= d1) & (h["ann_date"] > d1 - pd.Timedelta(days=days_back))
        if direction:
            m &= h["direction"] == direction
        return h[m]

    def repurchases(self, day, days_back: int) -> pd.DataFrame:
        r = self.repurchase
        d1 = pd.Timestamp(day)
        return r[(r["ann_date"] <= d1) & (r["ann_date"] > d1 - pd.Timedelta(days=days_back))]

    def pledge_latest(self, day) -> pd.Series:
        p = self.pledge
        # 质押统计按周五日期记,但披露要晚几天;保守地把可见时间推后 7 天
        d1 = pd.Timestamp(day) - pd.Timedelta(days=PLEDGE_LAG_DAYS)
        p = p[(p["date"] <= d1) & (p["date"] > d1 - pd.Timedelta(days=45))]
        return p.sort_values("date").groupby("symbol").tail(1).set_index("symbol")["ratio"]

    def unlocks_ahead(self, day, days_ahead: int = 60) -> pd.DataFrame:
        """Scheduled releases are public well in advance, so the calendar ahead is fair to look at."""
        u = self.unlock
        d1 = pd.Timestamp(day)
        return u[(u["date"] > d1) & (u["date"] <= d1 + pd.Timedelta(days=days_ahead))]


_pit: PIT | None = None


def pit() -> PIT:
    global _pit
    if _pit is None:
        _pit = PIT()
    return _pit


class AdjFactors:
    """Backward-adjust factor lookups, used to put per-share figures on the price's share basis."""

    def __init__(self, md) -> None:
        f = md.sql("SELECT symbol, date, adj_factor FROM adj_factor ORDER BY date")
        f["date"] = pd.to_datetime(f["date"]).astype("datetime64[ns]")
        self.f = f

    def at(self, symbols: pd.Series, dates: pd.Series) -> np.ndarray:
        left = pd.DataFrame({"symbol": symbols.to_numpy(),
                             "date": pd.to_datetime(dates).to_numpy().astype("datetime64[ns]")})
        left["_i"] = np.arange(len(left))
        left = left.sort_values("date")
        out = pd.merge_asof(left, self.f, on="date", by="symbol", direction="backward")
        out = out.sort_values("_i")
        return out["adj_factor"].fillna(1.0).to_numpy()


def period_label(p: date) -> str:
    return f"{p.year}Q{(p.month - 1) // 3 + 1}"
