"""East Money (东方财富) push2 / push2his endpoints. Rate limited, so it sits mid-chain."""
from __future__ import annotations

from datetime import date

import pandas as pd

from data import symbols as sym
from data.schema import empty_daily, normalize_daily
from data.sources.base import DataSource, HttpMixin, SourceError

KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
# SZ main+ChiNext, SH main+STAR, BJ
FS_A_SHARE = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"


class EastmoneySource(HttpMixin, DataSource):
    name = "eastmoney"
    default_headers = {**HttpMixin.default_headers, "Referer": "https://quote.eastmoney.com/"}

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        params = {
            "secid": sym.to_eastmoney_secid(symbol),
            "fields1": "f1,f2,f3",
            # date, open, close, high, low, volume(手), amount(元), amplitude, pct, chg, turnover%
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "0",
            "beg": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "lmt": "100000",
        }
        js = self.get_json(KLINE_URL, params=params)
        if not isinstance(js, dict):
            raise SourceError("eastmoney: bad response")
        data = js.get("data")
        if not data or not data.get("klines"):
            return empty_daily()
        rows = [k.split(",") for k in data["klines"]]
        df = pd.DataFrame({
            "date": [r[0] for r in rows],
            "open": [r[1] for r in rows],
            "close": [r[2] for r in rows],
            "high": [r[3] for r in rows],
            "low": [r[4] for r in rows],
            "volume": [float(r[5]) * 100 for r in rows],
            "amount": [r[6] for r in rows],
            "turnover": [r[10] if len(r) > 10 else None for r in rows],
        })
        return normalize_daily(df, symbol, self.name)

    def list_stocks(self) -> pd.DataFrame:
        recs = []
        page, size = 1, 100
        while True:
            js = self.get_json(CLIST_URL, params={
                "pn": page, "pz": size, "po": 1, "np": 1, "fltt": 2, "fid": "f12",
                "fs": FS_A_SHARE, "fields": "f12,f13,f14,f26",
            })
            data = (js or {}).get("data") or {}
            diff = data.get("diff") or []
            if isinstance(diff, dict):
                diff = list(diff.values())
            if not diff:
                break
            for r in diff:
                code = str(r["f12"])
                try:
                    s = sym.normalize(code)
                except ValueError:
                    continue
                if not sym.is_a_share(s):
                    continue
                ld = str(r.get("f26") or "")
                recs.append({
                    "symbol": s,
                    "name": str(r.get("f14") or "").strip(),
                    "list_date": pd.to_datetime(ld, format="%Y%m%d", errors="coerce") if ld not in ("", "-", "0") else pd.NaT,
                })
            if page * size >= int(data.get("total") or 0):
                break
            page += 1
        if not recs:
            raise SourceError("eastmoney returned an empty stock list")
        return pd.DataFrame(recs).drop_duplicates("symbol")
