"""低波动：定期持有过去 N 日波动率最低的一篮子股票，带个股止损。"""
from __future__ import annotations

from screening.params import COMMON_FILTERS, Param

META = {
    "name": "低波动组合",
    "tags": ["轮动", "低波", "防守"],
    "version": "1.0",
    "description": """
低波动异象：长期看，波动小的股票风险调整后收益往往不差于高波动股票。

**怎么做**

1. 每个调仓日，计算股票池里每只股票过去 N 日的日收益率标准差。
2. 选波动最低的 M 只等权持有。
3. 调仓日之间不动；调仓时持仓亏损超过止损线的股票不再入选（演示如何读取当前持仓）。

**适合**：震荡或下跌市里的防守配置。
**注意**：低波股票常集中在银行、公用事业等少数行业。
""",
    "defaults": {"rebalance": "monthly", "lookback": 70, "benchmark": "equal"},
}

PARAMS = [
    Param("window", "波动率窗口", "int", 60, min=10, max=250, unit="日"),
    Param("top", "持股数量", "int", 30, min=1, max=200, unit="只"),
    Param("stop_loss", "止损线", "float", 15.0, min=0, max=90, step=1, unit="%",
          help="调仓时，亏损超过这个比例的持仓不再入选；0 表示不止损"),
    *COMMON_FILTERS,
]


def rebalance(ctx, params):
    n = params["window"]
    pool = ctx.universe(params)
    if pool.empty:
        return {}
    close = ctx.history("close", n + 1, pool)
    if len(close) < n + 1:
        return {}
    ret = close.pct_change(fill_method=None).iloc[1:]
    vol = ret.std().where(ret.count() >= n * 0.9).dropna()  # skip stocks with many suspended days
    banned = set()
    if params["stop_loss"] > 0:
        pos = ctx.positions()
        banned = set(pos.loc[pos["pnl_pct"] <= -params["stop_loss"], "symbol"])
        for s in banned:
            ctx.log(f"{s} 触发止损，剔除")
    picks = vol.drop(index=list(banned), errors="ignore").nsmallest(params["top"]).index
    return {s: 1 / len(picks) for s in picks} if len(picks) else {}
