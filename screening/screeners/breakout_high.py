"""N 日新高突破：收盘价创出过去 N 个交易日新高，且当天放量。"""
from __future__ import annotations

import pandas as pd

from screening.context import last_rows
from screening.params import COMMON_FILTERS, Param

META = {
    "name": "N日新高突破",
    "tags": ["突破", "量价"],
    "version": "1.0",
    "description": """
选出今天收盘突破前 N 个交易日最高价、并且成交量明显放大的股票。

**怎么算**

1. 前高 = 不含今天的前 N 个交易日最高价（前复权）。
2. 今日收盘价 > 前高。
3. 今日成交量 ≥ 过去 20 日均量 × 放量倍数。

**适合**：寻找平台整理后向上突破的股票。
**注意**：突破当日追高有回落风险，可结合大盘环境判断。
""",
    "columns": {"close": "收盘", "prev_high": "前高", "break_pct": "突破幅度%",
                "vol_ratio": "量比(20日)", "pct_chg": "当日涨幅%"},
}

PARAMS = [
    Param("window", "突破周期 N", "int", 60, min=10, max=500, unit="日"),
    Param("vol_mult", "放量倍数", "float", 1.5, min=0, max=10, step=0.1,
          help="今日成交量 / 过去 20 日平均成交量；填 0 不看成交量"),
    Param("max_break_pct", "突破幅度不超过", "float", 15.0, min=0, max=50, step=0.5, unit="%"),
    *COMMON_FILTERS,
]


def screen(ctx, params: dict) -> pd.DataFrame:
    n = params["window"]
    pool = ctx.universe(params)
    bars = ctx.bars(lookback=max(n, 20) + 2, symbols=pool)
    g = bars.groupby("symbol")
    bars["prev_high"] = g["high"].transform(lambda s: s.shift(1).rolling(n, min_periods=n).max())
    bars["vol_ma20"] = g["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=20).mean())
    bars["prev_close"] = g["close"].shift(1)

    last = last_rows(bars)
    last = last[pd.to_datetime(last["date"]).dt.date == ctx.as_of].copy()
    last["break_pct"] = (last["close"] / last["prev_high"] - 1) * 100
    last["vol_ratio"] = last["volume"] / last["vol_ma20"]
    last["pct_chg"] = (last["close"] / last["prev_close"] - 1) * 100
    cond = (last["break_pct"] > 0) & (last["break_pct"] <= params["max_break_pct"])
    if params["vol_mult"] > 0:
        cond &= last["vol_ratio"] >= params["vol_mult"]
    out = last[cond]
    return out.sort_values("vol_ratio", ascending=False)[
        ["symbol", "close", "prev_high", "break_pct", "vol_ratio", "pct_chg"]]
