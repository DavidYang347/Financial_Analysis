"""附录 E scorecard, evidence count and the 入场检查清单 (附录 D).

The scorecard needs judgement in places (预期差, 可证伪度, 拥挤度); each
dimension below states the proxy used so it can be replaced later.
"""
from __future__ import annotations

import math

import pandas as pd

from oddsbook.odds import EVENT_THEMES


def evidence(card: dict, row: pd.Series) -> tuple[int, list[str]]:
    """Independent A/B evidence: distinct A/B channel signals + one audited fact that supports the theme."""
    n = int(card.get("n_ab", 0))
    facts = []
    th = card["theme"]
    if th == "distress" and (row.get("np_q", 0) > 0 or bool(row.get("ocf_q_pos2"))):
        facts.append("财报：单季盈利或经营现金流转正（A）")
    elif th == "revalue" and row.get("net_cash", 0) > 0:
        facts.append("财报：净现金为正（A）")
    elif th == "cycle" and row.get("debt_ratio", 100) < 50:
        facts.append("财报：资产负债表强于行业（A）")
    elif th in EVENT_THEMES and row.get("debt_ratio", 100) < 60 and row.get("equity", 0) > 0:
        facts.append("财报：负债可控、净资产为正，失败落点有托底（A）")
    elif th == "other" and row.get("ocf_ttm", 0) > 0 and row.get("np_ttm", 0) > 0:
        facts.append("财报：盈利且经营现金流为正（A）")
    return n + len(facts), facts


def scorecard(card: dict, row: pd.Series, n_ev: int, p: dict) -> dict:
    # Judged at the entry price (min of today's price and the buy line), like the gates in odds.py.
    e = card["entry_h"]
    up = card["target_h"] / e - 1 if e > 0 else card["upside"]
    odds, E = card["odds_entry"], card["expected_entry"]
    s = {}
    # 预期差 25 (proxy: upside to the base value, verifiable = backed by a real change)
    verifiable = card["has_change"] and n_ev >= 1
    s["s_gap"] = 25 if (up > 0.30 and verifiable) else 15 if up > 0.10 else 5
    # 赔率与期望 25
    if math.isfinite(odds) and odds >= 4 and E >= 0.5:
        s["s_odds"] = 25
    elif math.isfinite(odds) and odds >= 3:
        s["s_odds"] = 18
    elif math.isfinite(odds) and odds >= 2:
        s["s_odds"] = 10
    else:
        s["s_odds"] = 0
    # 下行托底 15
    s["s_floor"] = {"hard": 15, "valuation": 8}.get(card["support"], 3)
    # 催化剂与时间 10
    months = (card["catalyst_deadline"] - card["date"]).days / 30.4
    s["s_catalyst"] = 0 if not card["has_change"] else 10 if months <= 12 else 5 if months <= 18 else 0
    # 可证伪度 10 (events and turnarounds have explicit, dated falsifiers)
    s["s_falsify"] = 10 if card["theme"] in EVENT_THEMES | {"distress"} else 5 if card["has_change"] else 0
    # 流动性 5
    adv = row.get("amount20", 0) or 0
    s["s_liquidity"] = 5 if adv >= 1e8 else 3 if adv >= p["min_amount"] * 1e8 else 0
    # 拥挤度（反向）5: recent run-up and turnover
    r60, to = row.get("ret60", 0) or 0, row.get("turnover20", 0) or 0
    s["s_crowd"] = 0 if (r60 > 0.5 or to > 10) else 3 if (r60 > 0.25 or to > 5) else 5
    # 信息质量 5
    s["s_info"] = 5 if n_ev >= 2 else 2 if n_ev == 1 else 0
    s["score"] = sum(s.values())
    return s


def checklist(card: dict, row: pd.Series, n_ev: int, score: int, vetoed: str, p: dict) -> tuple[int, str]:
    """Number of "否" answers and which ones. Sizing / alerts / cooling are enforced by the book."""
    focus = [x.strip() for x in str(p.get("focus_industries", "")).replace("，", ",").split(",") if x.strip()]
    in_circle = (not focus) or card["theme"] in ("ma", "control", "bankruptcy", "distress") or \
        any(f in str(row.get("industry", "")) for f in focus)
    checks = {
        "说得清市场错在哪": card["has_change"] and math.isfinite(card["target_h"]),
        "在能力圈内": in_circle,
        "有可观察的变化": card["has_change"],
        "≥2 条独立 A/B 证据": n_ev >= p["min_ab_evidence"],
        "通过生存性检验": vetoed == "",
        "赔率与期望达标": bool(card["qualified"]),
        "敏感性至少过两问": card["sens_passes"] >= 2,
        "评分达标且无一票否决": score >= p["score_min"] and vetoed == "",
    }
    fails = [k for k, ok in checks.items() if not ok]
    return len(fails), "；".join(fails)


def grade(card: dict, row: pd.Series, veto_row, p: dict) -> dict:
    """Adds evidence, score, checklist and tier to ``card`` (in place) and returns it."""
    n_ev, facts = evidence(card, row)
    s = scorecard(card, row, n_ev, p)
    vet = "hard" if veto_row is not None and veto_row["hard"] else "soft" if veto_row is not None and veto_row["soft"] else ""
    nfail, fails = checklist(card, row, n_ev, s["score"], vet, p)
    card.update(s)
    card.update(n_evidence=n_ev, facts="；".join(facts), veto=vet,
                veto_reasons=veto_row["reasons"] if veto_row is not None else "",
                checklist_fails=nfail, checklist_fail_items=fails)
    option_like = vet == "soft" or card["theme"] == "bankruptcy" or "b_st_path" in card["codes"]
    if option_like:
        card["tier"] = "option"
    elif card["is_event"]:
        card["tier"] = "event"
    else:
        card["tier"] = "core" if s["score"] >= 80 else "standard"
    return card
