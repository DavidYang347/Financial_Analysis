"""双均线择时：单只股票，短均线上穿长均线满仓，下穿清仓。"""
from __future__ import annotations

from screening.params import Param

META = {
    "name": "双均线择时",
    "tags": ["择时", "单票", "趋势"],
    "version": "1.0",
    "description": """
对一只股票做趋势择时：短期均线在长期均线之上时持有，否则空仓。

**怎么做**

1. 每个交易日收盘后，用前复权收盘价计算 MA短 和 MA长。
2. `MA短 > MA长` 时目标仓位 = 设定仓位，否则 0。
3. 默认次日开盘成交，涨停买不进、跌停卖不出、当日买入次日才能卖。

**适合**：观察一只股票在趋势跟踪下能否跑赢持有不动。
**注意**：震荡市里反复交叉会产生很多小亏损和手续费。
""",
    "defaults": {"rebalance": "daily", "lookback": 130, "benchmark": "600519.SH"},
}

PARAMS = [
    Param("symbol", "股票代码", "str", "600519.SH", help="如 600519.SH、000001.SZ"),
    Param("short", "短期均线", "int", 10, min=2, max=120, unit="日"),
    Param("long", "长期均线", "int", 60, min=5, max=250, unit="日"),
    Param("weight", "持仓时仓位", "float", 1.0, min=0.1, max=1.0, step=0.1),
]


def rebalance(ctx, params):
    sym, s, l = params["symbol"].strip().upper(), params["short"], params["long"]
    if s >= l:
        raise ValueError("短期均线需要小于长期均线")
    close = ctx.history("close", l + 1, [sym])
    if sym not in close.columns or close[sym].count() < l:
        return {}  # not enough history yet (or suspended): stay in cash
    c = close[sym].dropna()
    ma_s, ma_l = c.tail(s).mean(), c.tail(l).mean()
    return {sym: params["weight"]} if ma_s > ma_l else {}
