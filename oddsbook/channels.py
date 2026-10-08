"""Six screening channels (v3 §5) -> radar signals.

One row per (symbol, signal):

    channel   A-F
    code      short signal id (see SIGNALS)
    theme     the 母题 used to value it (odds.THEMES)
    grade     evidence grade of the signal itself: A (announcement / audited data),
              B (cross-check, historical pattern), C (management talk, single source)
    change    True when the signal is a real *change*; False for "only cheap"
              (§6.1 快速淘汰 第 3 条 needs at least one change)
    date      the date the signal became visible (announcement date or today)
    anchor_date  for event signals: the deal's first announcement (pre-event price anchor)
    detail    human-readable explanation

Event signals use announcements in the scan window (last ``radar_event_days``
trading days, inclusive of today). State signals (net cash, valuation floor,
precursors) are re-evaluated every scan.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# code -> (channel, theme, grade, change, label)
SIGNALS = {
    # A 并购重组 / 控制权
    "a1_control": ("A", "control", "A", True, "控制权变更 / 协议转让"),
    "a1_ma_plan": ("A", "ma", "A", True, "筹划重组 / 预案"),
    "a1_ma_draft": ("A", "ma", "A", True, "重组草案"),
    "a1_ma_node": ("A", "ma", "A", True, "重组节点推进"),
    "a2_new_owner": ("A", "control", "B", True, "新实控人入主 6–24 个月 + 干净壳特征"),
    "a2_asset_sale": ("A", "control", "A", True, "剥离旧业务 / 出售资产"),
    "a2_placement_major": ("A", "control", "A", True, "大股东参与定增"),
    # B 困境反转
    "b_forecast_turn": ("B", "distress", "A", True, "连续亏损后预告减亏 / 扭亏"),
    "b_report_turn": ("B", "distress", "A", True, "连续亏损后单季扭亏"),
    "b_ocf_first": ("B", "distress", "A", True, "经营现金流先于利润转正（两个季度）"),
    "b_deleverage": ("B", "distress", "A", True, "负债连续下降 / 债务重组"),
    "b_bankruptcy": ("B", "bankruptcy", "A", True, "破产重整 / 重整投资人"),
    "b_big_bath": ("B", "distress", "A", True, "年报“大洗澡”但现金流没恶化"),
    "b_st_path": ("B", "distress", "B", True, "*ST 有可测算的摘帽路径"),
    # C 错杀
    "c_ma_failed": ("C", "other", "A", True, "重组 / 控制权变更终止后跌回公告前"),
    "c_overreaction": ("C", "other", "B", True, "一次性计提后股价跌幅过大"),
    "c_pb_floor": ("C", "other", "B", False, "估值处于历史底部（只是便宜）"),
    "c_forced_st": ("C", "other", "A", True, "被实施 ST 后的被动抛售（单年亏损，可修复）"),
    "c_forced_unlock": ("C", "other", "A", True, "大额解禁后的抛压"),
    # D 资产重估
    "d_net_cash": ("D", "revalue", "A", False, "净现金占市值比例高"),
    "d_spinoff": ("D", "revalue", "A", True, "子公司分拆上市"),
    # E 内部人
    "e_insider_buy": ("E", "other", "A", True, "大股东 / 董监高二级市场增持"),
    "e_buyback": ("E", "other", "A", True, "回购（上限价远高于现价 / 用于注销）"),
    "e_incentive": ("E", "other", "C", True, "股权激励 / 员工持股草案"),
    # F 行业拐点
    "f_industry_turn": ("F", "cycle", "B", True, "行业单季毛利率连续两季改善、收入止跌"),
}
THEME_PRIORITY = ["ma", "bankruptcy", "control", "distress", "cycle", "revalue", "other"]
# Signals that describe a state rather than a dated event: they are re-detected every scan, so the
# book keeps the date they were first seen (otherwise every scan would look like "new information").
STATE_CODES = {"a2_new_owner", "b_deleverage", "c_pb_floor", "c_overreaction", "d_net_cash", "f_industry_turn"}

COLUMNS = ["symbol", "channel", "code", "theme", "grade", "change", "date", "anchor_date", "detail"]


def _row(sym, code, date, detail="", anchor_date=None) -> dict:
    ch, theme, grade, change, label = SIGNALS[code]
    return {"symbol": sym, "channel": ch, "code": code, "theme": theme, "grade": grade, "change": change,
            "date": pd.Timestamp(date), "anchor_date": pd.Timestamp(anchor_date) if anchor_date is not None else pd.NaT,
            "detail": f"{label}：{detail}" if detail else label}


def deal_anchor(events, sym: str, kinds: tuple[str, ...], end) -> pd.Timestamp | None:
    """First announcement of the current deal: earliest ``kinds`` event after the last termination / completion.

    ``events`` is either the event frame or a dict symbol -> that symbol's events (faster in loops).
    """
    if isinstance(events, dict):
        e = events.get(sym)
        if e is None:
            return None
        e = e[e["ann_date"] <= end]
    else:
        e = events[(events["symbol"] == sym) & (events["ann_date"] <= end)]
    stop = e[e["event"].isin(["ma_terminated", "control_terminated", "ma_closed"])]["ann_date"].max()
    e = e[e["event"].isin(kinds)]
    if pd.notna(stop):
        e = e[e["ann_date"] > stop]
    return e["ann_date"].min() if len(e) else None


def scan(snap: pd.DataFrame, fs, events: pd.DataFrame, today, window_start, turns: pd.DataFrame | None,
         p: dict) -> pd.DataFrame:
    """All channel signals on ``today``. ``events``: announcements up to today (≥ 2 years of history)."""
    t = pd.Timestamp(today)
    w0 = pd.Timestamp(window_start)
    rows: list[dict] = []
    alive = set(snap.index)
    win = events[(events["ann_date"] >= w0) & (events["ann_date"] <= t) & events["symbol"].isin(alive)]
    deal_kinds = {"control_change", "stake_transfer", "ma_plan", "ma_draft", "ma_progress", "bankruptcy",
                  "ma_terminated", "control_terminated", "ma_closed"}
    hot = events[events["symbol"].isin(set(win["symbol"])) & events["event"].isin(deal_kinds)]
    by_sym = {k: v for k, v in hot.groupby("symbol", sort=False)}

    # ---- A1: announced deals ------------------------------------------------
    for ev_code, sig in (("control_change", "a1_control"), ("stake_transfer", "a1_control"),
                         ("ma_plan", "a1_ma_plan"), ("ma_draft", "a1_ma_draft"),
                         ("ma_progress", "a1_ma_node"), ("ma_approved", "a1_ma_node")):
        for r in win[win["event"] == ev_code].drop_duplicates("symbol", keep="last").itertuples():
            kinds = ("control_change", "stake_transfer") if sig == "a1_control" else ("ma_plan", "ma_draft", "ma_progress")
            anchor = deal_anchor(by_sym, r.symbol, kinds, t) or r.ann_date
            rows.append(_row(r.symbol, sig, r.ann_date, r.title[:60], anchor))

    # ---- A2: precursors -----------------------------------------------------
    cc = events[events["event"].isin(["control_change"]) & (events["ann_date"] <= t - pd.DateOffset(months=6))
                & (events["ann_date"] >= t - pd.DateOffset(months=24))]
    shell = snap[(snap["mcap"] < p["a2_mcap_max"] * 1e8) & (snap["debt_ratio"] < p["a2_debt_max"])]
    for sym, d in cc.groupby("symbol")["ann_date"].min().items():
        if sym in shell.index:
            rows.append(_row(sym, "a2_new_owner", t, f"{d:%Y-%m-%d} 控制权变更，市值 {shell.at[sym, 'mcap'] / 1e8:.0f} 亿",
                             d))
    for r in win[win["event"] == "asset_sale"].drop_duplicates("symbol").itertuples():
        rows.append(_row(r.symbol, "a2_asset_sale", r.ann_date, r.title[:60]))
    for r in win[win["event"] == "placement_major"].drop_duplicates("symbol").itertuples():
        rows.append(_row(r.symbol, "a2_placement_major", r.ann_date, r.title[:60]))

    # ---- B: distress turnaround ------------------------------------------------
    losers = snap[snap["loss_years"] >= p["b_loss_years"]]
    fc_new = losers[losers["fc_kind"].isin(["扭亏", "减亏"]) & (losers["fc_date"] >= w0)]
    for sym, r in fc_new.iterrows():
        rows.append(_row(sym, "b_forecast_turn", r["fc_date"], f"连亏 {r['loss_years']} 年，{r['fc_kind']}预告"))
    new_rep = snap[(snap["fin_ann"] >= w0) & (snap["fin_ann"] <= t)]
    turn = new_rep[(new_rep["loss_years"] >= p["b_loss_years"]) & (new_rep["np_q"] > 0)]
    for sym, r in turn.iterrows():
        rows.append(_row(sym, "b_report_turn", r["fin_ann"], f"{r['fin_period']:%Y-%m} 单季净利 {r['np_q'] / 1e8:.2f} 亿"))
    ocf = new_rep[new_rep["ocf_q_pos2"] & (new_rep["np_ttm"] < 0)]
    for sym, r in ocf.iterrows():
        rows.append(_row(sym, "b_ocf_first", r["fin_ann"], "近两季经营现金流为正，TTM 净利润仍为负"))
    dr = set(win.loc[win["event"] == "debt_restructure", "symbol"])
    delev = new_rep[new_rep["liab_falling"] & (new_rep["debt_ratio"] > 50)]
    for sym in set(delev.index) | (dr & alive):
        rows.append(_row(sym, "b_deleverage", t, "债务重组 / 豁免 / 展期" if sym in dr else "总负债连续三期下降"))
    bk = win[(win["event"] == "bankruptcy")]
    for r in bk.drop_duplicates("symbol", keep="last").itertuples():
        anchor = deal_anchor(by_sym, r.symbol, ("bankruptcy",), t) or r.ann_date
        rows.append(_row(r.symbol, "b_bankruptcy", r.ann_date, r.title[:60], anchor))
    imp = set(events.loc[(events["event"] == "impairment") & (events["ann_date"] >= t - pd.Timedelta(days=120)), "symbol"])
    bath = snap[snap.index.isin(imp) & (snap["fy_np"] < 0) & (snap["ocf_ttm"] > 0)
                & (snap["fin_ann"] >= w0) & (snap["fin_period"].dt.month == 12)]
    for sym, r in bath.iterrows():
        rows.append(_row(sym, "b_big_bath", r["fin_ann"], f"年度亏损 {r['fy_np'] / 1e8:.1f} 亿，经营现金流 TTM 为正"))
    growth = snap["board"].isin(["创业板", "科创板"])
    rev_floor = np.where(growth, 1e8, 3e8)
    stp = snap[snap["star_st"] & (snap["np_q"] > 0) & (snap["revenue_ttm"] >= rev_floor) & (snap["equity"] > 0)
               & (snap["fin_ann"] >= w0)]
    for sym, r in stp.iterrows():
        rows.append(_row(sym, "b_st_path", r["fin_ann"], "单季盈利、收入达标、净资产为正"))

    # ---- C: mispricing ----------------------------------------------------------
    term = win[win["event"].isin(["ma_terminated", "control_terminated"])]
    for r in term.drop_duplicates("symbol").itertuples():
        kinds = ("ma_plan", "ma_draft", "control_change", "stake_transfer")
        e = by_sym.get(r.symbol, events.iloc[:0])
        e = e[e["event"].isin(kinds) & (e["ann_date"] < r.ann_date)
              & (e["ann_date"] >= r.ann_date - pd.DateOffset(months=30))]
        if len(e):
            rows.append(_row(r.symbol, "c_ma_failed", r.ann_date, r.title[:60], e["ann_date"].min()))
    over = snap[snap.index.isin(imp) & (snap["ret20"] <= -p["c_report_drop"]) & (snap["ocf_ttm"] > 0)]
    for sym, r in over.iterrows():
        rows.append(_row(sym, "c_overreaction", t, f"近 20 日跌 {-r['ret20']:.0%}，近期有减值计提"))
    floor = snap[(snap["pb_pct"] < p["c_pb_pct"]) & (snap["pb_months"] >= 36) & (snap["pb"] > 0)
                 & ~(snap["np_ttm"] < snap["np_ttm_ly"] * 0.8)]
    for sym, r in floor.iterrows():
        rows.append(_row(sym, "c_pb_floor", t, f"PB {r['pb']:.2f}，历史分位 {r['pb_pct']:.0%}"))
    sti = win[win["event"] == "st_imposed"]
    for r in sti.drop_duplicates("symbol").itertuples():
        if r.symbol in snap.index and snap.at[r.symbol, "loss_years"] <= 1 and not snap.at[r.symbol, "star_st"]:
            rows.append(_row(r.symbol, "c_forced_st", r.ann_date, r.title[:60]))
    ul = fs.between("unlock", w0, t, col="date")
    ul = ul[(ul["ratio"] >= p["c_unlock_ratio"]) & ul["symbol"].isin(alive)]
    for sym, g in ul.groupby("symbol"):
        if snap.at[sym, "ret20"] < -0.10:
            rows.append(_row(sym, "c_forced_unlock", g["date"].max(), f"解禁占流通市值 {g['ratio'].sum():.0%}"))

    # ---- D: revaluation --------------------------------------------------------
    nc = snap[(snap["net_cash"] > 0) & (snap["net_cash"] >= p["d_netcash_mcap"] * snap["mcap"])]
    for sym, r in nc.iterrows():
        rows.append(_row(sym, "d_net_cash", t, f"净现金 {r['net_cash'] / 1e8:.1f} 亿 / 市值 {r['mcap'] / 1e8:.1f} 亿"))
    for r in win[win["event"] == "spinoff"].drop_duplicates("symbol").itertuples():
        rows.append(_row(r.symbol, "d_spinoff", r.ann_date, r.title[:60]))

    # ---- E: insiders --------------------------------------------------------------
    h = fs.between("holder", t - pd.Timedelta(days=90), t)
    if len(h):
        h = h[(h["direction"] > 0) & ~h["is_company_plan"] & h["symbol"].isin(alive)]
        agg = h.groupby("symbol").agg(pct=("pct_total", "sum"), last=("ann_date", "max"))
        agg = agg[(agg["pct"] >= p["e_insider_pct"]) & (agg["last"] >= w0)]
        for sym, r in agg.iterrows():
            rows.append(_row(sym, "e_insider_buy", r["last"], f"90 天累计增持 {r['pct']:.2f}% 总股本"))
    rp = fs.between("repurchase", w0, t)
    cancel = set(win.loc[win["event"] == "buyback_cancel", "symbol"])
    if len(rp):
        rp = rp[rp["symbol"].isin(alive)].drop_duplicates("symbol", keep="last")
        for r in rp.itertuples():
            prem = r.price_cap / snap.at[r.symbol, "close"] if r.price_cap and r.price_cap > 0 else np.nan
            if (prem >= p["e_buyback_premium"]) or r.symbol in cancel:
                tag = "用于注销" if r.symbol in cancel else ""
                rows.append(_row(r.symbol, "e_buyback", r.ann_date,
                                 f"上限价 / 现价 {prem:.2f} {tag}".strip() if np.isfinite(prem) else tag))
    for r in win[win["event"] == "incentive"].drop_duplicates("symbol").itertuples():
        rows.append(_row(r.symbol, "e_incentive", r.ann_date, r.title[:60]))

    # ---- F: industry turns (leaders + strongest balance sheets) ----------------
    if p.get("f_enabled", True) and turns is not None and len(turns):
        for ind in turns.loc[turns["turning"], "industry"]:
            g = snap[(snap["industry"] == ind) & (snap["mcap"] > 0)]
            if g.empty:
                continue
            strong = g[g["debt_ratio"] < g["debt_ratio"].median()]
            leaders = strong.sort_values("mcap", ascending=False).head(2).index.tolist()
            second = strong.sort_values("pb").head(3).index.tolist()
            for sym in dict.fromkeys(leaders + second):
                rows.append(_row(sym, "f_industry_turn", t, ind))

    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    df = df[df["symbol"].isin(alive)]
    return df.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "code"], keep="last").reset_index(drop=True)


def summarize(sig: pd.DataFrame) -> pd.DataFrame:
    """Per symbol: channels, best theme, number of independent A/B signals, whether there is a real change."""
    cols = ["channels", "codes", "theme", "n_ab", "has_change", "anchor_date", "last_signal", "last_change", "detail"]
    if sig.empty:
        return pd.DataFrame(columns=cols)
    rank = {t: i for i, t in enumerate(THEME_PRIORITY)}
    g = sig.assign(_r=sig["theme"].map(rank).fillna(99)).sort_values(["symbol", "_r", "date"])
    gb = g.groupby("symbol", sort=True)
    best = gb["theme"].first()
    out = pd.DataFrame(index=best.index)
    out["channels"] = gb["channel"].agg(lambda x: "".join(sorted(set(x))))
    out["codes"] = gb["code"].agg(",".join)
    out["theme"] = best
    ab = g[g["grade"].isin(["A", "B"])]
    out["n_ab"] = ab.groupby("symbol")["code"].nunique().reindex(out.index).fillna(0).astype(int)
    ch = g[g["change"].astype(bool)]
    out["has_change"] = out.index.isin(ch["symbol"])
    th = g[g["theme"] == g["symbol"].map(best)]
    out["anchor_date"] = th.groupby("symbol")["anchor_date"].min().reindex(out.index)
    out["last_signal"] = gb["date"].max()
    out["last_change"] = ch.groupby("symbol")["date"].max().reindex(out.index)
    out["detail"] = gb["detail"].agg("；".join)
    out.index.name = "symbol"
    return out[cols]
