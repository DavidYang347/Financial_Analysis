"""六条筛选通道(文档第 5 节)。每条通道返回一组信号,进入雷达池。

信号 = {symbol, channel, sub, grade, text, date, ...}
    grade: A = 审计财报 / 监管文件 / 交易所公告;B = 推断、历史规律;C = 只能当线索
    同一只股票可以被多条通道同时触发,独立的 A/B 级信号越多,评分里的"信息质量"越高。

免费数据覆盖不到的部分(行业价格 / 库存 / 开工率、分部估值、互动易、机构调研)没有做,
通道 F 用"行业亏损面连续收窄"做代理,通道 D 只看货币资金减总负债,见各函数注释。
"""
from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

from highodds import features as F


class EventIndex:
    """(symbol, event) -> 已排序的公告日期数组,支持 "某窗口内有没有 / 有几次" 的快速查询。"""

    def __init__(self, notices: pd.DataFrame) -> None:
        n = notices[notices["event"] != ""]
        self.d: dict[tuple[str, str], np.ndarray] = {}
        for (s, e), g in n.groupby(["symbol", "event"], sort=False):
            self.d[(s, e)] = np.sort(g["ann_date"].to_numpy().astype("datetime64[ns]"))
        self.by_sym: dict[str, set[str]] = {}
        for (s, e) in self.d:
            self.by_sym.setdefault(s, set()).add(e)

    def count(self, sym: str, event: str, day, days_back: int, days_skip: int = 0) -> int:
        a = self.d.get((sym, event))
        if a is None:
            return 0
        hi = np.datetime64(pd.Timestamp(day) - pd.Timedelta(days=days_skip))
        lo = np.datetime64(pd.Timestamp(day) - pd.Timedelta(days=days_back))
        return int(np.searchsorted(a, hi, side="right") - np.searchsorted(a, lo, side="right"))

    def last(self, sym: str, event: str, day) -> pd.Timestamp | None:
        a = self.d.get((sym, event))
        if a is None:
            return None
        i = int(np.searchsorted(a, np.datetime64(pd.Timestamp(day)), side="right"))
        return pd.Timestamp(a[i - 1]) if i else None

    def any_recent(self, sym: str, events, day, days_back: int) -> bool:
        return any(self.count(sym, e, day, days_back) for e in events)


def _sig(sym, channel, sub, grade, text, date, **kw) -> dict:
    return {"symbol": sym, "channel": channel, "sub": sub, "grade": grade, "text": text, "date": pd.Timestamp(date), **kw}


# ---- 通道 A:并购重组 / 控制权变更 ----------------------------------------------------------
def channel_a(S, chains, ev: EventIndex, cfg, day) -> list[dict]:
    out = []
    for (sym, fam), c in chains.items():
        live = c["live"]
        if sym not in S.index or not live or live.get("done"):
            continue
        if (day - live["last"]).days > cfg.event_lookback:
            continue
        label = {"restructure": "重组", "ctrl": "控制权变更", "bankruptcy": "破产重整"}[fam]
        out.append(_sig(sym, "A", "A1", "A", f"{label}:{live['tag']}(最近公告 {live['last']:%Y-%m-%d})", live["last"],
                        fam=fam, tag=live["tag"], chain_first=live["first"], n_kinds=live.get("kinds", 1)))
    # A2 前兆:条件越多越值得关注。壳特征单独不触发,必须有事件类前兆。
    for sym in S.index:
        kinds = ev.by_sym.get(sym)
        if not kinds:
            continue
        hits, dates = [], []
        for tag, back, skip, text in (("ctrl_change_done", 730, 180, "新实控人入主 6-24 个月"),
                                      ("asset_sale", 365, 0, "剥离/出售资产"),
                                      ("debt_forgive", 365, 0, "债务豁免/重组"),
                                      ("placement", 365, 0, "定增")):
            if ev.count(sym, tag, day, back, skip):
                hits.append(text)
                dates.append(ev.last(sym, tag, day))
        if not hits:
            continue
        r = S.loc[sym]
        if r["mcap_yi"] < cfg.a2_mcap and r["debt_ratio"] < cfg.a2_debt * 100 and not r["is_st"]:
            hits.append("小市值 + 低负债的干净壳特征")  # 只是加分项,日期沿用事件日期
        if len(hits) >= 2:
            out.append(_sig(sym, "A", "A2", "B", "重组前兆:" + "、".join(hits), max(dates), hits=len(hits)))
    return out


