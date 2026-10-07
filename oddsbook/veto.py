"""一票否决 (v3 §2.2).

hard -> excluded from every pool.
soft -> "放弃，或最多给 ≤1% 的期权仓": the stock may only be held as an option
        position (≤ option_weight), and only when its card odds are ≥ 8.

What free data can and can't see is written next to each rule; rules that
need data we don't have (担保和表外义务, 12 个月内到期债务明细) use proxies.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HARD_EVENTS = {"delist_final": "已公告终止上市", "investigation": "立案调查 / 行政处罚",
               "audit_adverse": "非标准审计意见"}
SOFT_EVENTS = {"delist_risk": "退市风险提示", "funds_occupied": "资金占用 / 违规担保",
               "audit_emphasis": "非标准审计意见（带强调事项 / 内控非标）",
               "frozen": "控股股东股份冻结 / 司法拍卖", "debt_default": "债务逾期 / 违约",
               "audit_change": "变更会计师事务所", "st_imposed": "被实施风险警示"}


def veto(snap: pd.DataFrame, events: pd.DataFrame, today, p: dict) -> pd.DataFrame:
    """``events``: announcements of the last 12 months (ann_date <= today)."""
    t = pd.Timestamp(today)
    out = pd.DataFrame(index=snap.index)
    hard = pd.Series([[] for _ in range(len(snap))], index=snap.index, dtype=object)
    soft = pd.Series([[] for _ in range(len(snap))], index=snap.index, dtype=object)

    def add(target: pd.Series, mask: pd.Series, reason: str) -> None:
        for s in mask[mask.fillna(False)].index:
            target[s].append(reason)

    # ---- announcements -----------------------------------------------------
    ev = events[events["ann_date"] >= t - pd.Timedelta(days=365)]
    cleared = set(ev.loc[ev["event"] == "audit_cleared", "symbol"])
    for code, reason in HARD_EVENTS.items():
        e = ev[ev["event"] == code]
        if code == "audit_adverse":
            e = e[~e["symbol"].isin(cleared)]
        if code == "investigation":  # a director investigated "因非公司事项" is not a company veto
            e = e[~e["title"].str.contains("非公司事项|个人|报案", na=False)]
        add(hard, snap.index.to_series().isin(e["symbol"]), reason)
    recent = ev[ev["ann_date"] >= t - pd.Timedelta(days=180)]
    for code, reason in SOFT_EVENTS.items():
        e = recent if code in ("frozen", "funds_occupied", "debt_default", "delist_risk", "st_imposed") else ev
        if code in ("audit_emphasis",):  # cleared later in the year -> no longer a veto
            cleared = set(ev.loc[ev["event"] == "audit_cleared", "symbol"])
            e = e[~e["symbol"].isin(cleared)]
        add(soft, snap.index.to_series().isin(e.loc[e["event"] == code, "symbol"]), reason)
    # Frequent auditor changes: two or more in a year are hard.
    ac = ev[ev["event"] == "audit_change"].groupby("symbol").size()
    add(hard, snap.index.to_series().map(ac).fillna(0) >= 2, "一年内多次更换审计机构")

    # ---- delisting indicators (主板为例；创业板 / 科创板 阈值更低) -------------
    growth = snap["board"].isin(["创业板", "科创板"])
    rev_floor = np.where(growth, 1e8, 3e8)
    fin_delist = (snap["fy_min_profit"] < 0) & (snap["fy_rev"] < rev_floor)
    add(soft, fin_delist, "财务类退市指标（利润为负且收入低于门槛）")
    cap_floor = np.where(growth, 3e8, 5e8)
    add(hard, snap["mcap"] < cap_floor * 1.2, "市值接近市值类退市线")
    add(hard, snap["close"] < 1.2, "股价接近面值退市线")
    add(soft, snap["star_st"], "*ST")

    # ---- balance sheet / cash -----------------------------------------------
    # 短债长投、高杠杆: no maturity table in free data -> current ratio + leverage proxy.
    add(soft, (snap["debt_ratio"] > 80) & (snap["current_ratio"] < 0.8), "高杠杆且流动比率 < 0.8")
    add(soft, snap["equity"] <= 0, "净资产为负")
    # 经营现金流长期无法解释利润: 3 years of profits, 3 years of cash burn.
    add(soft, (snap["np_fy3"] > 0) & (snap["ocf_fy3"] < -0.5 * snap["np_fy3"].abs()),
        "三年累计盈利但经营现金流为负")
    # Pledge (company level; the controller-level ratio is not in free data).
    add(soft, snap["pledge"] > 50, "质押比例高")
    add(hard, snap["pledge"] > 70, "质押比例 > 70%")

    # ---- liquidity: 5 天内退出 50%（按日均成交额 10%）-> position ≤ ADV ---------
    add(hard, snap["amount20"] < p["min_amount"] * 1e8, "日均成交额不足")
    # No financials at all: can't write a thesis or a falsify price.
    add(hard, snap["fin_period"].isna(), "没有可用财报")

    out["hard"] = hard.map(bool)
    out["soft"] = soft.map(bool) & ~out["hard"]
    out["reasons"] = (hard + soft).map(lambda r: "；".join(dict.fromkeys(r)))
    return out
