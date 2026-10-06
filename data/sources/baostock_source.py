"""BaoStock (证券宝). Free, no token, covers delisted SH/SZ stocks; no BSE coverage.

The baostock client holds one global socket, so every call is serialized
behind a process-wide lock. That makes it slow but reliable as a fallback.
"""
from __future__ import annotations

import logging
import threading
from datetime import date

import pandas as pd

from data import symbols as sym
from data.schema import empty_daily, normalize_daily
from data.sources.base import DataSource, SourceError, SourceUnavailable, clip_range

log = logging.getLogger(__name__)

_LOCK = threading.RLock()


class BaostockSource(DataSource):
    name = "baostock"

    def __init__(self) -> None:
        self._logged_in = False

    def _bs(self):
        try:
            import baostock as bs
        except ImportError as e:
            raise SourceUnavailable("baostock not installed") from e
        if not self._logged_in:
            lg = bs.login()
            if lg.error_code != "0":
                raise SourceError(f"baostock login failed: {lg.error_msg}")
            self._logged_in = True
        return bs

    def _rows(self, rs) -> pd.DataFrame:
        if rs.error_code != "0":
            # 10001001 = not logged in; force re-login next time.
            if rs.error_code.startswith("10001"):
                self._logged_in = False
            raise SourceError(f"baostock error {rs.error_code}: {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        return pd.DataFrame(rows, columns=rs.fields)

    def _query(self, fn: str, *args, **kw) -> pd.DataFrame:
        with _LOCK:
            for attempt in range(2):
                bs = self._bs()
                try:
                    return self._rows(getattr(bs, fn)(*args, **kw))
                except SourceError:
                    if attempt == 1:
                        raise
                    self._logged_in = False
                except Exception as e:  # socket errors inside the client
                    self._logged_in = False
                    if attempt == 1:
                        raise SourceError(f"baostock {fn} failed: {e!r}") from e
        raise SourceError("unreachable")

    def check(self) -> None:
        with _LOCK:
            self._bs()

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        if sym.split(symbol)[1] == "BJ":
            return empty_daily()
        df = self._query(
            "query_history_k_data_plus", sym.to_baostock(symbol),
            "date,open,high,low,close,volume,amount,turn,tradestatus",
            start_date=start.isoformat(), end_date=end.isoformat(), frequency="d", adjustflag="3",
        )
        if df.empty:
            return empty_daily()
        df = df[df["tradestatus"] == "1"]  # drop suspension placeholder rows
        df = df.rename(columns={"turn": "turnover"})
        return normalize_daily(clip_range(df, start, end), symbol, self.name)

    def fetch_adj_factor(self, symbol: str) -> pd.DataFrame:
        if sym.split(symbol)[1] == "BJ":
            return pd.DataFrame(columns=["date", "adj_factor"])
        df = self._query("query_adjust_factor", code=sym.to_baostock(symbol),
                         start_date="1990-01-01", end_date=date.today().isoformat())
        if df.empty:
            return pd.DataFrame(columns=["date", "adj_factor"])
        return pd.DataFrame({
            "date": pd.to_datetime(df["dividOperateDate"]).dt.date,
            "adj_factor": pd.to_numeric(df["backAdjustFactor"], errors="coerce"),
        }).dropna()

    def list_stocks(self) -> pd.DataFrame:
        df = self._query("query_stock_basic")
        df = df[df["type"] == "1"]  # 1 = stock
        out = pd.DataFrame({
            "symbol": df["code"].map(lambda c: sym.normalize(c.replace(".", ""))),
            "name": df["code_name"].str.strip(),
            "list_date": pd.to_datetime(df["ipoDate"], errors="coerce"),
            "delist_date": pd.to_datetime(df["outDate"].replace("", None), errors="coerce"),
            "status": df["status"].map({"1": "listed", "0": "delisted"}),
        })
        return out[out["symbol"].map(sym.is_a_share)].drop_duplicates("symbol")

    def trade_calendar(self) -> list[date]:
        df = self._query("query_trade_dates", start_date="1990-12-19", end_date=f"{date.today().year}-12-31")
        df = df[df["is_trading_day"] == "1"]
        return sorted(pd.to_datetime(df["calendar_date"]).dt.date)

    def close(self) -> None:
        if self._logged_in:
            try:
                import baostock as bs
                with _LOCK:
                    bs.logout()
            except Exception:
                pass
            self._logged_in = False
