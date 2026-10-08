"""Odds card (v3 Part C): 8-step method, 5-minute quick odds and the sensitivity test.

All prices are on the hfq basis (see features.py), so a card written today
can be compared with the price on any later day.

Step by step (§8):

1. scenarios      pessimistic = thesis falsified, base, optimistic
2. method         chosen by theme (§9), the same method for all three scenarios
3. value          base = normalised EPS x mid PE, or BVPS x mid PB, or event success price
4. downside anchor the most conservative of: BVPS x historical min PB, tangible BVPS x 0.8,
                  pre-event price, earnings-downgrade value (EPS x low PE).
                  If the price is already below the anchor, the anchor is no
                  longer a floor ("假赔率" guard): downside = ``broken_downside``.
5. probability    theme base rate + evidence bonuses; events = product of node probabilities
6. metrics        expected return, structural odds, breakeven probability
7. buy line       (T + R x D) / (1 + R)
8. sensitivity    anchor one notch lower, probability -10pp, catalyst 6 months late

Simplifications that free data forces (write them on the card, revisit later):
no consensus estimates (the "market model" is today's price), no pro-forma
numbers for deals (success value = pre-event price x uplift), no SOTP of held
stakes (net cash only).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from oddsbook.config import floats

THEMES = {
    "distress": "困境反转", "ma": "并购重组", "control": "控制权变更", "bankruptcy": "破产重整",
    "cycle": "周期拐点", "revalue": "资产重估", "other": "错杀 / 内部人 / 其他",
}
EVENT_THEMES = {"ma", "control", "bankruptcy"}
HARD_FLOOR_ANCHORS = {"净资产打折", "事件前价格", "净现金", "历史最低 PB"}


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return math.nan
    return v if math.isfinite(v) else math.nan


def mid_pe(row, p) -> float:
    pe = _f(row.get("ind_pe"))
    if not math.isfinite(pe):
        pe = 15.0
    return float(np.clip(pe, p["pe_mid_lo"], p["pe_mid_hi"]))


def normalized_eps_h(row) -> float:
    """Normalised EPS (hfq basis): mean of profitable years in the last five, else positive TTM.

    A one-off turnaround is not normalised profit (§9 陷阱): when only one of
    the last five years was profitable, use the lower of that year and TTM.
    """
    sh = _f(row.get("shares"))
    fp = _f(row.get("f_period")) or 1.0
    if not (sh > 0):
        return math.nan
    hist = _f(row.get("norm_np_hist"))
    ttm = _f(row.get("np_ttm"))
    n = int(row.get("pos_years") or 0)
    if n >= 2 and hist > 0:
        np_norm = hist
    elif n == 1 and hist > 0:
        np_norm = min(hist, ttm) if ttm > 0 else hist * 0.7
    elif ttm > 0:
        np_norm = ttm
    else:
        return math.nan
    return np_norm / sh * fp


def pre_event_price(close_h: pd.DataFrame, sym: str, anchor_date) -> float:
    """hfq close on the last trading day before ``anchor_date`` (inside the loaded window)."""
    if anchor_date is None or pd.isna(anchor_date) or sym not in close_h.columns:
        return math.nan
    s = close_h[sym].dropna()
    s = s[s.index < pd.Timestamp(anchor_date)]
    return float(s.iloc[-1]) if len(s) else math.nan


def event_probability(theme: str, codes: str, p: dict) -> tuple[float, str]:
    nodes = floats(p["p_ma_node"]) or [0.9, 0.95, 0.85, 0.95]
    if theme == "ma":
        if "a1_ma_node" in codes:
            prob, why = float(np.prod(nodes[1:])), "节点推进：剩余 " + "×".join(f"{x:.2f}" for x in nodes[1:])
        elif "a1_ma_draft" in codes:
            prob, why = float(np.prod(nodes)), "草案：" + "×".join(f"{x:.2f}" for x in nodes)
        else:
            prob, why = float(np.prod(nodes)) * 0.85, "预案阶段：节点概率 × 0.85（预案到草案）"
        return prob, why
    if theme == "control":
        return p["p_control_inject"], "控制权变更后 24 个月内注入资产 base rate"
    if theme == "bankruptcy":
        return p["p_bankruptcy"], "重整获批 base rate（已有投资人）"
    return math.nan, ""


def build_card(sym: str, row: pd.Series, sig: pd.Series, close_h: pd.DataFrame, today, p: dict,
               theme: str | None = None) -> dict:
    """Full odds card for one symbol. ``row``: snapshot row; ``sig``: channels.summarize row."""
    theme = theme or sig["theme"]
    P = _f(row["close_h"])
    bvps = _f(row.get("bvps_h"))
    tbvps = _f(row.get("tbvps_h"))
    pb_min, pb_med = _f(row.get("pb_min")), _f(row.get("pb_med"))
    eps_n = normalized_eps_h(row)
    eps_ttm = _f(row.get("eps_ttm_h"))
    pe_mid = mid_pe(row, p)
    sh = _f(row.get("shares"))
    fp = _f(row.get("f_period")) or 1.0
    netcash_ps = _f(row.get("net_cash")) / sh * fp if sh > 0 else math.nan
    is_event = theme in EVENT_THEMES
    notes: list[str] = []

    # ---- step 3: base value T, method ---------------------------------------------
    pre = pre_event_price(close_h, sym, sig.get("anchor_date"))
    if theme == "revalue":
        method = "净现金 + 主业 × 中枢 PE（SOTP 简化）"
        core = max(eps_n, 0) if math.isfinite(eps_n) else 0.0
        T = (netcash_ps if math.isfinite(netcash_ps) else 0.0) + core * pe_mid
    elif theme == "cycle":
        method = "BVPS × 历史中枢 PB"
        T = bvps * pb_med if bvps > 0 and pb_med > 0 else math.nan
        if math.isfinite(eps_n):
            T = np.nanmin([T, eps_n * pe_mid * 1.2]) if math.isfinite(T) else eps_n * pe_mid
    elif is_event:
        upl = p["event_uplift_control"] if theme == "control" else p["event_uplift_ma"]
        method = f"二元事件：成功价 = 事件前价格 × {1 + upl:.2f}"
        T = pre * (1 + upl) if math.isfinite(pre) else math.nan
        if math.isfinite(eps_n):  # never value the success case below the standalone business
            T = np.nanmax([T, eps_n * pe_mid])
    else:
        method = "正常化 EPS × 中枢 PE" if math.isfinite(eps_n) else "BVPS × 历史中枢 PB"
        T = eps_n * pe_mid if math.isfinite(eps_n) else (bvps * pb_med if bvps > 0 and pb_med > 0 else math.nan)

    # §5 通道 E: a buyback cap far above the price is management's own view of value.
    cap_h = _f(row.get("buyback_cap_h"))
    if p.get("buyback_target", False) and "e_buyback" in str(sig.get("codes", "")) and cap_h > P:
        if not math.isfinite(T) or cap_h > T:
            T = cap_h
            method = "回购上限价（管理层给出的价值下限）"

    # Sanity caps on the base value: book value x mid PB (non-event, non-SOTP), and a max upside.
    if math.isfinite(T) and not is_event and theme != "revalue" and bvps > 0 and not method.startswith("回购"):
        pb_mid = np.nanmax([pb_med, _f(row.get("ind_pb"))])
        if math.isfinite(pb_mid) and pb_mid > 0 and T > bvps * pb_mid * p["pb_cap_mult"]:
            T = bvps * pb_mid * p["pb_cap_mult"]
            notes.append(f"基准价值按 BVPS × 中枢 PB × {p['pb_cap_mult']} 封顶")
    if math.isfinite(T) and T > P * (1 + p["max_upside"]):
        T = P * (1 + p["max_upside"])
        notes.append(f"基准上行按 {p['max_upside']:.0%} 封顶")

    # ---- step 4: downside anchors (take the most conservative) -----------------------
    anchors: dict[str, float] = {}
    if bvps > 0 and pb_min > 0:
        anchors["历史最低 PB"] = bvps * pb_min
    if tbvps > 0:
        anchors["净资产打折"] = tbvps * 0.8
    if is_event and math.isfinite(pre):
        anchors["事件前价格"] = pre
    if eps_ttm > 0 and theme not in ("revalue",):
        # §8 step 4: earnings cut by 20% x the stock's own low PE (10th percentile of its history,
        # at least ``pe_mid_lo``); with too little history fall back to pe_mid_lo.
        pe_low = p["pe_mid_lo"]
        if p.get("anchor_pe", "own") == "own" and _f(row.get("pe_months")) >= 24 and _f(row.get("pe_p10")) > 0:
            pe_low = max(pe_low, _f(row.get("pe_p10")))
        anchors["盈利下修估值"] = eps_ttm * 0.8 * pe_low
    if theme == "revalue" and math.isfinite(netcash_ps) and netcash_ps > 0:
        anchors["净现金"] = netcash_ps
    anchors = {k: v for k, v in anchors.items() if math.isfinite(v) and v > 0}
    if is_event and "事件前价格" in anchors:
        # §9: the failure landing point of a deal is the pre-event price (or the shell value below it).
        floor_name, D = "事件前价格", anchors["事件前价格"]
    elif anchors:
        floor_name = min(anchors, key=anchors.get)
        D = anchors[floor_name]
    else:
        floor_name, D = "无锚", math.nan
    broken = False
    if not math.isfinite(D) or D >= P * (1 - p["min_downside"]):
        broken = True
        notes.append(f"现价已低于下行锚（{floor_name}），下行按 {p['broken_downside']:.0%} 估")
        D = P * (1 - p["broken_downside"])
        floor_name = "锚已跌破"
    downside = 1 - D / P

    # ---- step 5: probabilities ------------------------------------------------------
    if is_event:
        p_up, p_why = event_probability(theme, sig.get("codes", ""), p)
    else:
        base = {"distress": p["p_distress"], "cycle": p["p_other"], "revalue": p["p_other"]}.get(theme, p["p_other"])
        p_up, p_why = base, f"{THEMES.get(theme, theme)} base rate {base:.0%}"
        if theme == "distress" and bool(row.get("ocf_q_pos2")):
            p_up += p["p_bonus_ocf"]
            p_why += f" + 经营现金流同步转正 {p['p_bonus_ocf']:.0%}"
    extra = max(0, len(set(sig.get("channels", ""))) - 1)
    if extra:
        p_up += p["p_bonus_multi"] * extra
        p_why += f" + {extra} 条独立通道 × {p['p_bonus_multi']:.0%}"
    p_up = float(np.clip(p_up, 0.05, 0.9))
    if abs(p_up - 0.5) < 0.02:  # §8 step 5: no 50/50
        p_up = 0.47

    # ---- step 1 + 6: scenarios and metrics -------------------------------------------
    O = T * (1 + p["opt_uplift"]) if math.isfinite(T) else math.nan
    pb_, po_ = p_up * 0.75, p_up * 0.25
    pp_ = 1 - p_up

    def expected(Dv, pu):
        return (1 - pu) * (Dv / P - 1) + pu * 0.75 * (T / P - 1) + pu * 0.25 * (O / P - 1)

    E = expected(D, p_up)
    odds = (T - P) / (P - D) if P > D else math.nan
    breakeven = (P - D) / (T - D) if T > D else math.nan
    R = p["odds_min_event"] if is_event else p["odds_min"]
    buy_line = (T + R * D) / (1 + R) if math.isfinite(T) else math.nan

    # Entry price: min(price, buy line). A card that does not qualify at today's price waits
    # in the ammo pool for the buy line (§10.1), so the gates are judged at the entry price.
    e = min(P, buy_line) if math.isfinite(buy_line) and buy_line > D else P

    def expected_p(price, Dv, pu):
        return (1 - pu) * (Dv / price - 1) + pu * 0.75 * (T / price - 1) + pu * 0.25 * (O / price - 1)

    # ---- step 8: sensitivity (at the entry price) -------------------------------------
    D2 = D * (0.9 if is_event else 0.78)
    odds2 = (T - e) / (e - D2) if e > D2 else math.nan
    q1 = bool(odds2 >= 2)
    q2 = bool(expected_p(e, D, max(p_up - 0.10, 0.0)) > 0)
    h = p["horizon_years"]
    E_e = expected_p(e, D, p_up)
    ann_late = (1 + E_e) ** (1 / (h + 0.5)) - 1 if E_e > -1 else -1
    q3 = bool(ann_late >= p["annual_min"])
    passes = int(q1) + int(q2) + int(q3)

    hard_floor = (not broken) and floor_name in HARD_FLOOR_ANCHORS
    support = "hard" if hard_floor and floor_name != "历史最低 PB" else ("valuation" if not broken else "none")
    deadline = catalyst_deadline(theme, row, sig, today)
    card = {
        "symbol": sym, "date": pd.Timestamp(today), "theme": theme, "theme_name": THEMES.get(theme, theme),
        "channels": sig.get("channels", ""), "codes": sig.get("codes", ""), "method": method,
        "price_h": P, "price": _f(row["close"]), "target_h": T, "optimistic_h": O, "falsify_h": D,
        "anchor": floor_name, "anchors": "；".join(f"{k} {v:.2f}" for k, v in anchors.items()),
        "anchor_broken": broken, "support": support,
        "pre_event_h": pre, "p_up": p_up, "p_why": p_why, "p_pess": pp_, "p_base": pb_, "p_opt": po_,
        "upside": T / P - 1 if math.isfinite(T) else math.nan, "downside": downside,
        "odds": odds, "expected": E, "breakeven_p": breakeven, "R": R, "buy_line_h": buy_line,
        "sens_anchor": q1, "sens_prob": q2, "sens_delay": q3, "sens_passes": passes,
        "catalyst_deadline": deadline, "is_event": is_event, "notes": "；".join(notes),
        "n_ab": int(sig.get("n_ab", 0)), "has_change": bool(sig.get("has_change", False)),
        "detail": sig.get("detail", ""),
    }
    card["entry_h"] = e
    card["odds_entry"] = (T - e) / (e - D) if e > D else math.nan
    card["expected_entry"] = E_e
    card["downside_entry"] = 1 - D / e if e > 0 else math.nan
    # Gates at the entry price (§8 门槛). At the buy line the odds equal R by construction,
    # so what decides is the expected return and the downside cap.
    card["qualified"] = bool(
        math.isfinite(card["odds_entry"]) and card["odds_entry"] >= R - 1e-9
        and E_e >= p["exp_min"] and card["downside_entry"] <= p["max_downside"])
    card["qualified_now"] = bool(
        math.isfinite(odds) and odds >= R and E >= p["exp_min"] and downside <= p["max_downside"])
    card["np_ttm"] = _f(row.get("np_ttm"))
    card["np_ttm_ly"] = _f(row.get("np_ttm_ly"))
    card["net_cash"] = _f(row.get("net_cash"))
    card["industry"] = row.get("industry")
    card["amount20"] = _f(row.get("amount20"))
    card["mcap"] = _f(row.get("mcap"))
    card["board"] = row.get("board")
    card["is_st"] = bool(row.get("is_st"))
    return card


def expected_at(card: dict, price_h: float) -> float:
    """Expected return of the three-scenario card when bought at ``price_h``."""
    D, T, O, pu = card["falsify_h"], card["target_h"], card["optimistic_h"], card["p_up"]
    if not (price_h > 0 and math.isfinite(T)):
        return math.nan
    return (1 - pu) * (D / price_h - 1) + pu * 0.75 * (T / price_h - 1) + pu * 0.25 * (O / price_h - 1)


def catalyst_deadline(theme: str, row, sig, today) -> pd.Timestamp:
    """When the catalyst should have shown up (falsify date, §7 L5)."""
    t = pd.Timestamp(today)
    if theme in EVENT_THEMES:
        a = sig.get("anchor_date")
        base = pd.Timestamp(a) if a is not None and pd.notna(a) else t
        return max(base + pd.DateOffset(months=12), t + pd.DateOffset(months=6))
    if theme == "distress":
        # Next annual report: turnaround must show in the full year.
        y = t.year if t.month <= 3 else t.year + 1
        return pd.Timestamp(year=y, month=4, day=30)
    return t + pd.DateOffset(months=12)


def quick_odds(row: pd.Series, sig: pd.Series, close_h: pd.DataFrame, p: dict) -> dict:
    """§11.1 5-minute odds (ranking and elimination only, never a buy reason)."""
    P = _f(row["close_h"])
    bvps, pb_min, pb_med = _f(row.get("bvps_h")), _f(row.get("pb_min")), _f(row.get("pb_med"))
    eps_n = normalized_eps_h(row)
    theme = sig["theme"]
    if theme in EVENT_THEMES:
        pre = pre_event_price(close_h, row.name, sig.get("anchor_date"))
        upl = p["event_uplift_control"] if theme == "control" else p["event_uplift_ma"]
        T = pre * (1 + upl) if math.isfinite(pre) else math.nan
    else:
        pre = math.nan
        T = eps_n * mid_pe(row, p) if math.isfinite(eps_n) else (bvps * pb_med if bvps > 0 and pb_med > 0 else math.nan)
    if math.isfinite(T) and T > P * (1 + p["max_upside"]):
        T = P * (1 + p["max_upside"])
    cands = [x for x in (bvps * pb_min if bvps > 0 and pb_min > 0 else math.nan, pre) if math.isfinite(x)]
    D = max(cands) if cands else math.nan
    if bvps > 0:
        D = min(D, bvps * 0.8) if math.isfinite(D) else bvps * 0.8
    if not math.isfinite(D) or D >= P * (1 - p["min_downside"]):
        D = P * (1 - p["broken_downside"])
    up = T / P - 1 if math.isfinite(T) else math.nan
    down = 1 - D / P
    return {"quick_up": up, "quick_down": down, "quick_odds": up / down if down > 0 and math.isfinite(up) else math.nan,
            "quick_target_h": T, "quick_floor_h": D}


def current_odds(card: dict, price_h: float) -> float:
    """§16.2 (目标价 − 现价) ÷ (现价 − 证伪价)."""
    T, D = card["target_h"], card["falsify_h"]
    if price_h <= D:
        return -math.inf
    return (T - price_h) / (price_h - D)
