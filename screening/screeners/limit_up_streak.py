"""连板股：最近连续涨停的股票。"""
from __future__ import annotations

import pandas as pd

from screening.context import limit_pct
from screening.params import COMMON_FILTERS, Param

META = {
    "name": "连续涨停",
    "tags": ["情绪", "涨停"],
    "version": "1.0",
    "description": """
选出截至最新交易日连续涨停的股票，按连板数排序。

**怎么算**

涨停判定使用不复权收盘价：`收盘 ≥ round(前收 × (1 + 涨跌幅限制), 2) - 0.01`。
涨跌幅限制按板块区分：主板 10%，ST 5%，创业板 / 科创板 20%，北交所 30%。
ST 判定用的是当前股票名称，历史上摘帽、戴帽的情况不做区分。

**适合**：观察市场情绪和题材热度。
**注意**：新股上市初期没有涨跌幅限制，已通过“上市满 N 个交易日”参数过滤。
""",
    "columns": {"close": "收盘", "streak": "连板数", "pct_chg": "当日涨幅%", "amount_yi": "成交额(亿)"},
}

PARAMS = [
    Param("min_streak", "至少连板", "int", 2, min=1, max=20, unit="板"),
    *[p for p in COMMON_FILTERS if p.key != "min_amount"],
]


def screen(ctx, params: dict) -> pd.DataFrame:
    pool = ctx.universe(params)
    names = dict(zip(pool["symbol"], pool["name"]))
    bars = ctx.bars(lookback=30, symbols=pool, adjust="none")
    bars["prev_close"] = bars.groupby("symbol")["close"].shift(1)
    lim = bars["symbol"].map(lambda s: limit_pct(s, names.get(s, "")))
    bars["is_limit"] = bars["close"] >= (bars["prev_close"] * (1 + lim)).round(2) - 0.011

    rows = []
    for symbol, g in bars.groupby("symbol", sort=False):
        if pd.Timestamp(g["date"].iloc[-1]).date() != ctx.as_of:
            continue
        flags = g["is_limit"].tolist()
        streak = 0
        for f in reversed(flags):
            if not f:
                break
            streak += 1
        if streak >= params["min_streak"]:
            last = g.iloc[-1]
            rows.append({"symbol": symbol, "close": last["close"], "streak": streak,
                         "pct_chg": (last["close"] / last["prev_close"] - 1) * 100,
                         "amount_yi": last["amount"] / 1e8})
    out = pd.DataFrame(rows, columns=["symbol", "close", "streak", "pct_chg", "amount_yi"])
    return out.sort_values(["streak", "amount_yi"], ascending=False)
