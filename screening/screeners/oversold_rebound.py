"""超跌反弹：短期跌幅大、RSI 处于超卖区，最新一天收阳。"""
from __future__ import annotations

import pandas as pd

from screening.context import last_rows, rsi
from screening.params import COMMON_FILTERS, Param

META = {
    "name": "超跌反弹",
    "tags": ["反转"],
    "version": "1.0",
    "description": """
选出近期跌得很深、开始出现止跌迹象的股票。

**怎么算**

1. 过去 N 个交易日累计跌幅 ≥ 设定值（前复权）。
2. RSI 低于超卖阈值。
3. 最新交易日收阳线（收盘 > 开盘），且收盘高于前一日。

**适合**：短线博反弹。
**注意**：下跌趋势中的反弹常常很弱，基本面恶化的股票会一直跌，建议配合基本面研究使用。
""",
    "columns": {"close": "收盘", "drop_pct": "N日跌幅%", "rsi": "RSI", "pct_chg": "当日涨幅%"},
}

PARAMS = [
    Param("window", "统计周期 N", "int", 20, min=5, max=120, unit="日"),
    Param("min_drop", "累计跌幅至少", "float", 20.0, min=0, max=90, step=1, unit="%"),
    Param("rsi_period", "RSI 周期", "int", 14, min=2, max=60),
    Param("rsi_max", "RSI 低于", "float", 30.0, min=1, max=100, step=1),
    *COMMON_FILTERS,
]


def screen(ctx, params: dict) -> pd.DataFrame:
    n = params["window"]
    pool = ctx.universe(params)
    bars = ctx.bars(lookback=max(n, params["rsi_period"] * 4) + 2, symbols=pool)
    g = bars.groupby("symbol")
    bars["rsi"] = rsi(bars, params["rsi_period"])
    bars["close_n"] = g["close"].shift(n)
    bars["prev_close"] = g["close"].shift(1)
    # RSI on the previous day: we want "was oversold, now turning up"
    bars["rsi_prev"] = g["rsi"].shift(1)

    last = last_rows(bars)
    last = last[pd.to_datetime(last["date"]).dt.date == ctx.as_of].copy()
    last["drop_pct"] = (1 - last["close"] / last["close_n"]) * 100
    last["pct_chg"] = (last["close"] / last["prev_close"] - 1) * 100
    cond = (
        (last["drop_pct"] >= params["min_drop"])
        & (last["rsi_prev"].combine(last["rsi"], min) < params["rsi_max"])
        & (last["close"] > last["open"]) & (last["close"] > last["prev_close"])
    )
    out = last[cond]
    return out.sort_values("drop_pct", ascending=False)[["symbol", "close", "drop_pct", "rsi", "pct_chg"]]
