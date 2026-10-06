"""AKShare wrapper. Daily bars come from Sina first, then Tencent via akshare."""
from __future__ import annotations

from datetime import date

import pandas as pd

from data import symbols as sym
from data.schema import empty_daily, normalize_daily
from data.sources.base import DataSource, SourceError, SourceUnavailable


class AkshareSource(DataSource):
    name = "akshare"

    def _ak(self):
        try:
            import akshare as ak
        except ImportError as e:
            raise SourceUnavailable("akshare not installed") from e
        return ak

    def check(self) -> None:
        self._ak()

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        ak = self._ak()
        s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        errors = []
        # Sina: volume in shares, amount in CNY, turnover as a fraction.
        try:
            df = ak.stock_zh_a_daily(symbol=sym.to_sina(symbol), start_date=s, end_date=e, adjust="")
            if df is not None and not df.empty and "date" in df:
                df = df.rename(columns={"turnover": "turnover_frac"})
                df["turnover"] = pd.to_numeric(df["turnover_frac"], errors="coerce") * 100
                return normalize_daily(df, symbol, self.name)
        except Exception as ex:
            errors.append(f"sina: {ex!r}"[:200])
        # Tencent via akshare: volume in shares, amount in CNY, turnover as a fraction.
        try:
            df = ak.stock_zh_a_hist_tx(symbol=sym.to_tencent(symbol), start_date=s, end_date=e, adjust="")
            if df is not None and not df.empty:
                if "turnover" in df:
                    df["turnover"] = pd.to_numeric(df["turnover"], errors="coerce") * 100
                return normalize_daily(df, symbol, self.name)
        except Exception as ex:
            errors.append(f"tx: {ex!r}"[:200])
        if errors:
            raise SourceError("; ".join(errors))
        return empty_daily()

    def list_stocks(self) -> pd.DataFrame:
        ak = self._ak()
        frames = []
        try:
            df = ak.stock_info_a_code_name()
            frames.append(pd.DataFrame({"symbol": df["code"].astype(str), "name": df["name"].astype(str)}))
        except Exception as ex:
            raise SourceError(f"akshare stock_info_a_code_name failed: {ex!r}") from ex
        try:
            bj = ak.stock_info_bj_name_code()
            frames.append(pd.DataFrame({
                "symbol": bj["证券代码"].astype(str),
                "name": bj["证券简称"].astype(str),
                "list_date": pd.to_datetime(bj["上市日期"], errors="coerce"),
            }))
        except Exception:
            pass
        df = pd.concat(frames, ignore_index=True)
        df["symbol"] = df["symbol"].map(lambda c: _safe_norm(c))
        df = df.dropna(subset=["symbol"])
        df["name"] = df["name"].str.strip()
        return df[df["symbol"].map(sym.is_a_share)].drop_duplicates("symbol", keep="last")

    def trade_calendar(self) -> list[date]:
        ak = self._ak()
        try:
            df = ak.tool_trade_date_hist_sina()
        except Exception as ex:
            raise SourceError(f"akshare trade calendar failed: {ex!r}") from ex
        return sorted(pd.to_datetime(df["trade_date"]).dt.date)


def _safe_norm(code: str) -> str | None:
    try:
        return sym.normalize(code)
    except ValueError:
        return None
