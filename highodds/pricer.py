"""事件前价格查询:某只股票在某个日期之前最近一个收盘价(hfq 口径)。

通道 C(重组终止后错杀)和事件类赔率卡的"失败落点"都要用到"事件公告前的价格"。
结果按 (symbol, date) 缓存,同一个事件只查一次库。
"""
from __future__ import annotations

import pandas as pd


class Pricer:
    def __init__(self, md) -> None:
        self.md = md
        self._cache: dict[tuple[str, pd.Timestamp], float | None] = {}

    def before(self, pairs: set[tuple[str, pd.Timestamp]]) -> dict[tuple[str, pd.Timestamp], float | None]:
        """每个 (symbol, date) 返回 date 之前最近一个交易日的 hfq 收盘价(找不到为 None)。"""
        norm = {(s, pd.Timestamp(d).normalize()) for s, d in pairs}
        todo = [p for p in norm if p not in self._cache]
        if todo:
            q = pd.DataFrame(todo, columns=["symbol", "d"])
            syms = sorted(q["symbol"].unique())
            lo = (q["d"].min() - pd.Timedelta(days=20)).date()
            hi = q["d"].max().date()
            ph = ",".join("?" * len(syms))
            px = self.md.sql(f"""SELECT symbol, date, close * adj_factor AS px FROM daily_hfq
                                 WHERE symbol IN ({ph}) AND date BETWEEN ? AND ? ORDER BY date""", [*syms, lo, hi])
            px["date"] = pd.to_datetime(px["date"]).astype("datetime64[ns]")
            q["d"] = q["d"].astype("datetime64[ns]")
            got = pd.merge_asof(q.sort_values("d"), px, left_on="d", right_on="date", by="symbol",
                                direction="backward", allow_exact_matches=False)
            for r in got.itertuples():
                self._cache[(r.symbol, r.d)] = None if pd.isna(r.px) else float(r.px)
        return {(s, d): self._cache.get((s, pd.Timestamp(d).normalize())) for s, d in pairs}