# ---- 通道 B:困境反转 ----------------------------------------------------------------------
def channel_b(S, pit, ev: EventIndex, cfg, day) -> list[dict]:
    out = []
    fc = pit.forecasts(day, 180)
    fc = fc[fc["metric"].astype(str).str.contains("归属于上市公司股东的净利润") & fc["kind"].isin(["减亏", "扭亏"])]
    fc = fc.sort_values("ann_date").groupby("symbol").tail(1)
    for r in fc.itertuples():
        if r.symbol in S.index and S.at[r.symbol, "loss_years"] >= cfg.b_loss_years:
            out.append(_sig(r.symbol, "B", "B1", "A", f"连续亏损 {int(S.at[r.symbol, 'loss_years'])} 年后出{r.kind}预告",
                            r.ann_date, kind=r.kind))
    m = S[(S["ocf_pos2"]) & (S["np_last"] < 0) & ((S["loss_years"] >= 1) | (S["ann_np"] < 0))]
    for sym in m.index:  # 状态型信号的日期取财报公告日:同一份财报只算一次新信号
        out.append(_sig(sym, "B", "B2", "A", "最近两个季度经营现金流为正而净利润仍为负", m.at[sym, "fin_ann"]))
    for sym in S.index:
        if ev.count(sym, "st_removed", day, 180):
            out.append(_sig(sym, "B", "B3", "A", "撤销风险警示(摘帽)", ev.last(sym, "st_removed", day)))
        elif ev.count(sym, "debt_forgive", day, 180) and S.at[sym, "ann_np"] < 0:
            out.append(_sig(sym, "B", "B3", "A", "亏损公司获债务豁免/重组", ev.last(sym, "debt_forgive", day)))
    return out


# ---- 通道 C:错杀 --------------------------------------------------------------------------
def channel_c(S, chains, ev: EventIndex, pit, cfg, day, pricer) -> list[dict]:
    out = []
    pairs = {}
    for (sym, fam), c in chains.items():
        cl = c["closed"]
        if sym in S.index and cl and 0 <= (day - cl["end"]).days <= 180:
            pairs[(sym, fam)] = cl
    pre = pricer.before({(s, cl["first"]) for (s, _), cl in pairs.items()})
    for (sym, fam), cl in pairs.items():
        p0 = pre.get((sym, cl["first"]))
        px = S.at[sym, "px"]
        if p0 and px <= p0 * 1.02:
            out.append(_sig(sym, "C", "C1", "A", f"{'重组' if fam == 'restructure' else '控制权变更'}终止后股价"
                            f"({px / p0 - 1:+.0%})回到事件前水平", cl["end"], pre_px=p0, fam=fam))
    for sym in S.index[S["is_st"] | S["star_st"]]:
        n = ev.count(sym, "st_imposed", day, 60)
        if n and S.at[sym, "loss_years"] <= 1:
            out.append(_sig(sym, "C", "C3", "B", "刚被实施风险警示,且亏损不超过 1 个会计年度(可能是被动抛售)", day))
    un = pit.unlock
    un = un[(un["date"] <= day) & (un["date"] > day - pd.Timedelta(days=30)) & (un["ratio"] >= 10)]
    for sym in set(un["symbol"]) & set(S.index[S["ret60"] < -0.15]):
        out.append(_sig(sym, "C", "C3", "B", "大额限售股解禁后股价下跌(被动抛售)", day))
    f = S[(S["fin_ann"] >= day - pd.Timedelta(days=30)) & (S["ret20"] <= -cfg.c_drop) & (S["ocf_q"] > 0)
          & (S["np_yoy"] > -50)]
    for sym in f.index:
        out.append(_sig(sym, "C", "C4", "B", f"财报后下跌 {S.at[sym, 'ret20']:.0%},但单季经营现金流为正", day))
    return out


def channel_c_valuation(S, syms: set[str], cfg, day) -> list[dict]:
    """PB 历史底部:必须同时有通道 A/B 的变化信号才算数(文档 C 通道第三行),所以只给已有信号的股票加证据。"""
    out = []
    for sym in syms & set(S.index):
        r = S.loc[sym]
        if r["pb_pct"] <= cfg.c_pb_pct and (r["np_yoy"] > -30 or np.isnan(r["np_yoy"])):
            out.append(_sig(sym, "C", "C2", "B", f"PB 处于 {cfg.pb_window_days // 250} 年历史 {r['pb_pct']:.0%} 分位", day))
    return out


