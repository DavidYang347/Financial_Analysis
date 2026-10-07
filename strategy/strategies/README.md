# 策略

这个目录下的每个 `.py` 文件就是一个策略，页面里“策略管理”会自动列出来，“策略回测”可以选它运行回测。

- 新增策略：复制一个现有文件，改文件名和内容。文件名只能用小写字母、数字和下划线。
- 删除策略：删掉对应文件。已有的回测记录不受影响。
- 以 `_` 开头的文件不会被当作策略，可以放公共函数。
- 修改后不用重启后端，刷新页面即可。

## 脚本格式

```python
from screening.params import COMMON_FILTERS, Param
from strategy.context import top_n

META = {
    "name": "策略名称",                  # 必填
    "description": "Markdown 描述",      # 必填
    "tags": ["轮动"],                    # 可选
    "version": "1.0",                    # 可选
    "defaults": {                        # 可选，回测页的默认设置
        "rebalance": "monthly",          # daily / weekly / monthly / every_n
        "lookback": 140,                 # 策略需要的历史 K 线数量
        "benchmark": "equal",            # equal=全市场等权，none，或股票代码
    },
}

PARAMS = [                               # 可选，页面会生成表单（与筛选方法相同）
    Param("window", "动量窗口", "int", 120, min=5, max=250, unit="日"),
    *COMMON_FILTERS,
]

def rebalance(ctx, params):
    pool = ctx.universe(params)                       # 今天可交易的股票池
    close = ctx.history("close", 121, pool)           # 宽表：行=日期，列=股票
    mom = close.iloc[-1] / close.iloc[0] - 1
    return top_n(mom, 20)                             # {代码: 目标权重}
```

`rebalance` 在每个调仓日收盘后调用，返回值：

- `{代码: 权重}`：目标持仓，权重是占总资产的比例。合计不足 1 的部分留作现金，超过 1 会按比例缩小。不在字典里的持仓会被卖出。
- `{}`：清仓。
- `None`：这次不调仓，保持现有持仓。

`ctx` 提供（只能看到当天收盘为止的数据）：

- `ctx.today`：信号日
- `ctx.universe(params)`：当天有行情的股票（含当时还没退市的股票），应用通用过滤
- `ctx.bars(lookback, symbols, adjust="qfq")`：长表日线，列为 symbol, date, open, high, low, close, volume, amount, turnover
- `ctx.history(field, lookback, symbols)`：宽表
- `ctx.is_st(symbol)`、`ctx.st_symbols()`：当天的 ST 状态（来自历史 ST 记录）
- `ctx.positions()`、`ctx.weights()`、`ctx.cash`、`ctx.equity`：当前持仓与资金
- `ctx.state`：字典，跨调仓日保存自己的状态
- `ctx.log(...)`：写入回测日志，报告页可见
- `ctx.md`：底层 `MarketData`，查询不受日期限制，需要自己避免未来数据

## 撮合规则

- 成交价：默认次日开盘价（信号用当天收盘数据），也可以选当日收盘价成交。
- T+1：当天买入的股票当天不能卖。
- 整手：主板、创业板 100 股整数倍；科创板 200 股起、之后 1 股递增；北交所 100 股起。剩余不足一手时一次卖完。
- 涨跌停：开盘（或收盘）价在涨停价买不进、在跌停价卖不出。主板 10%、主板 ST 5%（2026-07-06 起 10%）、创业板 2020-08-24 起 20%、科创板 20%、北交所 30%；1996-12-16 之前和新股上市前 5 个交易日不设限。ST 按当天的状态判断（数据管理里下载的历史 ST 记录）。
- 停牌：没有当天行情就不能成交。被挡住的委托在之后的交易日自动重试，直到下一个调仓信号覆盖它。
- 费用：佣金（默认万 2.5，每笔最低 5 元）、卖出印花税（默认千 0.5）、滑点（默认万 5），都可在回测页修改。
- 除权除息：持仓股数按复权因子调整，相当于分红再投资、送转股自动到账。
- 退市：持仓在最后交易日之后按最后收盘价清算。

简化之处：没有历史 ST 记录的股票（未下载，或北交所）按当前名称判断，并用一字板检测兜底；不考虑成交量限制和集合竞价细节。

脚本在后端进程里执行，拥有和后端一样的权限，只放你自己写的或审阅过的代码。
