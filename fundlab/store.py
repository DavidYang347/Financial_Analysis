"""Point-in-time access to the fundlab tables.

    fs = FundStore()
    fs.fin_asof(day)            # latest report per symbol published strictly before ``day``
    fs.events_between(a, b)     # announcements with a <= ann_date < b
    fs.table("forecast")        # whole table (filter on ann_date yourself)

Timing convention: the strategy reviews announcements after the close of
``day`` (v3 每日盘后流程) and trades at the next open, so information dated
``day`` itself is visible (``ann_date <= day``). Notices released after the
close are dated that day and are public before the next open.
"""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache

import pandas as pd

from data import config

DIR = config.LAKE_DIR / "fundlab"
TABLES = ("fin", "forecast", "events", "holder", "repurchase", "unlock", "pledge", "valuation", "notice_coverage")


class FundStore:
    def __init__(self, directory=None) -> None:
        self.dir = directory or DIR
        self._t: dict[str, pd.DataFrame] = {}

    def exists(self) -> bool:
        return (self.dir / "fin.parquet").exists()

    def table(self, name: str) -> pd.DataFrame:
        if name not in TABLES:
            raise ValueError(name)
        if name not in self._t:
            p = self.dir / f"{name}.parquet"
            df = pd.read_parquet(p) if p.exists() else pd.DataFrame()
            for c in ("ann_date", "period", "date", "month_end", "start", "end"):
                if c in df:
                    df[c] = pd.to_datetime(df[c])
            self._t[name] = df
        return self._t[name]

    # ---- point-in-time --------------------------------------------------------
    def fin_asof(self, day, history: int = 1) -> pd.DataFrame:
        """Reports public by the close of ``day``. ``history`` > 1 keeps the last N periods per symbol."""
        f = self.table("fin")
        t = pd.Timestamp(day)
        f = f[f["ann_date"] <= t]
        f = f.sort_values(["symbol", "period"])
        return f.groupby("symbol", sort=False).tail(history).reset_index(drop=True)

    def between(self, name: str, start, end, col: str = "ann_date") -> pd.DataFrame:
        """Rows with start <= col <= end (both inclusive)."""
        df = self.table(name)
        if df.empty:
            return df
        return df[(df[col] >= pd.Timestamp(start)) & (df[col] <= pd.Timestamp(end))]

    def status(self) -> dict:
        out = {}
        for n in TABLES:
            df = self.table(n)
            info = {"rows": int(len(df))}
            for c in ("ann_date", "date", "month_end"):
                if c in df and len(df):
                    info["from"], info["to"] = str(df[c].min())[:10], str(df[c].max())[:10]
                    break
            out[n] = info
        return out


@lru_cache(maxsize=1)
def shared() -> FundStore:
    return FundStore()