# ---- 通道 D:资产重估 ----------------------------------------------------------------------
def channel_d(S, ev: EventIndex, cfg, day) -> list[dict]:
    """只有"货币资金 - 总负债"这一个口径:比文档的"净现金 + 金融资产 + 上市公司股权"保守,会漏掉持股型公司。"""
    out = []
    m = S[(S["netcash_ps"] >= cfg.d_netcash * S["px"]) & (S["netcash_ps"] > 0)]
    for sym in m.index:
        out.append(_sig(sym, "D", "D1", "A", f"货币资金减总负债 = 市值的 {S.at[sym, 'netcash_ps'] / S.at[sym, 'px']:.0%}",
                        m.at[sym, "fin_ann"]))
    for sym in S.index:
        if ev.count(sym, "spin_off", day, 365):
            out.append(_sig(sym, "D", "D2", "B", "子公司分拆上市", ev.last(sym, "spin_off", day)))
    return out


# ---- 通道 E:内部人与资金行为 ---------------------------------------------------------------
def channel_e(S, pit, cfg, day) -> list[dict]:
    out = []
    inc = pit.holder_moves(day, 90, "增持")
    dec = pit.holder_moves(day, 90, "减持")
    a = inc.groupby("symbol")["pct_total"].sum()
    b = dec.groupby("symbol")["pct_total"].sum().reindex(a.index).fillna(0)
    for sym, v in a.items():
        if sym in S.index and v >= cfg.e_holder_pct and v > b[sym]:
            last = inc[inc["symbol"] == sym]["ann_date"].max()
            out.append(_sig(sym, "E", "E1", "A", f"近 90 日股东增持合计占总股本 {v:.2f}%", last))
    rp = pit.repurchases(day, 150)
    rp = rp[(rp["pct_low"] >= cfg.e_buyback_pct) & (rp["progress"].astype(str) != "停止实施")]
    for r in rp.sort_values("ann_date").groupby("symbol").tail(1).itertuples():
        if r.symbol in S.index:
            cap = r.price_cap
            txt = f"回购计划 ≥ 总股本 {r.pct_low:.1f}%"
            if cap and cap > 0:
                txt += f",价格上限 {cap:.2f} 元"
            out.append(_sig(r.symbol, "E", "E2", "A", txt, r.ann_date))
    return out


# ---- 通道 F:行业拐点(代理) ---------------------------------------------------------------
def channel_f(S, pit, cfg, day) -> list[dict]:
    """文档用产品价格 / 库存 / 开工率判断拐点,这些数据没有免费批量接口。
    代理指标:行业内单季归母净利润为负的公司占比连续两个季度下降(亏损面收窄)。
    拐点行业里取"资产负债表最强 + 弹性最大"的 3 家。每月跑一次。"""
    f = pit.fin_visible(day)
    f = f[f["industry"].notna()]
    periods = sorted(f["period"].unique())[-4:]
    if len(periods) < 3:
        return []
    share = {}
    for p in periods:
        g = f[(f["period"] == p) & f["np_q"].notna()]
        share[p] = g.groupby("industry")["np_q"].agg(lambda s: (s < 0).mean()), g.groupby("industry")["np_q"].size()
    last3 = periods[-3:]
    s0, s1, s2 = (share[p][0] for p in last3)
    n2 = share[last3[-1]][1]
    idx = s0.index.intersection(s1.index).intersection(s2.index)
    turn = [i for i in idx if n2.get(i, 0) >= cfg.f_min_stocks and s0[i] > s1[i] > s2[i] and s0[i] - s2[i] >= 0.05
            and s2[i] >= 0.15]  # 亏损面还有明显空间才叫"底部"
    out = []
    for ind in turn:
        g = S[(S["industry"] == ind) & (S["debt_ratio"] < 55) & (S["loss_years"] >= 1)]
        g = g[g["pb_pct"].notna()].sort_values(["pb_pct"]).head(3)
        for sym in g.index:
            out.append(_sig(sym, "F", "F1", "B", f"行业「{ind}」亏损面 {s0[ind]:.0%}→{s1[ind]:.0%}→{s2[ind]:.0%} 连续收窄,"
                            f"本股资产负债率 {S.at[sym, 'debt_ratio']:.0f}%", day, industry=ind))
    return out


def scan(S: pd.DataFrame, chains, ev: EventIndex, pit, cfg, day, pricer, month_end: bool) -> list[dict]:
    on = set(cfg.channels)
    sig: list[dict] = []
    if "A" in on:
        sig += channel_a(S, chains, ev, cfg, day)
    if "B" in on:
        sig += channel_b(S, pit, ev, cfg, day)
    if "C" in on:
        sig += channel_c(S, chains, ev, pit, cfg, day, pricer)
    if "D" in on:
        sig += channel_d(S, ev, cfg, day)
    if "E" in on:
        sig += channel_e(S, pit, cfg, day)
    if "F" in on and month_end:
        sig += channel_f(S, pit, cfg, day)
    if "C" in on and sig:
        sig += channel_c_valuation(S, {s["symbol"] for s in sig}, cfg, day)
    return sig
