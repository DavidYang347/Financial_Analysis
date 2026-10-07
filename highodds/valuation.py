"""把一只股票的信号变成赔率卡(文档第 8、9 节)。

价格全部是 hfq 口径(见 features.py)。两种卡:

* 事件卡(二元):有仍在推进的重组 / 控制权变更 / 重整链。
    失败价 = 事件链第一条公告之前的价格(重整再打折),成功价 = 事件前价格 × (1 + 成功涨幅)。
    成功概率 = 所处阶段的 base rate × event_prob_scale。
* 估值卡(三情景):其余所有信号。三个情景用同一套方法(PB 法),不允许各用各的。
    悲观 = min(BVPS × 历史底部 PB, BVPS × 0.8),D 通道再与净现金比较取高者;
    基准 = BVPS × 中枢 PB(困境 / 周期通道再与"正常化 EPS × 中枢 PE"取平均);
    乐观 = 基准 × bull_mult。

未做(需要人工研究或付费数据):备考利润 × 可比 PE、分部估值 SOTP、按行业周期中枢盈利估值。
这些是后续优化的主要方向,见 README。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from highodds import odds as O

MOTIF = {"A": "并购重组/控制权", "B": "困境反转", "C": "错杀", "D": "资产重估", "E": "内部人行为", "F": "周期拐点"}
# 有"变化"的子信号;C2(估值底部)和 D1(净现金)只是便宜,不算变化。
CHANGE_SUBS = {"A1", "A2", "B1", "B2", "B3", "C1", "C3", "C4", "D2", "E1", "E2", "F1"}
CATALYST_MONTHS = {"A1": 6, "A2": 12, "B1": 9, "B2": 12, "B3": 9, "C1": 9, "C3": 9, "C4": 12, "D2": 9, "E1": 12,
                   "E2": 12, "F1": 12}
PRIORITY = ["A", "B", "F", "D", "C", "E"]


@dataclass
class CardResult:
    card: O.OddsCard
    kind: str                    # event | value
    channel: str                 # 主通道
    motif: str
    subs: list[str]
    evidence_ab: int
    catalyst_months: float | None
    floor_kind: str              # hard | valuation | none
    binary: bool
    family: str | None = None
    tag: str | None = None
    chain_first: pd.Timestamp | None = None
    thesis: str = ""
    score: dict = field(default_factory=dict)
    sens: dict = field(default_factory=dict)
    conviction: str = "none"
    limit: float = 0.0           # 同时满足三个门槛的最高买入价(hfq)
    has_change: bool = False
    pass_thresholds: bool = False
    fail_reasons: list = field(default_factory=list)

    def summary(self) -> dict:
        c = self.card
        return {"kind": self.kind, "channel": self.channel, "motif": self.motif, "subs": ",".join(self.subs),
                "binary": self.binary, "tag": self.tag or "", "thesis": self.thesis,
                "price": round(c.price, 4), "bear": round(c.bear, 4), "base": round(c.base, 4),
                "bull": round(c.bull, 4), "p_bear": round(c.p_bear, 3), "p_base": round(c.p_base, 3),
                "p_bull": round(c.p_bull, 3), "odds": round(c.odds(), 2), "expected": round(c.expected(), 3),
                "downside": round(c.downside(), 3), "limit": round(self.limit, 4),
                "score": self.score.get("total"), "conviction": self.conviction,
                "sens_passed": self.sens.get("passed"), "evidence_ab": self.evidence_ab,
                "method": c.method, "pass": self.pass_thresholds, "why_not": ";".join(self.fail_reasons)}


def _finite(x) -> bool:
    """x 是有限数字。None / NaN / pd.NA / 字符串都返回 False。"""
    try:
        return bool(np.isfinite(float(x)))
    except (TypeError, ValueError):
        return False


def primary_channel(subs_channels: set[str]) -> str:
    for c in PRIORITY:
        if c in subs_channels:
            return c
    return "C"


def scenario_probs(result_kind: str, channel: str, r, cfg) -> tuple[float, float, float]:
    """估值卡三情景概率。默认 30/50/20(文档算例);困境反转且经营现金流已转正时基准概率上调 5 个点(文档 8 节 base rate)。"""
    pb, pm, pu = cfg.p_bear, cfg.p_base, cfg.p_bull
    if channel == "B" and bool(r.get("ocf_pos2", False)):
        shift = min(0.05, pb)
        pb, pm = pb - shift, pm + shift
    return pb, pm, pu


def build_card(sym: str, r: pd.Series, sigs: list[dict], chain_live: dict | None, chain_fam: str | None,
               pre_px: float | None, cfg, day=None) -> CardResult | None:
    px, md = float(r["px"]), cfg.min_downside
    if not _finite(px) or px <= 0:
        return None
    channels = {s["channel"] for s in sigs}
    subs = sorted({s["sub"] for s in sigs})
    ab = {s["sub"] for s in sigs if s["grade"] in ("A", "B")}
    bv0 = r.get("bvps")
    if _finite(bv0) and bv0 > 0 and px <= 2.0 * bv0:
        ab.add("asset_floor")  # 审计财报里的净资产给下行托底:A 级事实
    main = primary_channel(channels)
    has_change = any(s in CHANGE_SUBS for s in subs)
    cats = [CATALYST_MONTHS[s] for s in subs if s in CATALYST_MONTHS]
    catalyst = min(cats) if cats else None

    # ---------------- 事件卡 ----------------
    if chain_live is not None and chain_fam is not None and _finite(pre_px) and pre_px > 0:
        tag = chain_live["tag"]
        up = {"restructure": cfg.uplift_restructure, "ctrl": cfg.uplift_ctrl,
              "bankruptcy": cfg.uplift_bankruptcy}[chain_fam]
        success = pre_px * (1 + up)
        fail = pre_px * (cfg.bk_fail_mult if chain_fam == "bankruptcy" else 1.0)
        # 失败落点只在现价高于它时才是有效的下行锚。现价已经跌到事件前价格以下,说明市场不信这个事件
        # (或者事件链太老,事件前价格已经过期),这时不能用 min_downside 把下行托住再去算一个漂亮的赔率。
        if px <= fail:
            return None
        if day is not None and chain_live["first"] < pd.Timestamp(day) - pd.Timedelta(days=cfg.event_age_days):
            return None
        if success <= px * 1.05:  # 利好已经在价格里
            return None
        p = O.stage_prob(tag, cfg.event_prob_scale)
        card = O.binary_card(px, fail, success, p, md, method=f"二元事件:{tag}", anchor="pre_event")
        label = {"restructure": "重组", "ctrl": "控制权变更", "bankruptcy": "重整"}[chain_fam]
        return CardResult(card=card, kind="event", channel="A", motif=MOTIF["A"], subs=subs,
                          evidence_ab=max(len(ab), 1), catalyst_months=6.0, floor_kind="hard", binary=True,
                          family=chain_fam, tag=tag, chain_first=chain_live["first"], has_change=True,
                          thesis=f"{label}处于「{tag}」阶段,市场按事件前价格 {pre_px:.2f}(hfq)之上的定价与成功概率 {p:.0%} 不匹配")

    # ---------------- 估值卡 ----------------
    bv = r.get("bvps")
    if not _finite(bv) or bv <= 0:
        return None
    floor_px = bv * r["pb_floor"] if _finite(r.get("pb_floor")) else None
    anchor = O.quick_anchor(floor_px, bv, None, cfg.bvps_haircut)
    floor_kind = "valuation"
    ncps = r.get("netcash_ps")
    if "D" in channels and _finite(ncps) and ncps > 0:
        anchor = max(anchor, 0.9 * ncps)   # 净现金已扣完全部负债,是硬托底
        floor_kind = "hard"
    if not _finite(r.get("pb_center")):
        return None
    center = float(np.clip(r["pb_center"], cfg.pb_center_min, cfg.pb_center_cap))
    base_pb = bv * center
    base, method = base_pb, f"PB 法:BVPS × 中枢 PB {center:.2f}"
    en = r.get("eps_norm")
    if main in ("B", "F") and _finite(en) and en > 0:
        base = 0.5 * (base_pb + en * cfg.pe_center)
        method += f";正常化 EPS {en:.2f} × PE {cfg.pe_center:g} 取平均"
    base = min(base, px * 3.0)
    bear = min(anchor, px * (1 - md))
    if base <= px * 1.02 or bear <= 0 or base <= bear:
        return None
    probs = scenario_probs("value", main, r, cfg)
    card = O.scenario_card(px, bear, base, base * cfg.bull_mult, probs, md, method=method,
                           anchor="net_cash" if floor_kind == "hard" else "valuation_floor")
    best = next((s for s in sigs if s["sub"] in CHANGE_SUBS), sigs[0])
    return CardResult(card=card, kind="value", channel=main, motif=MOTIF[main], subs=subs,
                      evidence_ab=len(ab), catalyst_months=float(catalyst) if catalyst else None,
                      floor_kind=floor_kind, binary=False, has_change=has_change,
                      thesis=f"{best['text']};PB {r['pb']:.2f}(历史 {r['pb_pct']:.0%} 分位)")


def finish_card(res: CardResult, r: pd.Series, cfg) -> CardResult:
    """补齐买入价上限、评分、敏感性、门槛检查。

    评分和敏感性都在"买入线价位"上算:弹药池里的股票是等价格的,真正下单时的赔率就是买入线处的赔率。
    用今天的价格算会把"好公司但现在还贵"的标的全部打成低分,弹药池就建不起来。"""
    c = res.card
    R = cfg.odds_min_event if res.binary else cfg.odds_min
    res.limit = c.entry_limit(R, cfg.exp_min, cfg.downside_cap)
    at = replace(c, price=res.limit) if res.limit > 0 else c
    ret60 = r.get("ret60")
    res.score = O.score_card(O.ScoreInputs(
        edge=at.upside(), odds=at.odds(), expected=at.expected(), floor_kind=res.floor_kind,
        catalyst_months=res.catalyst_months, falsify_count=2 if res.has_change else 1,
        amount20_yi=float(r.get("amount20")) if _finite(r.get("amount20")) else 0.0,
        ret60=None if not _finite(ret60) else float(ret60),
        evidence_ab=res.evidence_ab, verifiable=res.has_change))
    res.conviction = O.conviction(res.score["total"], cfg)
    res.sens = O.sensitivity(at, cfg)
    res.pass_thresholds, res.fail_reasons = O.passes(at, cfg)
    return res


def entry_checklist(res: CardResult, px: float, soft_veto: str, cfg, cooled: bool) -> list[str]:
    """入场检查清单(文档附录 D)。返回答案为"否"的项;两项以上为否,不建正式仓。"""
    no = []
    if not res.has_change:
        no.append("没有可观察的变化")
    if res.evidence_ab < 2:
        no.append("独立 A/B 级证据不足 2 条")
    if soft_veto:
        no.append("生存性:" + soft_veto.rstrip(";"))
    if px > res.limit:
        no.append("价格高于买入线")
    if res.sens.get("passed", 0) < 2:
        no.append("敏感性三问通过不足两问")
    if res.score.get("total", 0) < cfg.score_pass:
        no.append("评分不足")
    if not cooled:
        no.append("冷静期不足 24 小时")
    return no
