"""Tencent (腾讯证券) public K-line endpoint.

newfqkline returns at most 2000 bars per request; we page backwards by date.
Row layout: [date, open, close, high, low, volume, {events}, turnover%, amount(万元), ...]
Volume is in 手, except STAR-market stocks which are in shares (handled by
``volume_to_shares``).
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from data import symbols as sym
from data.config import EARLIEST_DATE
from data.schema import empty_daily, normalize_daily
from data.sources.base import DataSource, HttpMixin, SourceError, clip_range, volume_to_shares

URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
PAGE = 2000


class TencentSource(HttpMixin, DataSource):
    name = "tencent"
    default_headers = {**HttpMixin.default_headers, "Referer": "https://gu.qq.com/"}

    def _page(self, code: str, start: date, end: date) -> list[list]:
        param = f"{code},day,{start.isoformat()},{end.isoformat()},{PAGE},"
        js = self.get_json(URL, params={"param": param})
        if not isinstance(js, dict) or js.get("code") != 0:
            raise SourceError(f"tencent bad response for {code}: {str(js)[:200]}")
        node = (js.get("data") or {}).get(code)
        if not isinstance(node, dict):
            return []
        return node.get("day") or node.get("qfqday") or []

    def _raw_rows(self, code: str, start: date, end: date) -> list[list]:
        rows: list[list] = []
        cur_end = end
        for _ in range(50):  # 50 * 2000 bars is far beyond any A-share history
            page = self._page(code, start, cur_end)
            if not page:
                break
            rows = page + rows
            first = date.fromisoformat(page[0][0])
            if len(page) < PAGE or first <= start:
                break
            cur_end = first - timedelta(days=1)
        return rows

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        code = sym.to_tencent(symbol)
        rows = self._raw_rows(code, max(start, EARLIEST_DATE), end)
        if not rows:
            return empty_daily()
        df = pd.DataFrame({
            "date": [r[0] for r in rows],
            "open": [r[1] for r in rows],
            "close": [r[2] for r in rows],
            "high": [r[3] for r in rows],
            "low": [r[4] for r in rows],
            "volume": [r[5] for r in rows],
            "turnover": [r[7] if len(r) > 7 and r[7] not in ("", None) else None for r in rows],
            "amount": [r[8] if len(r) > 8 and r[8] not in ("", None) else None for r in rows],
        })
        for c in ("open", "close", "high", "low", "volume", "turnover", "amount"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["amount"] = df["amount"] * 1e4  # 万元 -> 元
        df["volume"] = volume_to_shares(df["volume"], df["amount"], df["close"], default_multiplier=100)
        return normalize_daily(clip_range(df, start, end), symbol, self.name)

    def trade_calendar(self) -> list[date]:
        """Past trading days, derived from the SSE Composite index bars."""
        rows = self._raw_rows("sh000001", EARLIEST_DATE, date.today())
        if not rows:
            raise SourceError("tencent returned no index bars")
        return sorted({date.fromisoformat(r[0]) for r in rows})
