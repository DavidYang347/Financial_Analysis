"""All tunable parameters in one place.

``SPEC`` is the single source of truth: the strategy page form is built from it,
``resolve()`` turns a params dict into an attribute namespace (``p.odds_min``),
and defaults follow appendix G of the strategy document unless noted "假设".
"""
from __future__ import annotations

from types import SimpleNamespace

from screening.params import Param, opt

CHANNELS = ["A", "B", "C", "D", "E", "F"]
CHANNEL_NAMES = {"A": "并购重组/控制权", "B": "困境反转", "C": "错杀", "D": "资产重估", "E": "内部人行为", "F": "行业拐点"}

G_POOL, G_ODDS, G_VAL, G_SIZE, G_POOLS, G_EXIT, G_BRK, G_CH = (
    "股票池", "赔率门槛", "估值与概率", "仓位与风控", "池子容量", "建仓与退出", "回撤熔断", "通道参数")

SPEC: list[Param] = [
    # ---- 股票池
    Param("exclude_st", "排除 ST / *ST", "bool", False, group=G_POOL,
          help="文档把 ST 当作困境反转的主战场,默认不排除;ST 股自动减半风险预算、加大跳空缓冲"),
    Param("exclude_bj", "排除北交所", "bool", True, group=G_POOL, help="北交所历史 ST 记录不全,默认排除"),
    Param("boards", "板块", "multiselect", ["主板", "创业板", "科创板", "北交所"],
          options=[opt("主板"), opt("创业板"), opt("科创板"), opt("北交所")], group=G_POOL),
    Param("min_list_days", "上市满", "int", 250, min=0, max=5000, unit="个交易日", group=G_POOL),
    Param("min_amount", "近 20 日日均成交额不低于", "float", 0.3, min=0, max=100, step=0.1, unit="亿元",
          group=G_POOL, help="文档:≥3000 万元"),
    Param("channels", "启用的通道", "multiselect", list(CHANNELS), group=G_POOL,
          options=[opt(c, f"{c} {CHANNEL_NAMES[c]}") for c in CHANNELS],
          help="关掉某条通道可以单独衡量其他通道的贡献"),
    # ---- 赔率门槛
    Param("odds_min", "结构赔率门槛", "float", 3.0, min=1, max=10, step=0.1, group=G_ODDS),
    Param("odds_min_event", "事件类结构赔率门槛", "float", 2.5, min=1, max=10, step=0.1, group=G_ODDS),
    Param("exp_min", "期望收益门槛", "float", 0.30, min=0, max=2, step=0.05, group=G_ODDS, help="按 12-18 个月计"),
    Param("downside_cap", "悲观下行上限", "float", 0.25, min=0.05, max=1, step=0.01, group=G_ODDS,
          help="在买入价处的下行幅度;设为 1 关闭"),
    Param("quick_odds_min", "速算赔率进观察池", "float", 2.5, min=1, max=10, step=0.1, group=G_ODDS),
    Param("quick_odds_priority", "速算赔率优先深研", "float", 4.0, min=1, max=20, step=0.1, group=G_ODDS),
    Param("min_downside", "下行幅度下限", "float", 0.10, min=0.02, max=0.5, step=0.01, group=G_ODDS,
          help="假设:现价已低于下行锚时,仍按至少这个幅度算下行,防止赔率被除以接近 0 的数放大"),
    Param("score_pass", "评分放行", "float", 70, min=0, max=100, group=G_ODDS),
    Param("score_high", "高确信评分", "float", 85, min=0, max=100, group=G_ODDS),
    Param("score_option", "期权仓评分下限", "float", 60, min=0, max=100, group=G_ODDS),
    Param("sens_anchor_shift", "敏感性:下行锚下压", "float", 0.10, min=0, max=0.5, step=0.01, group=G_ODDS),
    Param("sens_prob_shift", "敏感性:基准概率下调", "float", 0.10, min=0, max=0.5, step=0.01, group=G_ODDS),
    Param("sens_delay_months", "敏感性:催化剂推迟", "int", 6, min=0, max=24, unit="月", group=G_ODDS),
    Param("sens_min_annual", "推迟后年化收益不低于", "float", 0.15, min=0, max=1, step=0.01, group=G_ODDS,
          help="假设:文档只问“按年化还值不值得”,没给数"),
    # ---- 估值与概率
    Param("p_bear", "悲观概率", "float", 0.30, min=0, max=1, step=0.05, group=G_VAL, help="文档算例 30/50/20"),
    Param("p_base", "基准概率", "float", 0.50, min=0, max=1, step=0.05, group=G_VAL),
    Param("p_bull", "乐观概率", "float", 0.20, min=0, max=1, step=0.05, group=G_VAL),
    Param("bull_mult", "乐观价值 / 基准价值", "float", 1.4, min=1, max=3, step=0.05, group=G_VAL, help="算例一:20 / 14.4"),
    Param("pb_window_days", "PB 分位窗口", "int", 750, min=120, max=2500, unit="交易日", group=G_VAL),
    Param("pb_floor_pct", "PB 历史底部分位", "float", 0.10, min=0, max=0.5, step=0.01, group=G_VAL),
    Param("pb_center_min", "PB 中枢下限", "float", 0.9, min=0.3, max=3, step=0.1, group=G_VAL),
    Param("pb_center_cap", "PB 中枢上限", "float", 2.0, min=0.5, max=6, step=0.1, group=G_VAL),
    Param("bvps_haircut", "净资产打折", "float", 0.8, min=0.3, max=1, step=0.05, group=G_VAL,
          help="速算下行锚与 BVPS × 该系数比较,取更低者"),
    Param("pe_center", "正常化 PE 中枢", "float", 15.0, min=5, max=40, step=1, group=G_VAL),
    Param("event_age_days", "事件链最长有效期", "int", 365, min=30, max=1500, unit="天", group=G_VAL,
          help="假设:事件链第一条公告距今超过此天数,“事件前价格”已经过期,不再出事件卡"),
    Param("uplift_restructure", "重组成功涨幅", "float", 0.60, min=0, max=3, step=0.05, group=G_VAL,
          help="假设:相对事件前价格的成功价;文档要求用备考利润 × 可比 PE,这里用固定涨幅代替,待优化"),
    Param("uplift_ctrl", "控制权变更成功涨幅", "float", 0.50, min=0, max=3, step=0.05, group=G_VAL, help="假设"),
    Param("uplift_bankruptcy", "重整成功涨幅", "float", 0.80, min=0, max=3, step=0.05, group=G_VAL, help="假设"),
    Param("event_prob_scale", "事件概率整体缩放", "float", 1.0, min=0.3, max=1.3, step=0.05, group=G_VAL,
          help="用来校准文档 base rate(重组 65%-75% 等)"),
    # ---- 仓位与风控
    Param("risk_low", "低确信单笔风险预算", "float", 0.005, min=0, max=0.05, step=0.0005, group=G_SIZE),
    Param("risk_std", "标准单笔风险预算", "float", 0.010, min=0, max=0.05, step=0.0005, group=G_SIZE),
    Param("risk_high", "高确信单笔风险预算", "float", 0.015, min=0, max=0.05, step=0.0005, group=G_SIZE),
    Param("gap_main", "跳空缓冲:主板", "float", 0.05, min=0, max=0.5, step=0.01, group=G_SIZE),
    Param("gap_high", "跳空缓冲:ST/创业板/科创板", "float", 0.10, min=0, max=0.5, step=0.01, group=G_SIZE),
    Param("gap_bj_event", "跳空缓冲:北交所/二元事件", "float", 0.15, min=0, max=0.5, step=0.01, group=G_SIZE),
    Param("small_cap_mcap", "小盘股市值线", "float", 30.0, min=5, max=200, unit="亿元", group=G_SIZE,
          help="假设:低于此市值风险预算减半"),
    Param("max_single", "单票上限", "float", 0.10, min=0.01, max=0.5, step=0.01, group=G_SIZE),
    Param("max_single_hard", "单票上限(高确信且下行有硬托底)", "float", 0.12, min=0.01, max=0.5, step=0.01, group=G_SIZE),
    Param("max_cluster", "同一行业上限", "float", 0.25, min=0.05, max=1, step=0.01, group=G_SIZE),
    Param("max_event_bucket", "事件仓合计上限", "float", 0.30, min=0, max=1, step=0.01, group=G_SIZE),
    Param("max_option_single", "期权仓单只上限", "float", 0.01, min=0, max=0.1, step=0.005, group=G_SIZE),
    Param("max_option_total", "期权仓合计上限", "float", 0.05, min=0, max=0.2, step=0.005, group=G_SIZE),
    Param("max_total_risk", "总风险预算", "float", 0.08, min=0.01, max=0.5, step=0.005, group=G_SIZE),
    Param("max_positions", "持仓数量上限", "int", 15, min=1, max=50, unit="只", group=G_SIZE),
    Param("adv_mult", "单票市值不超过日均成交额的", "float", 1.0, min=0.1, max=10, step=0.1, unit="倍", group=G_SIZE,
          help="由“5 个交易日按日均成交额 10% 退出 50%”推出:仓位 ≤ 1 倍日均成交额"),
    Param("allow_option", "软否决项允许进期权仓", "bool", True, group=G_SIZE,
          help="文档:命中否决项放弃,或最多给 ≤1% 的期权仓。硬否决(造假/非标审计/退市/财务类退市指标/面值/市值)"
               "一律放弃;软否决(质押 >70%、退市风险警示)只能进期权仓"),
    Param("pledge_veto", "质押比例否决线", "float", 70.0, min=30, max=100, step=1, unit="%", group=G_SIZE),
    Param("bk_fail_mult", "重整失败价 / 事件前价格", "float", 0.7, min=0.1, max=1, step=0.05, group=G_VAL,
          help="假设:重整失败往往走向退市,落点压到事件前价格的 70%"),
    Param("week_new_risk", "每周新增风险预算上限", "float", 0.03, min=0, max=0.2, step=0.005, group=G_SIZE,
          help="文档 14.3 恐慌剧本:当周新增风险预算不超过 3%;这里对所有周一律生效"),
    # ---- 池子容量
    Param("deep_per_week", "每周深度研究(完整赔率卡)数量", "int", 5, min=1, max=50, unit="只", group=G_POOLS,
          help="文档:每周约半天深研。程序不受时间限制,这里仍然限制数量,让弹药池按文档节奏慢慢建起来"),
    Param("warmup_mult", "首次扫描深研数量倍数", "int", 4, min=1, max=20, group=G_POOLS,
          help="文档 21.1 的“建库”阶段:回测第一周一次性多做几张赔率卡,避免空仓等太久"),
    Param("card_cooldown_days", "赔率卡未通过后的冷却", "int", 28, min=0, max=180, unit="天", group=G_POOLS),
    Param("radar_cap", "雷达池上限", "int", 200, min=20, max=1000, unit="只", group=G_POOLS),
    Param("watch_cap", "观察池上限", "int", 60, min=5, max=300, unit="只", group=G_POOLS),
    Param("ammo_cap", "弹药池上限", "int", 15, min=1, max=100, unit="只", group=G_POOLS),
    Param("radar_expire_days", "雷达池过期", "int", 56, min=7, max=400, unit="天", group=G_POOLS, help="文档:8 周"),
    Param("watch_expire_days", "观察池过期", "int", 180, min=30, max=720, unit="天", group=G_POOLS, help="文档:6 个月没有新变化"),
    Param("ammo_far_days", "弹药池远离买入线降级", "int", 90, min=30, max=360, unit="天", group=G_POOLS),
    Param("ammo_max_gap", "入弹药池:现价高出买入线不超过", "float", 0.35, min=0.05, max=2, step=0.05, group=G_POOLS,
          help="假设:文档的弹药池是“等价格”的标的,离买入线太远的不占名额"),
    # ---- 建仓与退出
    Param("tranche1", "试错仓占目标", "float", 0.40, min=0.1, max=1, step=0.05, group=G_EXIT),
    Param("tranche2", "确认仓占目标", "float", 0.30, min=0, max=1, step=0.05, group=G_EXIT),
    Param("tp1", "第一止盈位(占基准价值)", "float", 0.70, min=0.3, max=1.5, step=0.05, group=G_EXIT),
    Param("tp1_cut", "第一止盈减仓比例", "float", 0.22, min=0, max=1, step=0.01, group=G_EXIT),
    Param("tp2", "第二止盈位(占基准价值)", "float", 1.00, min=0.5, max=2, step=0.05, group=G_EXIT),
    Param("tp2_cut", "第二止盈减仓比例", "float", 0.35, min=0, max=1, step=0.01, group=G_EXIT),
    Param("trail_dd", "乐观区间移动止盈回撤", "float", 0.22, min=0.05, max=0.6, step=0.01, group=G_EXIT),
    Param("time_stop_days", "时间止损", "int", 360, min=60, max=1000, unit="天", group=G_EXIT,
          help="假设:持有超过此天数仍未兑现,减半并重测赔率"),
    Param("falsify_wait_days", "证伪后观察期", "int", 2, min=0, max=10, unit="个交易日", group=G_EXIT,
          help="文档:立即减仓 ≥50%,48 小时内重估;仍未恢复则清仓"),
    Param("odds_cut", "当前赔率低于此减仓", "float", 2.0, min=0.5, max=5, step=0.1, group=G_EXIT),
    Param("odds_exit", "当前赔率低于此清仓", "float", 1.0, min=0, max=3, step=0.1, group=G_EXIT),
    # ---- 回撤熔断
    Param("dd1", "回撤熔断一级", "float", 0.08, min=0.02, max=0.5, step=0.01, group=G_BRK),
    Param("dd2", "回撤熔断二级", "float", 0.12, min=0.02, max=0.5, step=0.01, group=G_BRK),
    Param("dd3", "回撤熔断三级", "float", 0.18, min=0.02, max=0.5, step=0.01, group=G_BRK),
    Param("risk_dd2", "二级熔断后总风险预算", "float", 0.05, min=0.005, max=0.2, step=0.005, group=G_BRK),
    Param("freeze_days", "三级熔断冷静期", "int", 14, min=0, max=60, unit="天", group=G_BRK),
    # ---- 通道参数
    Param("event_lookback", "事件扫描回看", "int", 120, min=10, max=400, unit="天", group=G_CH),
    Param("min_evidence", "进弹药池所需独立 A/B 级证据数", "int", 2, min=1, max=5, group=G_POOLS,
          help="文档 4.1。审计财报里的资产托底(现价 ≤ 2 倍每股净资产)算一条 A 级证据"),
    Param("a2_mcap", "A2 壳特征:市值小于", "float", 50.0, min=5, max=500, unit="亿元", group=G_CH),
    Param("a2_debt", "A2 壳特征:资产负债率小于", "float", 0.5, min=0.1, max=1, step=0.05, group=G_CH),
    Param("b_loss_years", "B 困境:连续亏损年数", "int", 2, min=1, max=5, unit="年", group=G_CH),
    Param("c_pb_pct", "C 错杀:PB 分位小于", "float", 0.10, min=0.01, max=0.5, step=0.01, group=G_CH),
    Param("c_drop", "C 错杀:财报后跌幅超过", "float", 0.20, min=0.05, max=0.6, step=0.01, group=G_CH),
    Param("d_netcash", "D 重估:净现金占市值超过", "float", 0.5, min=0.1, max=2, step=0.05, group=G_CH,
          help="文档 70% 含金融资产和持有的上市公司股权;这里只有货币资金减总负债,口径更保守,所以取 50%"),
    Param("e_holder_pct", "E 增持:占总股本不低于", "float", 0.5, min=0.05, max=10, step=0.05, unit="%", group=G_CH),
    Param("e_buyback_pct", "E 回购:计划占总股本不低于", "float", 1.0, min=0.1, max=10, step=0.1, unit="%", group=G_CH),
    Param("f_min_stocks", "F 行业拐点:行业最少公司数", "int", 15, min=5, max=200, group=G_CH),
]

DEFAULTS = {p.key: p.default for p in SPEC}


def resolve(params: dict | None = None) -> SimpleNamespace:
    d = dict(DEFAULTS)
    d.update({k: v for k, v in (params or {}).items() if k in d})
    tot = d["p_bear"] + d["p_base"] + d["p_bull"]
    if tot > 0 and abs(tot - 1) > 1e-9:  # keep the three scenarios a proper distribution
        d["p_bear"], d["p_base"], d["p_bull"] = (d[k] / tot for k in ("p_bear", "p_base", "p_bull"))
    return SimpleNamespace(**d)
