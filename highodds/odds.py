"""赔率测算(文档 Part C):速算、三情景赔率卡、买入线、敏感性三问、评分卡。

全部是不依赖数据源的纯函数,方便单测和在页面里单独调用。价格一律是"今天的股本口径"下的每股价格。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class OddsCard:
    """一张赔率卡(文档附录 D 第四节)。"""
    price: float
    bear: float          # 悲观价值 = 证伪价 D
    base: float          # 基准价值 T
    bull: float          # 乐观价值
    p_bear: float
    p_base: float
    p_bull: float
    binary: bool = False  # 二元事件:只有 失败价(bear) 和 成功价(base)
    method: str = ""      # 估值方法说明
    anchor: str = ""      # 下行锚类型: net_asset / valuation_floor / pre_event / net_cash
    min_down: float = 0.10

    # ---- 派生指标 ----------------------------------------------------------------
    @property
    def D(self) -> float:
        return self.bear

    @property
    def T(self) -> float:
        return self.base

    def ret(self, v: float, price: float | None = None) -> float:
        p = price or self.price
        return v / p - 1

    def downside(self, price: float | None = None) -> float:
        """证伪下行幅度,带下限(现价已经低于下行锚时不让分母趋近 0)。"""
        p = price or self.price
        return max(1 - self.bear / p, self.min_down)

    def upside(self, price: float | None = None) -> float:
        p = price or self.price
        return self.base / p - 1

    def odds(self, price: float | None = None) -> float:
        """结构赔率 = (T - P) / (P - D)。"""
        d = self.downside(price)
        return self.upside(price) / d if d > 0 else float("inf")

    def expected(self, price: float | None = None) -> float:
        p = price or self.price
        return (self.p_bear * (self.bear / p - 1) + self.p_base * (self.base / p - 1)
                + self.p_bull * (self.bull / p - 1))

    def buy_line(self, R: float) -> float:
        """赔率买入线 = (T + R × D) / (1 + R)。

        文档公式默认下行有意义。证伪价 D 贴近现价时下行会被 ``min_down`` 托住,
        此时买入线要和 odds() 的口径一致:(T - P) = R × P × min_down → P = T / (1 + R × min_down)。
        两者取更低的一个。"""
        return min((self.base + R * self.bear) / (1 + R), self.base / (1 + R * self.min_down))

    def entry_limit(self, R: float, exp_min: float, down_cap: float) -> float:
        """同时满足三个门槛(赔率 ≥ R、期望收益 ≥ exp_min、下行 ≤ down_cap)的最高买入价。

        三个条件都随价格下降而变好,所以取三个上限里最低的一个。
        文档的赔率买入线只管第一个条件,这里把期望和下行一起放进来,买入线才是"真能买"的价格。"""
        ev = self.p_bear * self.bear + self.p_base * self.base + self.p_bull * self.bull
        lim = [self.buy_line(R), ev / (1 + exp_min)]
        if down_cap < 1:
            lim.append(self.bear / (1 - down_cap))
        return min(lim)

    def breakeven_p(self, price: float | None = None) -> float | None:
        """二元事件的盈亏平衡概率 = (P - 失败价) / (成功价 - 失败价)。"""
        if not self.binary:
            return None
        p = price or self.price
        span = self.base - self.bear
        return (p - self.bear) / span if span > 0 else None

    def valid(self) -> bool:
        return (self.price > 0 and self.bear > 0 and self.base > self.bear and self.bull >= self.base
                and abs(self.p_bear + self.p_base + self.p_bull - 1) < 1e-6)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(odds=round(self.odds(), 3), expected=round(self.expected(), 4),
                 upside=round(self.upside(), 4), downside=round(self.downside(), 4))
        return d


def quick_anchor(floor_price: float | None, bvps: float | None, pre_event: float | None = None,
                 haircut: float = 0.8) -> float | None:
    """5 分钟速算的下行锚(文档 11.1):
    max(BVPS × 历史最低 PB, 事件公告前价格),再与 BVPS × 0.8 比较取更低值。
    floor_price 就是 BVPS × 历史最低 PB 折算到今天的每股价格。"""
    cands = [x for x in (floor_price, pre_event) if x is not None and x > 0]
    a = max(cands) if cands else None
    if bvps is not None and bvps > 0:
        a = min(a, bvps * haircut) if a is not None else bvps * haircut
    return a


def quick_odds(base_value: float, price: float, anchor: float, min_down: float = 0.10) -> tuple[float, float, float]:
    """(上行, 下行, 速算赔率)。速算只用来排序和淘汰,不能作为买入依据。"""
    up = base_value / price - 1
    down = max(1 - anchor / price, min_down)
    return up, down, (up / down if down > 0 else float("inf"))


def binary_card(price: float, fail: float, success: float, p_success: float, min_down: float = 0.10,
                method: str = "二元事件", anchor: str = "pre_event") -> OddsCard:
    p = min(max(p_success, 0.0), 1.0)
    return OddsCard(price=price, bear=fail, base=success, bull=success, p_bear=1 - p, p_base=p, p_bull=0.0,
                    binary=True, method=method, anchor=anchor, min_down=min_down)


def scenario_card(price: float, bear: float, base: float, bull: float, probs: tuple[float, float, float],
                  min_down: float = 0.10, method: str = "", anchor: str = "") -> OddsCard:
    pb, pm, pu = probs
    s = pb + pm + pu
    return OddsCard(price=price, bear=bear, base=base, bull=max(bull, base), p_bear=pb / s, p_base=pm / s,
                    p_bull=pu / s, method=method, anchor=anchor, min_down=min_down)


def passes(card: OddsCard, cfg, event: bool | None = None) -> tuple[bool, list[str]]:
    """三个门槛:结构赔率、期望收益、悲观下行。返回 (是否通过, 未通过原因)。"""
    ev = card.binary if event is None else event
    need = cfg.odds_min_event if ev else cfg.odds_min
    why = []
    if card.odds() < need:
        why.append(f"赔率 {card.odds():.2f} < {need}")
    if card.expected() < cfg.exp_min:
        why.append(f"期望 {card.expected():.0%} < {cfg.exp_min:.0%}")
    if card.downside() > cfg.downside_cap:
        why.append(f"下行 {card.downside():.0%} > {cfg.downside_cap:.0%}")
    return not why, why


# ---- 敏感性三问(文档 11.2) ------------------------------------------------------------
def sensitivity(card: OddsCard, cfg, horizon_months: float = 12.0) -> dict:
    """三问都过:正常仓位;过两问:仓位减半;其余放弃。"""
    P = card.price
    # 1) 下行锚往下压一档,赔率还 >= 2 吗
    shifted = OddsCard(**{**asdict(card), "bear": card.bear * (1 - cfg.sens_anchor_shift)})
    q1 = shifted.odds() >= 2.0
    # 2) 基准情景概率下调,期望收益还 > 0 吗(让出来的概率给悲观情景)
    shift = min(cfg.sens_prob_shift, card.p_base)
    worse = OddsCard(**{**asdict(card), "p_base": card.p_base - shift, "p_bear": card.p_bear + shift})
    q2 = worse.expected() > 0
    # 3) 催化剂推迟,按年化收益还值得吗
    e = card.expected()
    months = horizon_months + cfg.sens_delay_months
    annual = (1 + e) ** (12.0 / months) - 1 if e > -1 else -1.0
    q3 = annual >= cfg.sens_min_annual
    n = int(q1) + int(q2) + int(q3)
    return {"q1_anchor_down": q1, "q2_prob_down": q2, "q3_delay": q3, "passed": n,
            "size_mult": 1.0 if n == 3 else (0.5 if n == 2 else 0.0),
            "odds_shifted": round(shifted.odds(), 2), "exp_worse": round(worse.expected(), 3),
            "annual_delayed": round(annual, 3)}


# ---- 评分卡(文档附录 E) -----------------------------------------------------------------
SCORE_DIMS = [("edge", 25, "预期差大小"), ("odds", 25, "赔率与期望"), ("floor", 15, "下行托底"),
              ("catalyst", 10, "催化剂与时间"), ("falsify", 10, "可证伪度"), ("liquidity", 5, "流动性"),
              ("crowding", 5, "拥挤度(反向)"), ("info", 5, "信息质量")]


@dataclass
class ScoreInputs:
    edge: float                 # (T - P) / P
    odds: float
    expected: float
    floor_kind: str             # hard / valuation / none
    catalyst_months: float | None  # 催化剂预计多久内出现;None = 没有
    falsify_count: int          # 可观察的证伪条件个数
    amount20_yi: float          # 近 20 日日均成交额(亿元)
    ret60: float | None         # 近 60 日涨幅,拥挤度代理
    evidence_ab: int            # 独立 A/B 级证据条数
    verifiable: bool = True     # 预期差是否可验证


def score_card(x: ScoreInputs) -> dict:
    # 预期差:差异 > 30% 且可验证 25;10%-30% 15;< 10% 5。不可验证降一档。
    edge = 25 if x.edge > 0.30 else (15 if x.edge >= 0.10 else 5)
    if not x.verifiable:
        edge = {25: 15, 15: 5, 5: 0}[edge]
    if x.odds >= 4 and x.expected >= 0.5:
        od = 25
    elif x.odds >= 3:
        od = 18
    elif x.odds >= 2:
        od = 10
    else:
        od = 0
    floor = {"hard": 15, "valuation": 8}.get(x.floor_kind, 3)
    if x.catalyst_months is None:
        cat = 0
    else:
        cat = 10 if x.catalyst_months <= 12 else (5 if x.catalyst_months <= 18 else 0)
    fal = 10 if x.falsify_count >= 2 else (5 if x.falsify_count == 1 else 0)
    liq = 5 if x.amount20_yi >= 1.0 else (3 if x.amount20_yi >= 0.3 else 0)
    crowd = 5 if (x.ret60 is None or x.ret60 < 0.20) else (3 if x.ret60 < 0.50 else 0)
    info = 5 if x.evidence_ab >= 2 else (2 if x.evidence_ab == 1 else 0)
    parts = {"edge": edge, "odds": od, "floor": floor, "catalyst": cat, "falsify": fal, "liquidity": liq,
             "crowding": crowd, "info": info}
    parts["total"] = sum(parts.values())
    return parts


def conviction(score: float, cfg) -> str:
    if score >= cfg.score_high:
        return "high"
    if score >= cfg.score_pass:
        return "standard"
    if score >= cfg.score_option:
        return "low"
    return "none"


# ---- 事件概率(文档第 8 节 base rate) ----------------------------------------------------
# 从该阶段走到"完成"的概率。阶段越靠后概率越高;起点取文档给的区间中值,再乘 event_prob_scale。
STAGE_PROB = {
    "restructure_plan": 0.45, "restructure_progress": 0.45, "restructure_misc": 0.45,
    "restructure_draft": 0.70, "restructure_inquiry": 0.68, "restructure_accepted": 0.85,
    "restructure_approved": 0.93,
    "ctrl_change": 0.70, "ctrl_change_done": 0.30,      # 已完成变更后"24 个月内注入资产"20%-35%
    "bankruptcy_pre": 0.40, "bankruptcy_investor": 0.775, "bankruptcy_plan": 0.90,
}
STAGE_ORDER = ["restructure_plan", "restructure_progress", "restructure_misc", "restructure_draft",
               "restructure_inquiry", "restructure_accepted", "restructure_approved"]
EVENT_FAMILY = {
    "restructure": {"restructure_plan", "restructure_progress", "restructure_misc", "restructure_draft",
                    "restructure_inquiry", "restructure_accepted", "restructure_approved"},
    "ctrl": {"ctrl_change", "ctrl_change_done"},
    "bankruptcy": {"bankruptcy_pre", "bankruptcy_investor", "bankruptcy_plan"},
}
FAMILY_TERMINATE = {"restructure": "restructure_terminate", "ctrl": "ctrl_change_terminate"}


def family_of(tag: str) -> str | None:
    for f, tags in EVENT_FAMILY.items():
        if tag in tags:
            return f
    return None


def stage_prob(tag: str, scale: float = 1.0) -> float:
    return min(0.95, STAGE_PROB.get(tag, 0.4) * scale)
