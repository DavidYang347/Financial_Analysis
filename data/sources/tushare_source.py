"""Tushare Pro. Needs TUSHARE_TOKEN in .env; skipped automatically without it."""
from __future__ import annotations

import threading
import time
from datetime import date, timedelta

import pandas as pd

from data import config
from data import symbols as sym
from data.schema import empty_daily, normalize_daily
from data.sources.base import DataSource, SourceError, SourceUnavailable

# daily() returns at most 6000 rows per call; ~20 years of trading days fits in one window.
WINDOW_DAYS = 365 * 20


class TushareSource(DataSource):
    name = "tushare"

    def __init__(self) -> None:
        self._pro = None
        self._lock = threading.Lock()
        self._last_call = 0.0

    def _api(self):
        if not config.TUSHARE_TOKEN:
            raise SourceUnavailable("TUSHARE_TOKEN not set")
        if self._pro is None:
            try:
                import tushare as ts
            except ImportError as e:
                raise SourceUnavailable("tushare not installed") from e
            self._pro = ts.pro_api(config.TUSHARE_TOKEN)
        return self._pro

    def _call(self, fn: str, **kw) -> pd.DataFrame:
        pro = self._api()
        with self._lock:  # stay well under the per-minute quota
            wait = 0.15 - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
        try:
            return getattr(pro, fn)(**kw)
        except Exception as e:
            msg = str(e)
            if "权限" in msg or "token" in msg.lower():
                raise SourceUnavailable(f"tushare {fn}: {msg}") from e
            raise SourceError(f"tushare {fn}: {msg}") from e

    def check(self) -> None:
        self._api()

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        frames = []
        cur = start
        while cur <= end:
            w_end = min(end, cur + timedelta(days=WINDOW_DAYS))
            df = self._call("daily", ts_code=sym.to_tushare(symbol),
                            start_date=cur.strftime("%Y%m%d"), end_date=w_end.strftime("%Y%m%d"))
            if df is not None and not df.empty:
                frames.append(df)
            cur = w_end + timedelta(days=1)
        if not frames:
            return empty_daily()
        df = pd.concat(frames, ignore_index=True)
        df = pd.DataFrame({
            "date": pd.to_datetime(df["trade_date"], format="%Y%m%d"),
            "open": df["open"], "high": df["high"], "low": df["low"], "close": df["close"],
            "volume": pd.to_numeric(df["vol"], errors="coerce") * 100,     # 手 -> shares
            "amount": pd.to_numeric(df["amount"], errors="coerce") * 1000,  # 千元 -> 元
            "turnover": None,
        })
        return normalize_daily(df, symbol, self.name)

    def fetch_adj_factor(self, symbol: str) -> pd.DataFrame:
        df = self._call("adj_factor", ts_code=sym.to_tushare(symbol))
        if df is None or df.empty:
            return pd.DataFrame(columns=["date", "adj_factor"])
        df = pd.DataFrame({
            "date": pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date,
            "adj_factor": pd.to_numeric(df["adj_factor"], errors="coerce"),
        }).dropna().sort_values("date")
        # keep change points only
        return df[df["adj_factor"].diff().fillna(1) != 0].reset_index(drop=True)

    def list_stocks(self) -> pd.DataFrame:
        frames = []
        for status in ("L", "D", "P"):
            df = self._call("stock_basic", list_status=status,
                            fields="ts_code,name,list_date,delist_date,list_status")
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            raise SourceError("tushare stock_basic returned nothing")
        df = pd.concat(frames, ignore_index=True)
        out = pd.DataFrame({
            "symbol": df["ts_code"].map(sym.normalize),
            "name": df["name"],
            "list_date": pd.to_datetime(df["list_date"], format="%Y%m%d", errors="coerce"),
            "delist_date": pd.to_datetime(df["delist_date"], format="%Y%m%d", errors="coerce"),
            "status": df["list_status"].map({"L": "listed", "D": "delisted", "P": "listed"}),
        })
        return out[out["symbol"].map(sym.is_a_share)].drop_duplicates("symbol")

    def trade_calendar(self) -> list[date]:
        df = self._call("trade_cal", exchange="SSE", start_date="19901219",
                        end_date=f"{date.today().year}1231", is_open="1")
        return sorted(pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date)
