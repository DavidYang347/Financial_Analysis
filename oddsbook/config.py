"""All tunable parameters of the playbook.

Defaults start from 附录 G of the v3 document; seven of them were changed after
the 2025-01 ~ 2026-09 optimisation (see the comment above PARAMS).

Adding a parameter: append a ``Param`` here; the strategy page builds its form
from this list, and every module reads values from the resolved ``params`` dict.
"""
from __future__ import annotations

from screening.params import Param, opt

G_POOL, G_CH, G_ODDS, G_SCORE, G_SIZE, G_EXIT, G_RISK = (
    "股票池与能力圈", "筛选通道", "赔率测算", "评分与池子", "仓位", "持仓与退出", "组合风控")

# Tuned defaults (2026-10-07, python -m oddsbook.experiment; doc default in brackets):
#   odds_min 2.5 [3.0], research_per_week 40 [6], max_checklist_fail 2 [1],
#   risk_low / risk_std / risk_high 1% / 2% / 3% [0.5 / 1 / 1.5%], max_total_risk 16% [8%].
# The document's risk budget (8%, 0.5/1/1.5%) gives about half the return with half the drawdown.
PARAMS: list[Param] = [
    # ---- universe / competence ---------------------------------------------------
    Param("exclude_bj", "排除北交所", "bool", True, group=G_POOL, help="北交所流动性和数据覆盖都较差"),
    Param("exclude_financials", "排除金融业", "bool", True, group=G_POOL, help="银行、券商、保险的杠杆和估值口径不适用"),
    Param("focus_industries", "能力圈行业", "str", "", group=G_POOL,
          help="逗号分隔的东财行业名（如 化学制品,电力）。留空=不限。通道 A（重组）和 B（困境）不受限，其余通道只在能力圈内"),
    Param("min_amount", "日均成交额不低于", "float", 0.3, min=0, max=50, step=0.1, unit="亿元", group=G_POOL,
          help="近 20 日；快速淘汰第 2 条，默认 3000 万"),
    Param("adv_multiple", "成交额 ≥ 计划仓位的倍数", "float", 2.0, min=0, max=50, group=G_POOL),
    Param("min_list_days", "上市满", "int", 250, min=0, max=3000, unit="交易日", group=G_POOL),

    # ---- channels ------------------------------------------------------------------
    Param("radar_event_days", "事件回看", "int", 10, min=5, max=60, unit="交易日", group=G_CH,
          help="每周扫描时看最近多少个交易日的新公告"),
    Param("b_loss_years", "B 连续亏损年数", "int", 2, min=1, max=5, group=G_CH),
    Param("c_pb_pct", "C 估值历史分位低于", "float", 0.10, min=0.01, max=0.5, step=0.01, group=G_CH),
    Param("c_report_drop", "C 财报后跌幅超过", "float", 0.20, min=0.05, max=0.6, step=0.01, group=G_CH),
    Param("c_unlock_ratio", "C 大额解禁占流通市值", "float", 0.10, min=0.01, max=1, step=0.01, group=G_CH),
    Param("d_netcash_mcap", "D 净现金/市值 ≥", "float", 0.5, min=0.1, max=2, step=0.05, group=G_CH),
    Param("e_insider_pct", "E 增持占总股本 ≥", "float", 0.5, min=0.05, max=5, step=0.05, unit="%", group=G_CH,
          help="近 90 天二级市场增持合计（不含员工持股和回购专户）"),
    Param("e_buyback_premium", "E 回购上限价 / 现价 ≥", "float", 1.3, min=1.0, max=3, step=0.05, group=G_CH),
    Param("a2_mcap_max", "A2 壳特征：市值低于", "float", 50, min=5, max=500, unit="亿元", group=G_CH),
    Param("a2_debt_max", "A2 壳特征：资产负债率低于", "float", 50, min=10, max=90, unit="%", group=G_CH),
    Param("distress_industry", "困境反转要求行业不在下行", "select", "off", group=G_CH,
          options=[opt("off", "不检查"), opt("not_rolling", "行业毛利率没有连续两季回落"),
                   opt("turning", "行业毛利率连续两季改善")],
          help="§7 深研问题：困境是行业性的还是公司特有的，行业出清到了哪一步"),
    Param("distress_improving", "困境反转要求 TTM 利润同比改善", "bool", False, group=G_CH,
          help="§5 通道 B：减亏 / 扭亏要在财报上看得到；TTM 归母净利润低于一年前的不进弹药池"),
    Param("signals_off", "停用的信号", "str", "", group=G_CH,
          help="逗号分隔的信号代码（见 oddsbook/channels.py 的 SIGNALS），这些信号不进雷达池"),
    Param("f_enabled", "启用通道 F（行业拐点）", "bool", True, group=G_CH,
          help="用行业单季毛利率连续改善 + 收入止跌近似“行业领先指标”"),

    # ---- odds -----------------------------------------------------------------------
    Param("odds_min", "结构赔率门槛", "float", 2.5, min=1, max=10, step=0.1, group=G_ODDS),
    Param("odds_min_event", "事件类赔率门槛", "float", 2.5, min=1, max=10, step=0.1, group=G_ODDS),
    Param("quick_odds_min", "速算赔率进观察池", "float", 2.5, min=1, max=10, step=0.1, group=G_ODDS),
    Param("exp_min", "期望收益门槛", "float", 0.30, min=0, max=2, step=0.05, group=G_ODDS, help="按 12–18 个月"),
    Param("max_downside", "悲观下行上限", "float", 0.35, min=0.05, max=0.8, step=0.01, group=G_ODDS,
          help="文档建议尽量 ≤25%；这里是硬上限，25% 以上会在评分和仓位里自动吃亏"),
    Param("min_downside", "最小下行", "float", 0.08, min=0.01, max=0.3, step=0.01, group=G_ODDS,
          help="防止下行锚贴着现价导致赔率无穷大"),
    Param("broken_downside", "锚已跌破时的下行假设", "float", 0.25, min=0.05, max=0.6, step=0.01, group=G_ODDS,
          help="现价已低于下行锚：锚不再是托底，下行改按这个幅度估（防“假赔率”）"),
    Param("pe_mid_lo", "中枢 PE 下限", "float", 8, min=3, max=30, group=G_ODDS),
    Param("pe_mid_hi", "中枢 PE 上限", "float", 25, min=8, max=80, group=G_ODDS),
    Param("opt_uplift", "乐观情景相对基准的上浮", "float", 0.4, min=0, max=3, step=0.05, group=G_ODDS),
    Param("anchor_pe", "盈利下修锚用的 PE", "select", "floor", group=G_ODDS,
          options=[opt("own", "自身历史 PE 10% 分位（不低于中枢 PE 下限）"), opt("floor", "固定用中枢 PE 下限")],
          help="下行锚之一：EPS × 0.8 × 低位 PE"),
    Param("buyback_target", "回购上限价作为基准价值", "bool", False, group=G_ODDS,
          help="有回购信号时，基准价值取 max(估值, 回购上限价)；回购上限价是管理层自己写下的价值判断"),
    Param("max_upside", "基准上行上限", "float", 1.5, min=0.3, max=10, step=0.1, group=G_ODDS,
          help="基准价值最多是现价的 1 + 这个倍数；防止“恢复到多年前的利润”把目标价拍得过高（假赔率的另一半）"),
    Param("pb_cap_mult", "基准价值不超过 BVPS × 中枢 PB ×", "float", 1.5, min=0.5, max=5, step=0.1, group=G_ODDS,
          help="中枢 PB 取自身历史中位数和行业中位数的较大者；资产重估和事件类不受限"),
    Param("event_uplift_ma", "重组成功价相对公告前价", "float", 1.0, min=0.1, max=5, step=0.05, group=G_ODDS,
          help="没有备考利润时的近似（文档算例 4.80 → 10.50）"),
    Param("event_uplift_control", "控制权变更成功价相对公告前价", "float", 0.6, min=0.1, max=5, step=0.05, group=G_ODDS),
    Param("p_distress", "困境反转基准概率", "float", 0.45, min=0.05, max=0.95, step=0.01, group=G_ODDS,
          help="减亏预告 → 次年扭亏 40–55%"),
    Param("p_ma_node", "重组各节点概率", "str", "0.90,0.95,0.85,0.95", group=G_ODDS,
          help="问询回复 / 股东大会 / 交易所审核 / 证监会注册"),
    Param("p_control_inject", "控制权变更后注入资产概率", "float", 0.27, min=0.05, max=0.9, step=0.01, group=G_ODDS),
    Param("p_bankruptcy", "重整获批概率（已有投资人）", "float", 0.78, min=0.05, max=0.99, step=0.01, group=G_ODDS),
    Param("p_other", "其他母题基准概率", "float", 0.40, min=0.05, max=0.95, step=0.01, group=G_ODDS),
    Param("p_bonus_ocf", "经营现金流同步转正 +", "float", 0.08, min=0, max=0.3, step=0.01, group=G_ODDS),
    Param("p_bonus_multi", "每多一条独立通道 +", "float", 0.05, min=0, max=0.2, step=0.01, group=G_ODDS),
    Param("horizon_years", "持有期假设", "float", 1.25, min=0.25, max=3, step=0.25, unit="年", group=G_ODDS),
    Param("annual_min", "推迟 6 个月后年化不低于", "float", 0.15, min=0, max=1, step=0.01, group=G_ODDS),

    # ---- scoring / pools --------------------------------------------------------------
    Param("score_min", "弹药池评分门槛", "int", 70, min=0, max=100, group=G_SCORE),
    Param("score_high", "高确信评分", "int", 85, min=0, max=100, group=G_SCORE),
    Param("min_ab_evidence", "独立 A/B 级证据至少", "int", 2, min=0, max=5, group=G_SCORE),
    Param("max_checklist_fail", "入场检查清单最多几项“否”", "int", 2, min=0, max=11, group=G_SCORE),
    Param("radar_cap", "雷达池上限", "int", 200, min=10, max=2000, group=G_SCORE),
    Param("watch_cap", "观察池上限", "int", 60, min=5, max=500, group=G_SCORE),
    Param("ammo_cap", "弹药池上限", "int", 15, min=1, max=100, group=G_SCORE),
    Param("radar_weeks", "雷达池过期", "int", 8, min=1, max=52, unit="周", group=G_SCORE),
    Param("watch_months", "观察池无变化过期", "int", 6, min=1, max=24, unit="月", group=G_SCORE),
    Param("ammo_stale_months", "弹药池远离买入线降级", "int", 3, min=1, max=12, unit="月", group=G_SCORE),
    Param("research_per_week", "每周深研（完整赔率卡）数量", "int", 40, min=1, max=100, group=G_SCORE,
          help="模拟深研产能：每周从观察池按速算赔率挑这么多只做完整赔率卡（文档的人工节奏约每周 1 只）"),
    Param("research_rank", "深研排序", "select", "quick_odds", group=G_SCORE,
          options=[opt("quick_odds", "速算赔率"), opt("evidence", "证据条数 + 速算赔率"),
                   opt("small", "小市值优先")],
          help="观察池里先研究谁"),
    Param("research_retry_weeks", "深研未通过后多久可重做", "int", 4, min=1, max=52, unit="周", group=G_SCORE,
          help="期间出现新证据会提前重做"),
    Param("pending_days", "买单未成交多久撤单", "int", 3, min=1, max=20, unit="交易日", group=G_SCORE,
          help="涨停排队买不进就不追"),

    # ---- sizing -----------------------------------------------------------------------
    Param("risk_low", "单笔风险预算（低确信）", "float", 0.010, min=0, max=0.05, step=0.001, group=G_SIZE),
    Param("risk_std", "单笔风险预算（标准）", "float", 0.020, min=0, max=0.05, step=0.001, group=G_SIZE),
    Param("risk_high", "单笔风险预算（高确信）", "float", 0.030, min=0, max=0.05, step=0.001, group=G_SIZE),
    Param("gap_main", "跳空缓冲：主板", "float", 0.05, min=0, max=0.5, step=0.01, group=G_SIZE),
    Param("gap_growth", "跳空缓冲：ST/创业板/科创板", "float", 0.10, min=0, max=0.5, step=0.01, group=G_SIZE),
    Param("gap_binary", "跳空缓冲：北交所/二元事件", "float", 0.15, min=0, max=0.5, step=0.01, group=G_SIZE),
    Param("small_cap", "小盘股（风险预算减半）低于", "float", 50, min=0, max=500, unit="亿元", group=G_SIZE),
    Param("kelly_frac", "凯利上限倍数", "float", 0.25, min=0, max=1, step=0.05, group=G_SIZE),
    Param("max_weight", "单票上限（按成本）", "float", 0.10, min=0.01, max=0.5, step=0.01, group=G_SIZE),
    Param("max_weight_high", "单票上限（高确信+硬托底）", "float", 0.12, min=0.01, max=0.5, step=0.01, group=G_SIZE),
    Param("trim_weight", "单票市值超过则减到上限", "float", 0.15, min=0.01, max=0.5, step=0.01, group=G_SIZE),
    Param("max_industry", "同一行业上限", "float", 0.30, min=0.05, max=1, step=0.01, group=G_SIZE),
    Param("option_weight", "期权仓单只上限", "float", 0.01, min=0, max=0.05, step=0.005, group=G_SIZE),
    Param("option_total", "期权仓合计上限", "float", 0.05, min=0, max=0.2, step=0.01, group=G_SIZE),
    Param("max_total_risk", "总风险预算", "float", 0.16, min=0.01, max=0.3, step=0.005, group=G_SIZE,
          help="各仓位按证伪价计算的潜在亏损之和"),
    Param("max_positions", "持仓数量上限", "int", 15, min=1, max=50, group=G_SIZE),
    Param("adv_cap", "单票市值 ≤ 日均成交额 ×", "float", 1.0, min=0.05, max=10, step=0.05, group=G_SIZE,
          help="§2.2：5 个交易日内按日均成交额 10% 退出目标仓位的 50% ⇒ 仓位 ≤ 1 倍日均成交额"),
    Param("tranches", "分批比例", "str", "0.4,0.3,0.3", group=G_SIZE, help="试错 / 确认 / 优势"),
    Param("deep_discount", "跌破买入线多少提高首批", "float", 0.15, min=0, max=0.5, step=0.01, group=G_SIZE),
    Param("deep_first", "提高后的首批比例", "float", 0.6, min=0.1, max=1, step=0.05, group=G_SIZE),
    Param("add_odds", "赔率加仓需要的赔率", "float", 4.0, min=1, max=10, step=0.1, group=G_SIZE),

    # ---- exits ------------------------------------------------------------------------
    Param("falsify_cut", "证伪后立即减仓", "float", 0.5, min=0.1, max=1, step=0.05, group=G_EXIT),
    Param("reassess_days", "证伪后重估期限", "int", 2, min=0, max=20, unit="交易日", group=G_EXIT, help="文档 48 小时"),
    Param("price_stop", "跌破证伪价的动作", "select", "half", group=G_EXIT,
          options=[opt("half", "减仓 50%"), opt("all", "清仓"), opt("off", "不做价格止损")]),
    Param("time_stop_quarters", "催化剂过期后几个季度时间止损", "int", 2, min=1, max=8, group=G_EXIT),
    Param("tp1", "达到基准价值的比例时首次止盈", "float", 0.8, min=0.4, max=1.2, step=0.05, group=G_EXIT),
    Param("tp1_cut", "首次止盈减仓", "float", 0.25, min=0, max=1, step=0.05, group=G_EXIT),
    Param("tp2_cut", "达到基准价值减仓", "float", 0.35, min=0, max=1, step=0.05, group=G_EXIT),
    Param("trail", "利润仓移动止盈回撤", "float", 0.22, min=0.05, max=0.6, step=0.01, group=G_EXIT),
    Param("odds_reduce", "当前赔率低于则减仓", "float", 2.0, min=0, max=10, step=0.1, group=G_EXIT),
    Param("odds_exit", "当前赔率低于则清仓", "float", 1.0, min=0, max=10, step=0.1, group=G_EXIT),

    # ---- portfolio risk ----------------------------------------------------------------
    Param("dd1", "回撤熔断 1：暂停新开仓", "float", 0.08, min=0.01, max=0.5, step=0.01, group=G_RISK),
    Param("dd2", "回撤熔断 2：风险预算降到 5%", "float", 0.12, min=0.01, max=0.5, step=0.01, group=G_RISK),
    Param("dd3", "回撤熔断 3：只留高确信核心仓", "float", 0.18, min=0.01, max=0.6, step=0.01, group=G_RISK),
    Param("dd2_risk", "熔断 2 后的总风险预算", "float", 0.05, min=0.01, max=0.3, step=0.005, group=G_RISK),
    Param("cooldown_days", "熔断冷静期", "int", 10, min=0, max=60, unit="交易日", group=G_RISK,
          help="冷静期结束后以当时净值为新的高点重新计算回撤"),
    Param("panic_week_drop", "恐慌剧本：全市场周跌幅超过", "float", 0.08, min=0.02, max=0.3, step=0.01, group=G_RISK),
    Param("panic_week_risk", "恐慌剧本：当周新增风险上限", "float", 0.03, min=0.005, max=0.1, step=0.005, group=G_RISK),
]


def floats(s: str) -> list[float]:
    return [float(x) for x in str(s).replace("，", ",").split(",") if x.strip()]
