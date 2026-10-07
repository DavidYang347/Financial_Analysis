"""动量轮动：定期买入过去 N 日涨幅最大的一篮子股票，等权持有。"""
from __future__ import annotations

from screening.params import COMMON_FILTERS, Param
from strategy.context import top_n

META = {
    "name": "动量轮动",
    "tags": ["轮动", "多因子", "动量"],
    "version": "1.0",
    "description": """
截面动量：每个调仓日买入过去一段时间涨得最好的股票，下个调仓日重新排名。

**怎么做**

1. 股票池：通用过滤后的可交易股票（含当时尚未退市的股票，无幸存者偏差）。
2. 动量 = 过去 N 日涨幅，跳过最近 K 日（避开短期反转）。
3. 取动量最高的 M 只等权持有；可选只在动量为正时持有。

**适合**：趋势明显的行情。
**注意**：A 股短期反转效应较强，N 太短时动量常常失效；换手率较高。
""",
    "defaults": {"rebalance": "monthly", "lookback": 140, "benchmark": "equal"},
}

PARAMS = [
    Param("window", "动量窗口", "int", 120, min=5, max=250, unit="日"),
    Param("skip", "跳过最近", "int", 5, min=0, max=60, unit="日", help="排除最近几天的涨跌，减少短期反转的影响"),
    Param("top", "持股数量", "int", 20, min=1, max=200, unit="只"),
    Param("positive_only", "只买动量为正的股票", "bool", True),
    *COMMON_FILTERS,
]


def rebalance(ctx, params):
    n, k = params["window"], params["skip"]
    pool = ctx.universe(params)
    if pool.empty:
        return {}
    close = ctx.history("close", n + k + 1, pool)
    if len(close) < n + k + 1:
        return {}
    past, recent = close.iloc[0], close.iloc[-1 - k]
    mom = (recent / past - 1).dropna()
    if params["positive_only"]:
        mom = mom[mom > 0]
    ctx.log(f"股票池 {len(pool)} 只，有效动量 {len(mom)} 只")
    return top_n(mom, params["top"])
