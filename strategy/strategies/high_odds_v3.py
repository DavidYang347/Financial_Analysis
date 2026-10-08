"""高赔率 · 低频 · 基本面 v3：四级池子 + 六条通道 + 赔率卡 + 仓位与风控（oddsbook 实现）。"""
from __future__ import annotations

from oddsbook import config as cfg

META = {
    "name": "高赔率低频基本面 v3",
    "tags": ["基本面", "事件", "低频", "赔率"],
    "version": "0.2",
    "description": """
按 `materials/高赔率低频基本面交易策略_v3执行版.md` 实现的可回测版本。代码在 `oddsbook/`，数据在 `fundlab/`（`python -m fundlab fetch` 下载，`python -m fundlab build` 生成时点表）。

**节奏**（每天收盘后运行，次日开盘成交）

- 每周末：六条通道扫描全市场 → 雷达池；5 分钟快速淘汰（一票否决、成交额、有没有“变化”、速算赔率 ≥ 2.5）→ 观察池；按速算赔率挑几只做深研（完整赔率卡、评分卡、入场检查清单）→ 弹药池；弹药池每周用最新价格和财报重算。
- 每天：持仓公告逐条看（证伪 → 先减 50%，2 个交易日内重估；确认证伪 → 清仓；节点通过 → 验证加仓）；弹药池价格到买入线 → 次日建试错仓（冷静期 ≥ 1 天，新利空则不买）；止盈、移动止盈、价格止损兜底；回撤熔断。
- 每月末：持仓审计（当前赔率 < 2 减仓、< 1 清仓）、时间止损、池子过期清理。

**赔率卡**：三情景（悲观 = 证伪，基准，乐观），估值方法按母题选；下行锚取最保守的一个（历史最低 PB、净资产打折、事件前价格、盈利下修估值）；现价已低于下行锚时锚失效，按“锚已跌破时的下行假设”估（防假赔率）。买入线 = (T + R·D)/(1 + R)。敏感性三问（锚下压一档 / 概率 −10pp / 推迟 6 个月）至少过两问。

**仓位**：风险预算 ÷ (证伪下行 + 跳空缓冲)，事件 / ST / 小盘减半，1/4 凯利上限，单票 / 行业 / 期权仓 / 总风险预算 / 持仓数上限；试错 / 确认 / 优势三批。

**观察**：回测目录里额外保存 x_pools（池子规模）、x_signals（通道信号）、x_cards（赔率卡）、x_journal（每个决策及原因）、x_audits（月度审计）、x_ammo（期末弹药池）、x_trips（逐笔往返）、x_stats（按通道 / 母题的胜率、盈亏比、期望），在“高赔率观察”页查看。

**0.2 调参**（2025-01 ~ 2026-09，约 70 组对比，`python -m oddsbook.experiment --list` 可查）：结构赔率门槛 3.0 → 2.5、每周深研 6 → 40 只、检查清单允许 2 项“否”、单笔风险预算 0.5/1/1.5% → 1/2/3%、总风险预算 8% → 16%。想要文档原始的风险水平，把后两项改回即可（收益约减半，回撤约减半）。

**已知简化**（免费数据做不到的地方，见 oddsbook 各模块说明）：没有一致预期和备考利润，事件成功价用“事件前价格 × 倍数”近似；有息负债用总负债扣应付和预收近似；质押是公司层面比例；财报数值是最新口径（更正会覆盖原值）；公告只用标题分类。
""",
    "defaults": {"rebalance": "daily", "lookback": 30, "benchmark": "equal", "start": "2025-01-02",
                 "fill": "next_open", "tolerance": 0.002},
}

PARAMS = cfg.PARAMS


def _book(ctx, params):
    b = ctx.state.get("book")
    if b is None:
        from oddsbook.book import Book
        b = ctx.state["book"] = Book(params, ctx.md)
    return b


def rebalance(ctx, params):
    return _book(ctx, params).step(ctx)


def finalize(ctx, params):
    from oddsbook import journal
    b = ctx.state.get("book")
    if b is None:
        return {}
    out = journal.export(b, ctx)
    trips = journal.round_trips(getattr(ctx, "trades", None), out["journal"])
    out["trips"] = trips
    out["stats"] = journal.channel_stats(trips)
    return out
