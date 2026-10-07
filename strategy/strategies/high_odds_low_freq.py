"""高赔率低频基本面策略:materials/高赔率低频基本面交易策略_v3执行版.md 的量化实现。"""
from __future__ import annotations

from highodds.config import SPEC, resolve

META = {
    "name": "高赔率低频基本面策略",
    "tags": ["基本面", "事件驱动", "困境反转", "并购重组", "低频"],
    "version": "0.1",
    "description": """
以基本面为锚,寻找预期差,只在**结构赔率 ≥ 3:1、期望收益 ≥ 30%、悲观下行 ≤ 25%** 时出手。
完整实现见 `highodds/` 包,规则出处是 `materials/高赔率低频基本面交易策略_v3执行版.md`。

**每个交易日收盘后**

- 持仓监控:逻辑证伪(重组终止 / 立案调查 / 非标审计)减仓 ≥ 50% 并观察,确认失效清仓;跌破证伪价;
  止盈(基准价值 70% / 100%,乐观区间移动止盈);时间止损;事件节点通过后验证加仓;回撤熔断 8% / 12% / 18%。
- 弹药池里的标的价格进入买入线:记下,**次日复核**后再建试错仓(冷静期 ≥ 24 小时)。

**每周最后一个交易日**:六条通道扫描 → 雷达池 → 5 分钟快速淘汰 → 观察池 → 深度研究(完整赔率卡、评分卡、
敏感性三问)→ 弹药池。弹药池每周按最新价格重算。**每月最后一个交易日**:当前赔率审计、行业拐点通道。

**仓位**:单票仓位 = 风险预算 ÷ (证伪下行 + 跳空缓冲);二元事件 / ST / 小盘股风险预算减半;
总风险预算 ≤ 8%,单票 ≤ 10%,行业 ≤ 25%,期权仓合计 ≤ 5%。

**数据**:行情来自本地数据湖;基本面、业绩预告、公告事件、质押、增减持、回购、解禁需先运行
`make fundamentals`(下载 + 构建,可断点续传)。所有基本面数据都带公告日,回测只使用信号日当天及以前已公告的内容。

**和文档的差异(需要你后续补充)**:赔率卡的估值全部由程序按固定规则生成(PB 法为主),没有人工研究的
备考利润、分部估值、行业周期中枢;事件成功价用固定涨幅代替。证据等级只区分 A / B,没有 C 级线索源。
这些是最大的优化空间,见 `highodds/valuation.py` 顶部说明。

回测结束后,**策略观察**页面(每次回测附带的表)会给出:池子流转记录、赔率卡、按通道 / 母题的收益归因、
概率校准。它们存放在回测目录的 `ho_*.parquet` 里。
""",
    "defaults": {"rebalance": "daily", "lookback": 300, "benchmark": "equal", "start": "2025-01-02",
                "initial_cash": 1000000, "fill": "next_open",
                "tolerance": 0.001},   # 试错仓只有 0.3%-0.5%,默认的 1% 容差会把它们全部跳过
}

PARAMS = list(SPEC)

_books: dict = {}


def _book(ctx, params):
    from highodds.book import Book
    key = id(ctx)
    b = _books.get(key)
    if b is None or b.n_calls > 0 and ctx.state.get("_ho_book") is not b:
        _books.clear()
        b = Book(ctx.md, resolve(params))
        ctx.state["_ho_book"] = b
        _books[key] = b
    return b


def rebalance(ctx, params):
    b = _book(ctx, params)
    return b.step(ctx)


def finalize(ctx):
    b = ctx.state.get("_ho_book")
    out = b.finalize(ctx) if b else {}
    _books.clear()
    return out
