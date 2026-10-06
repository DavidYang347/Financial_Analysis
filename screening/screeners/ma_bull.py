"""均线多头排列：短中长期均线依次向上排开，且收盘价站上短期均线。"""
from __future__ import annotations

import pandas as pd

from screening.context import last_rows, sma
from screening.params import COMMON_FILTERS, Param

META = {
    "name": "均线多头排列",
    "tags": ["趋势"],
    "version": "1.0",
    "description": """
选出处在上升趋势里的股票：收盘价 > MA短 > MA中 > MA长，并且长期均线本身在抬头。

**怎么算**

1. 用前复权收盘价计算三条简单移动平均线。
2. 最新交易日满足 `close > MA短 > MA中 > MA长`。
3. 长期均线比 N 天前高，避免横盘里均线偶然交叉。

**适合**：顺势持有、寻找已经走出来的趋势股。
**注意**：信号滞后，刚启动的股票通常还不满足条件。
""",
    "columns": {"close": "收盘", "ma_s": "MA短", "ma_m": "MA中", "ma_l": "MA长",
                "ret_20": "20日涨幅%", "gap_pct": "偏离MA短%"},
}

PARAMS = [
    Param("short", "短期均线", "int", 5, min=2, max=60, unit="日"),
    Param("mid", "中期均线", "int", 20, min=5, max=120, unit="日"),
    Param("long", "长期均线", "int", 60, min=20, max=250, unit="日"),
    Param("slope_days", "长期均线抬头比较天数", "int", 5, min=1, max=60, unit="日"),
    Param("max_gap_pct", "收盘价偏离短期均线不超过", "float", 8.0, min=0, max=50, step=0.5, unit="%",
          help="过滤掉已经涨得太急的股票；填 50 等于不限制"),
    *COMMON_FILTERS,
]


def screen(ctx, params: dict) -> pd.DataFrame:
    s, m, l, k = params["short"], params["mid"], params["long"], params["slope_days"]
    if not s < m < l:
        raise ValueError("均线周期需要满足 短 < 中 < 长")
    pool = ctx.universe(params)
    bars = ctx.bars(lookback=l + k + 5, symbols=pool)
    bars["ma_s"] = sma(bars, "close", s)
    bars["ma_m"] = sma(bars, "close", m)
    bars["ma_l"] = sma(bars, "close", l)
    bars["ma_l_prev"] = bars.groupby("symbol")["ma_l"].shift(k)
    bars["close_20"] = bars.groupby("symbol")["close"].shift(20)

    last = last_rows(bars)
    last = last[pd.to_datetime(last["date"]).dt.date == ctx.as_of]
    cond = (
        (last["close"] > last["ma_s"]) & (last["ma_s"] > last["ma_m"]) & (last["ma_m"] > last["ma_l"])
        & (last["ma_l"] > last["ma_l_prev"])
    )
    out = last[cond].copy()
    out["gap_pct"] = (out["close"] / out["ma_s"] - 1) * 100
    out = out[out["gap_pct"] <= params["max_gap_pct"]]
    out["ret_20"] = (out["close"] / out["close_20"] - 1) * 100
    return out.sort_values("ret_20", ascending=False)[
        ["symbol", "close", "ma_s", "ma_m", "ma_l", "ret_20", "gap_pct"]]
