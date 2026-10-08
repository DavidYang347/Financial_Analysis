"""Monthly / weekly review statistics (the quantitative parts of the two SOPs).

Conventions (SOP 铁律 5):
    * Period return = hfq close at the last trading day of the period / hfq close at the last
      trading day before the period - 1. Ratios of hfq closes equal ratios of qfq closes, so this
      is the SOP's 前复权 interval return.
    * 月内翻倍 (SOP 5.2 "月内涨幅 ≥ 100%") is reported two ways:
        - close-to-close: period return ≥ 100%;
        - low-to-high: within the month, max over days of (hfq high / lowest hfq low on or before
          that day, both inside the month) ≥ 2. This is what "从最低点到最高点实现过翻倍" means.
    * Main gainer list excludes ST (status on the last day of the period) and stocks listed for
      fewer than 60 trading days at the period start; those go into side tables.
    * Limit-up / limit-down days use the exchange limit for that day (main 10%, ST 5% / 10% from
      2026-07-06, ChiNext / STAR 20%, BSE 30%); a close at the limit price counts.

Output is a dict of plain lists (JSON-serialisable), saved by review.store.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from data.query import MarketData
from review import store
from review.fetch import RAW
from strategy import rules

MIN_LIST_DAYS = 60
CAP_BUCKETS = [(0, 30e8, "<30亿"), (30e8, 100e8, "30–100亿"), (100e8, 500e8, "100–500亿"), (500e8, math.inf, ">500亿")]
DRIVER_BY_EVENT = {
    "ma_plan": "并购重组", "ma_draft": "并购重组", "ma_progress": "并购重组", "ma_approved": "并购重组",
    "ma_closed": "并购重组", "control_change": "控制权变更", "stake_transfer": "控制权变更",
    "bankruptcy": "困境反转", "debt_restructure": "困境反转", "st_removed": "困境反转", "audit_cleared": "困境反转",
}


# ---- data ------------------------------------------------------------------------
class Panel:
    """Daily bars (raw + hfq) for a window, wide by symbol."""

    def __init__(self, md: MarketData, start: date, end: date, warm_days: int = 400) -> None:
        cal = md.sql("SELECT DISTINCT date FROM daily WHERE date <= ? ORDER BY 1", [end])["date"]
        cal = pd.to_datetime(cal)
        i0 = max(0, int(cal.searchsorted(pd.Timestamp(start))) - warm_days)
        self.d0 = cal.iloc[i0]
        df = md.sql("""SELECT symbol, date, open, high, low, close, volume, amount, turnover, adj_factor
                       FROM daily_hfq WHERE date BETWEEN ? AND ?""", [self.d0.date(), end])
        df["date"] = pd.to_datetime(df["date"])
        f = df["adj_factor"]
        df["close_h"], df["high_h"], df["low_h"], df["open_h"] = df["close"] * f, df["high"] * f, df["low"] * f, df["open"] * f
        self.long = df
        piv = lambda c: df.pivot(index="date", columns="symbol", values=c).sort_index()
        self.close, self.close_h, self.high_h, self.low_h = piv("close"), piv("close_h"), piv("high_h"), piv("low_h")
        self.amount, self.turnover, self.volume = piv("amount"), piv("turnover"), piv("volume")
        self.adj = piv("adj_factor")
        self.dates = self.close.index
        self.stocks = md.stocks().set_index("symbol")
        self.st = md.st_history()

    def window(self, a: pd.Timestamp, b: pd.Timestamp):
        return (self.dates >= a) & (self.dates <= b)


def _period_days(panel: Panel, a, b) -> pd.DatetimeIndex:
    return panel.dates[(panel.dates >= pd.Timestamp(a)) & (panel.dates <= pd.Timestamp(b))]


def _limit_flags(panel: Panel, days: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Boolean frames (date x symbol): closed at limit-up / limit-down on each day."""
    c = panel.close
    # Reference = last traded close (skips suspension days), moved onto today's share basis.
    prev = c.ffill().shift(1) * panel.adj.ffill().shift(1) / panel.adj
    up = pd.DataFrame(False, index=days, columns=c.columns)
    dn = pd.DataFrame(False, index=days, columns=c.columns)
    first_bar = c.notna().cumsum()
    for d in days:
        ref = prev.loc[d]
        cl = c.loc[d]
        ok = ref.notna() & cl.notna() & (first_bar.loc[d] > rules.NEW_LISTING_FREE_DAYS)
        st = panel.st.on(d.date())
        pct = pd.Series([rules.limit_pct(s, d.date(), s in st) for s in c.columns], index=c.columns)
        pct = pct.replace(math.inf, np.nan)
        upx = np.floor(ref * (1 + pct) * 100 + 0.5) / 100
        dnx = np.floor(ref * (1 - pct) * 100 + 0.5) / 100
        up.loc[d] = ok & (cl >= upx - 1e-6)
        dn.loc[d] = ok & (cl <= dnx + 1e-6)
    return up, dn


def _streaks(flags: pd.DataFrame, traded: pd.DataFrame | None = None) -> pd.DataFrame:
    """Consecutive limit-up count ending on each day. Suspension days (no bar) keep the streak."""
    f = flags.to_numpy().astype(bool)
    tr = np.ones_like(f) if traded is None else traded.reindex_like(flags).fillna(False).to_numpy().astype(bool)
    out = np.zeros(f.shape, dtype=int)
    for i in range(len(f)):
        prev = out[i - 1] if i else np.zeros(f.shape[1], dtype=int)
        out[i] = np.where(tr[i], np.where(f[i], prev + 1, 0), prev)
    return pd.DataFrame(out, index=flags.index, columns=flags.columns)


def _shape(n_up: int, max_streak: int, ret: float, one_word: int, path: pd.Series) -> str:
    """走势形态：连板一字 / 换手连板 / 趋势慢牛 / 底部反转 / 波段反复."""
    if max_streak >= 3 and one_word >= max(2, max_streak // 2):
        return "连板一字"
    if max_streak >= 3:
        return "换手连板"
    p = path.dropna()
    if len(p) < 5:
        return "波段反复"
    dd = (p / p.cummax() - 1).min()
    low_pos = int(np.argmin(p.to_numpy())) / max(1, len(p) - 1)
    if dd > -0.10:
        return "趋势慢牛"
    if low_pos <= 0.35 and p.iloc[-1] / p.min() - 1 > 0.5:
        return "底部反转"
    return "波段反复"


def _industry_map() -> pd.Series:
    p = RAW / "sw_cons.parquet"
    if not p.exists():
        return pd.Series(dtype=str)
    c = pd.read_parquet(p)
    from data.symbols import normalize
    c["symbol"] = c["stock"].map(lambda x: normalize(x))
    return c.drop_duplicates("symbol").set_index("symbol")["industry"]


def _em_industry() -> pd.Series:
    """东财二级行业 (from the fundlab financial table), finer than 申万一级."""
    try:
        from fundlab.store import FundStore
        f = FundStore().table("fin")
        return f.dropna(subset=["industry"]).drop_duplicates("symbol", keep="last").set_index("symbol")["industry"]
    except Exception:
        return pd.Series(dtype=str)


def _concepts() -> pd.Series:
    p = RAW / "concepts.parquet"
    if not p.exists():
        return pd.Series(dtype=object)
    c = pd.read_parquet(p)
    from data.symbols import normalize
    c["symbol"] = c["code"].map(lambda x: normalize(x) if str(x).isdigit() else None)
    # Skip giant catch-all boards; keep the more specific concepts first.
    size = c.groupby("concept")["symbol"].transform("size")
    c = c[size <= 400].assign(size=size).sort_values(["symbol", "size"])
    return c.groupby("symbol")["concept"].apply(lambda x: list(dict.fromkeys(x))[:4])


def _events(a, b) -> pd.DataFrame:
    try:
        from fundlab.store import FundStore
        e = FundStore().table("events")
        return e[(e["ann_date"] >= pd.Timestamp(a)) & (e["ann_date"] <= pd.Timestamp(b))]
    except Exception:
        return pd.DataFrame(columns=["symbol", "ann_date", "event", "title"])


def _forecasts(a, b) -> pd.DataFrame:
    try:
        from fundlab.store import FundStore
        f = FundStore().table("forecast")
        return f[(f["ann_date"] >= pd.Timestamp(a)) & (f["ann_date"] <= pd.Timestamp(b)) & (f["metric"] == "np")]
    except Exception:
        return pd.DataFrame(columns=["symbol", "ann_date", "kind", "pct"])


def _catalysts(symbols, a, b) -> dict[str, dict]:
    """Announcement-based catalyst hints per symbol (window: 20 days before the period to its end)."""
    ev = _events(pd.Timestamp(a) - pd.Timedelta(days=30), b)
    fc = _forecasts(pd.Timestamp(a) - pd.Timedelta(days=30), b)
    out = {}
    pri = ["ma_approved", "ma_draft", "ma_plan", "ma_progress", "control_change", "stake_transfer", "bankruptcy",
           "debt_restructure", "st_removed", "audit_cleared", "asset_sale", "placement", "incentive", "buyback_plan",
           "insider_buy", "spinoff", "impairment", "st_imposed", "delist_risk", "investigation", "insider_sell"]
    rank = {e: i for i, e in enumerate(pri)}
    sset = set(symbols)
    ev = ev[ev["symbol"].isin(sset)]
    for sym, g in ev.groupby("symbol"):
        g = g.assign(r=g["event"].map(rank).fillna(99)).sort_values(["r", "ann_date"])
        top = g.head(3)
        driver = next((DRIVER_BY_EVENT[e] for e in g["event"] if e in DRIVER_BY_EVENT), None)
        out[sym] = {"driver": driver, "events": [
            {"date": str(r.ann_date)[:10], "event": r.event, "title": r.title[:80]} for r in top.itertuples()]}
    for r in fc[fc["symbol"].isin(sset)].sort_values("ann_date").itertuples():
        d = out.setdefault(r.symbol, {"driver": None, "events": []})
        d["forecast"] = {"date": str(r.ann_date)[:10], "kind": r.kind, "pct": None if pd.isna(r.pct) else float(r.pct)}
        if d["driver"] is None and r.kind in ("预增", "扭亏", "略增", "续盈", "减亏"):
            d["driver"] = "业绩"
    return out


def _cap_bucket(v: float) -> str:
    for a, b, name in CAP_BUCKETS:
        if a <= v < b:
            return name
    return "未知"


def _float_cap(panel: Panel, d: pd.Timestamp) -> pd.Series:
    """流通市值 ≈ close x volume / (turnover%) on day d (turnover is on float shares)."""
    c = panel.close.loc[d]
    v = panel.volume.loc[d]
    to = panel.turnover.loc[d]
    return (c * v / (to / 100)).where(to > 0)


def _fmt(x, nd=4):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    if isinstance(x, (np.floating, float)):
        return round(float(x), nd)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (pd.Timestamp, date)):
        return str(x)[:10]
    return x


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: _fmt(v) for k, v in r.items()} for r in df.to_dict("records")]


# ---- index / valuation / macro ------------------------------------------------------
def index_table(a: pd.Timestamp, b: pd.Timestamp, prev_end: pd.Timestamp) -> list[dict]:
    p = RAW / "indices.parquet"
    if not p.exists():
        return []
    ix = pd.read_parquet(p)
    ix["date"] = pd.to_datetime(ix["date"])
    rows = []
    for name, g in ix.groupby("index", sort=False):
        g = g.sort_values("date").set_index("date")
        base = g[g.index <= prev_end]
        cur = g[(g.index >= a) & (g.index <= b)]
        if base.empty or cur.empty:
            continue
        b0 = base["close"].iloc[-1]
        ytd0 = g[g.index < pd.Timestamp(year=b.year, month=1, day=1)]
        y0 = ytd0["close"].iloc[-1] if len(ytd0) else g["close"].iloc[0]
        prev_amt = g[(g.index > prev_end - pd.Timedelta(days=40)) & (g.index <= prev_end)]
        hi250 = g[g.index <= b]["close"].tail(250)
        rows.append({
            "index": name, "ret": cur["close"].iloc[-1] / b0 - 1, "ytd": cur["close"].iloc[-1] / y0 - 1,
            "close": cur["close"].iloc[-1], "high": cur["high"].max(), "low": cur["low"].min(),
            "amplitude": cur["high"].max() / cur["low"].min() - 1,
            "avg_amount": cur["amount"].mean(),
            "upper_shadow": (cur["high"].max() - max(cur["close"].iloc[-1], cur["open"].iloc[0])) / b0,
            "lower_shadow": (min(cur["close"].iloc[-1], cur["open"].iloc[0]) - cur["low"].min()) / b0,
            "pos_250": (cur["close"].iloc[-1] - hi250.min()) / (hi250.max() - hi250.min()) if len(hi250) > 20 else None,
            "new_year_high": bool(cur["high"].max() >= g[(g.index >= pd.Timestamp(year=b.year, month=1, day=1)) & (g.index <= b)]["high"].max() - 1e-9),
            "ma60": g[g.index <= b]["close"].tail(60).mean(), "ma120": g[g.index <= b]["close"].tail(120).mean(),
        })
    return _records(pd.DataFrame(rows))


def valuation_table(b: pd.Timestamp) -> list[dict]:
    p = RAW / "index_pe.parquet"
    if not p.exists():
        return []
    v = pd.read_parquet(p)
    v["date"] = pd.to_datetime(v["date"])
    v = v[v["date"] <= b].dropna(subset=["pe"])
    out = []
    cgb = None
    cp = RAW / "macro" / "cgb.parquet"
    if cp.exists():
        c = pd.read_parquet(cp)
        c["日期"] = pd.to_datetime(c["日期"])
        c = c[(c["曲线名称"] == "中债国债收益率曲线") & (c["日期"] <= b)].sort_values("日期")
        if len(c):
            cgb = float(c["10年"].astype(float).iloc[-1])
    for name, g in v.groupby("index"):
        g = g.sort_values("date")
        pe = float(g["pe"].iloc[-1])
        hist = g[g["date"] >= b - pd.DateOffset(years=10)]["pe"]
        out.append({"index": name, "pe": pe, "pe_pct_10y": float((hist < pe).mean()),
                    "pe_min_10y": float(hist.min()), "pe_max_10y": float(hist.max()),
                    "erp": (1 / pe * 100 - cgb) if cgb is not None and name == "沪深300" else None,
                    "cgb10": cgb if name == "沪深300" else None})
    return _records(pd.DataFrame(out))


def macro_table(b: pd.Timestamp) -> dict:
    out = {}
    d = RAW / "macro"

    def mon(s):
        return pd.to_datetime(s.astype(str).str.replace("年", "-").str.replace("月份", ""), format="%Y-%m", errors="coerce")

    def rows(fname: str, cols: dict[str, str], month_fmt: str | None = None, lag: int = 1) -> list[dict]:
        """Last 4 data months already published by ``b``.

        ``lag`` = months between the data month and its release: PMI comes out on the last day of the
        same month (0); CPI / PPI / money supply in the middle of the next month (1).
        """
        p = d / fname
        if not p.exists():
            return []
        x = pd.read_parquet(p)
        if month_fmt:
            x["m"] = pd.to_datetime(x["月份"].astype(str), format=month_fmt, errors="coerce")
        else:
            x["m"] = mon(x["月份"])
        cut = (pd.Timestamp(b) - pd.DateOffset(months=lag)) if lag else pd.Timestamp(b)
        x = x[x["m"] <= cut].sort_values("m").tail(4)
        out_rows = []
        for _, r in x.iterrows():
            item = {"month": str(r["m"])[:7]}
            for k, c in cols.items():
                v = pd.to_numeric(r.get(c), errors="coerce")
                item[k] = None if pd.isna(v) else float(v)
            out_rows.append(item)
        return out_rows

    out["pmi"] = rows("pmi.parquet", {"mfg": "制造业-指数", "non_mfg": "非制造业-指数"}, lag=0)
    out["cpi_yoy"] = rows("cpi.parquet", {"yoy": "全国-同比增长"})
    out["ppi_yoy"] = rows("ppi.parquet", {"yoy": "当月同比增长"})
    out["money"] = rows("m2.parquet", {"m2_yoy": "货币和准货币(M2)-同比增长", "m1_yoy": "货币(M1)-同比增长"})
    if (d / "cgb.parquet").exists():
        c = pd.read_parquet(d / "cgb.parquet")
        c["日期"] = pd.to_datetime(c["日期"])
        c = c[(c["曲线名称"] == "中债国债收益率曲线") & (c["日期"] <= b)].sort_values("日期")
        if len(c):
            out["cgb10"] = [{"date": str(r["日期"])[:10], "y10": float(r["10年"])} for _, r in c.tail(1).iterrows()]
            m0 = c[c["日期"] <= b - pd.DateOffset(months=1)]
            if len(m0):
                out["cgb10_chg_bp"] = (float(c["10年"].iloc[-1]) - float(m0["10年"].iloc[-1])) * 100
    return out


def margin_table(days: pd.DatetimeIndex, prev_end: pd.Timestamp) -> dict:
    p = RAW / "margin.parquet"
    if not p.exists():
        return {}
    m = pd.read_parquet(p)
    m["date"] = pd.to_datetime(m["date"])
    m = m.sort_values("date").set_index("date")
    m["total"] = m["sh"] + m["sz"]
    m = m.ffill()
    cur = m[(m.index >= days[0]) & (m.index <= days[-1])]
    base = m[m.index <= prev_end]
    if cur.empty or base.empty:
        return {}
    return {"end": float(cur["total"].iloc[-1]), "change": float(cur["total"].iloc[-1] - base["total"].iloc[-1]),
            "start": float(base["total"].iloc[-1])}


def emotion_table(days: pd.DatetimeIndex, up: pd.DataFrame, dn: pd.DataFrame, streak: pd.DataFrame,
                  panel: Panel) -> list[dict]:
    """Daily emotion data. Limit counts from the local lake; broken-limit / premium from eastmoney pools when cached."""
    rows = []
    ret = panel.close_h.pct_change(fill_method=None)
    for d in days:
        prev_i = panel.dates.get_loc(d) - 1
        prem = None
        if prev_i >= 0:
            pd_ = panel.dates[prev_i]
            yz = up.columns[up.loc[pd_]] if pd_ in up.index else []
            if len(yz):
                prem = float(ret.loc[d, yz].mean())
        zb = None
        zp = RAW / "pools" / "zb" / f"{d:%Y%m%d}.parquet"
        if zp.exists():
            zb = len(pd.read_parquet(zp))
        n_up = int(up.loc[d].sum())
        st = panel.st.on(d.date())
        not_st = ~up.columns.isin(list(st))
        rows.append({"date": str(d)[:10], "limit_up": n_up, "limit_down": int(dn.loc[d].sum()),
                     "limit_up_ex_st": int((up.loc[d] & not_st).sum()), "limit_down_ex_st": int((dn.loc[d] & not_st).sum()),
                     "max_streak": int(streak.loc[d].max()),
                     "broken": zb, "broken_rate": zb / (zb + n_up) if zb is not None and zb + n_up > 0 else None,
                     "premium": prem, "up_ratio": float((ret.loc[d] > 0).sum() / ret.loc[d].notna().sum()),
                     "amount": float(panel.amount.loc[d].sum())})
    return _records(pd.DataFrame(rows))


def classify_emotion(row: dict) -> str:
    """SOP 周度 Step 3 五个阶段（单日口径，涨跌停按不含 ST 计，与东方财富涨停池一致）.

    冰点：跌停 ≥ 100，或跌停 ≥ 30 且最高板 ≤ 3 且涨停溢价为负
    分歧/退潮：跌停 ≥ 30，或炸板率 > 35% 且跌停 ≥ 15，或上涨占比 < 20%
    高潮：涨停 > 100 且上涨占比 > 50%
    发酵/主升：最高板 ≥ 5 且涨停 ≥ 60
    修复：其余
    """
    zu, zd = row.get("limit_up_ex_st", row["limit_up"]), row.get("limit_down_ex_st", row["limit_down"])
    ms, prem, br, upr = row["max_streak"], row.get("premium"), row.get("broken_rate"), row.get("up_ratio", 0.5)
    if zd >= 100 or (zd >= 30 and ms <= 3 and prem is not None and prem < 0):
        return "冰点"
    if zd >= 30 or (br is not None and br > 0.35 and zd >= 15) or upr < 0.2:
        return "分歧/退潮"
    if zu > 100 and upr > 0.5:
        return "高潮"
    if ms >= 5 and zu >= 60:
        return "发酵/主升"
    return "修复"


def industry_table(a: pd.Timestamp, b: pd.Timestamp, prev_end: pd.Timestamp, prev_a: pd.Timestamp) -> list[dict]:
    p = RAW / "sw_hist.parquet"
    if not p.exists():
        return []
    h = pd.read_parquet(p)
    h["date"] = pd.to_datetime(h["date"])
    out = []
    tot_cur = h[(h["date"] >= a) & (h["date"] <= b)]["amount"].sum()
    tot_prev = h[(h["date"] >= prev_a) & (h["date"] <= prev_end)]["amount"].sum()
    for (code, name), g in h.groupby(["code", "industry"]):
        g = g.sort_values("date").set_index("date")
        base = g[g.index <= prev_end]
        cur = g[(g.index >= a) & (g.index <= b)]
        prev = g[(g.index >= prev_a) & (g.index <= prev_end)]
        if base.empty or cur.empty:
            continue
        pb = g[g.index < prev_a]
        out.append({"code": code, "industry": name, "ret": cur["close"].iloc[-1] / base["close"].iloc[-1] - 1,
                    "prev_ret": (base["close"].iloc[-1] / pb["close"].iloc[-1] - 1) if len(pb) else None,
                    "amount_share": cur["amount"].sum() / tot_cur if tot_cur else None,
                    "prev_amount_share": prev["amount"].sum() / tot_prev if tot_prev and len(prev) else None,
                    "above_ma60": bool(cur["close"].iloc[-1] > g[g.index <= b]["close"].tail(60).mean())})
    df = pd.DataFrame(out).sort_values("ret", ascending=False)
    df["share_chg"] = df["amount_share"] - df["prev_amount_share"]
    return _records(df)


# ---- the main builder ----------------------------------------------------------------
def compute(a: pd.Timestamp, b: pd.Timestamp, kind: str, label: str, top_n: int, md: MarketData | None = None) -> dict:
    """Statistics for [a, b] (calendar bounds; trading days inside are used)."""
    md = md or MarketData()
    panel = Panel(md, a.date(), b.date())
    days = _period_days(panel, a, b)
    if len(days) == 0:
        raise ValueError(f"{label} 没有交易日数据")
    prev_end = panel.dates[panel.dates < days[0]][-1]
    if kind == "monthly":
        pa = pd.Timestamp(year=prev_end.year, month=prev_end.month, day=1)
    else:
        pa = prev_end - pd.Timedelta(days=prev_end.weekday())
    prev_days = _period_days(panel, pa, prev_end)

    ch = panel.close_h
    base = ch.loc[prev_end]
    end = ch.loc[days[-1]]
    ret = end / base - 1
    win = panel.window(days[0], days[-1])
    hi, lo = panel.high_h.loc[win], panel.low_h.loc[win]
    run_low = lo.cummin()
    # Low-to-high: best ratio of a high to the lowest low on or before that day.
    ratio = hi / run_low
    l2h = ratio.max() - 1
    # The high that achieved the max ratio, and the lowest low on or before that high.
    hi_day = ratio.idxmax()
    lo_arr, idx = lo.to_numpy(), lo.index
    hi_pos = {c: idx.get_loc(d) for c, d in hi_day.dropna().items()}
    low_day = pd.Series({c: idx[int(np.nanargmin(lo_arr[: hi_pos[c] + 1, j]))]
                         for j, c in enumerate(lo.columns) if c in hi_pos
                         and np.isfinite(lo_arr[: hi_pos[c] + 1, j]).any()})

    up_all, dn_all = _limit_flags(panel, panel.dates[(panel.dates >= prev_days[0] if len(prev_days) else days[0]) & (panel.dates <= days[-1])])
    up, dn = up_all.loc[days], dn_all.loc[days]
    streak_all = _streaks(up_all, panel.close.notna().reindex(up_all.index))
    streak = streak_all.loc[days]
    n_up = up.sum()
    one_word = (up & (panel.high_h.loc[days] - panel.low_h.loc[days]).abs().lt(1e-6)).sum()

    # Universe on the last day.
    alive = end.notna() & base.notna()
    bars_before = ch.loc[:prev_end].notna().sum()
    st_end = panel.st.on(days[-1].date())
    st_start = panel.st.on(days[0].date())
    is_new = bars_before < MIN_LIST_DAYS
    is_st = pd.Series(ch.columns.isin(list(st_end)), index=ch.columns)
    names = panel.stocks["name"].reindex(ch.columns)
    board = pd.Series([rules.board_of(s) for s in ch.columns], index=ch.columns)
    fcap0 = _float_cap(panel, prev_end)
    fcap1 = _float_cap(panel, days[-1])
    sw = _industry_map().reindex(ch.columns)
    emi = _em_industry().reindex(ch.columns)
    concepts = _concepts().reindex(ch.columns)
    amt = panel.amount.loc[days].sum()
    pos_hist = ch.loc[:prev_end].tail(250)
    pos_before = (base - pos_hist.min()) / (pos_hist.max() - pos_hist.min())
    vol_ratio = panel.amount.loc[days].mean() / panel.amount.loc[:prev_end].tail(60).mean()

    tbl = pd.DataFrame({
        "symbol": ch.columns, "name": names.values, "ret": ret.values, "l2h": l2h.reindex(ch.columns).values,
        "low_date": low_day.reindex(ch.columns).values, "high_date": hi_day.reindex(ch.columns).values,
        "limit_up_days": n_up.reindex(ch.columns).values, "max_streak": streak.max().reindex(ch.columns).values,
        "one_word": one_word.reindex(ch.columns).values,
        "limit_down_days": dn.sum().reindex(ch.columns).values,
        "float_cap0": fcap0.reindex(ch.columns).values, "float_cap1": fcap1.reindex(ch.columns).values,
        "board": board.values, "sw": sw.values, "industry": emi.values,
        "concepts": concepts.values, "amount": amt.reindex(ch.columns).values,
        "pos_250_before": pos_before.reindex(ch.columns).values, "vol_ratio": vol_ratio.reindex(ch.columns).values,
        "is_st": is_st.values, "is_new": is_new.reindex(ch.columns).values, "alive": alive.values,
        "was_st_start": ch.columns.isin(list(st_start)),
    }).set_index("symbol")
    tbl["cap_bucket"] = tbl["float_cap0"].map(lambda v: _cap_bucket(v) if pd.notna(v) else "未知")
    tbl["shape"] = [
        _shape(int(r.limit_up_days or 0), int(r.max_streak or 0), r.ret, int(r.one_word or 0),
               ch.loc[days[0]:days[-1], s]) if r.alive else None
        for s, r in tbl.iterrows()]
    t = tbl[tbl["alive"]].copy()
    main = t[~t["is_st"] & ~t["is_new"]].sort_values("ret", ascending=False)
    gainers = main.head(top_n).copy()
    losers = main.sort_values("ret").head(30 if kind == "monthly" else 20).copy()
    side_st = t[t["is_st"]].sort_values("ret", ascending=False).head(10)
    side_new = t[t["is_new"]].sort_values("ret", ascending=False).head(10)
    doubled_cc = t[t["ret"] >= 1.0].sort_values("ret", ascending=False)
    # Doublers: listed stocks only; IPOs inside the window (no limits on the first days) go to a side list.
    dl = tbl[(tbl["l2h"] >= 1.0)]
    # 退市整理期 (first day has no price limit; a -90% open then an intraday bounce is not a rally).
    delisting = dl["name"].fillna("").str.contains(r"退$|^退市")
    doubled_delist = dl[delisting].sort_values("l2h", ascending=False)
    dl = dl[~delisting]
    doubled_lh = dl[~dl["is_new"]].sort_values("l2h", ascending=False)
    doubled_new = dl[dl["is_new"]].sort_values("l2h", ascending=False)

    # Catalysts for everything we list.
    listed = set(gainers.index) | set(losers.index) | set(doubled_lh.index) | set(doubled_cc.index)
    cat = _catalysts(listed, days[0], days[-1])

    web = store.load_research(label) if kind == "monthly" else {}

    def attach(df):
        df = df.copy()
        df["rank"] = range(1, len(df) + 1)
        df["catalyst"] = [cat.get(s) for s in df.index]
        df["driver_hint"] = [(web.get(s) or {}).get("driver") or (cat.get(s) or {}).get("driver") for s in df.index]
        df["web"] = [web.get(s) for s in df.index]
        df["theme"] = [(web.get(s) or {}).get("theme") for s in df.index]
        return df.reset_index()

    gainers, losers, doubled_lh, doubled_cc = attach(gainers), attach(losers), attach(doubled_lh), attach(doubled_cc)
    doubled_new, doubled_delist = attach(doubled_new), attach(doubled_delist)
    side_st, side_new = attach(side_st), attach(side_new)

    # ---- breadth / style / volume ----
    rr = t["ret"]
    breadth = {
        "n": int(rr.notna().sum()), "median": float(rr.median()), "mean": float(rr.mean()),
        "up_share": float((rr > 0).mean()),
        "gt20": int((rr > 0.2).sum()), "lt_m20": int((rr < -0.2).sum()),
        "gt50": int((rr > 0.5).sum()), "doubled_cc": int((rr >= 1).sum()),
        "doubled_l2h": int(len(doubled_lh)), "doubled_l2h_new": int(len(doubled_new)),
        "doubled_l2h_delisting": int(len(doubled_delist)),
        "new_high_60": int((ch.loc[days[-1]] >= ch.loc[:days[-1]].tail(60).max() - 1e-9).sum()),
        "limit_up_total": int(up.values.sum()), "limit_down_total": int(dn.values.sum()),
    }
    amt_day = panel.amount.sum(axis=1)
    cur_amt = amt_day.loc[days].mean()
    prev_amt = amt_day.loc[prev_days].mean() if len(prev_days) else None
    yr = amt_day.loc[:days[-1]].tail(250)
    volume = {"avg_daily": float(cur_amt), "prev_avg_daily": float(prev_amt) if prev_amt else None,
              "chg": float(cur_amt / prev_amt - 1) if prev_amt else None,
              "pct_1y": float((yr < cur_amt).mean()),
              "max_day": str(amt_day.loc[days].idxmax())[:10], "max_amount": float(amt_day.loc[days].max())}
    by_board = t.groupby("board")["ret"].median().to_dict()
    by_cap = t.groupby("cap_bucket")["ret"].median().to_dict()

    # ---- gainers profile (SOP 5.3) ----
    def dist(df, col):
        return df[col].fillna("未知").value_counts().to_dict()
    gp = gainers.copy()
    gp["driver_hint"] = gp["driver_hint"].fillna("无公告催化")
    explode = gp.explode("concepts")
    profile = {"theme": dist(gp.head(20), "theme"), "board": dist(gp, "board"), "cap": dist(gp, "cap_bucket"), "sw": dist(gp, "sw"),
               "driver": dist(gp, "driver_hint"), "shape": dist(gp, "shape"),
               "concepts": explode["concepts"].dropna().value_counts().head(12).to_dict()}

    # ---- doubled genes (SOP 5.2 共性) ----
    dg = doubled_lh
    genes = {}
    if len(dg):
        genes = {"n": int(len(dg)), "median_cap0": float(dg["float_cap0"].median()),
                 "cap": dist(dg, "cap_bucket"), "board": dist(dg, "board"), "sw": dist(dg, "sw"),
                 "median_pos_250_before": float(dg["pos_250_before"].median()),
                 "low_pos_share": float((dg["pos_250_before"] < 0.3).mean()),
                 "median_vol_ratio": float(dg["vol_ratio"].median()),
                 "driver": dist(dg.assign(driver_hint=dg["driver_hint"].fillna("无公告催化")), "driver_hint"),
                 "shape": dist(dg, "shape"), "st_share": float(dg["is_st"].mean()), "theme": dist(dg, "theme"),
                 "concepts": dg.explode("concepts")["concepts"].dropna().value_counts().head(10).to_dict()}
        gd = _holders_change(dg["symbol"])
        if gd:
            genes["holders"] = gd

    # ---- daily emotion + leaders ----
    emotion = emotion_table(days, up, dn, streak, panel)
    for r in emotion:
        r["stage"] = classify_emotion(r)
    top_streak = streak.max()
    top_streak = top_streak[~top_streak.index.isin(list(st_end | st_start))]  # 总龙头不含 ST
    leader_sym = top_streak.idxmax() if len(top_streak) else None
    leaders = {
        "max_streak": {"symbol": leader_sym, "name": names.get(leader_sym), "streak": int(top_streak.max()),
                       "ret": _fmt(ret.get(leader_sym))} if leader_sym else None,
        "top_gainer": {"symbol": gainers["symbol"].iloc[0], "name": gainers["name"].iloc[0],
                       "ret": _fmt(gainers["ret"].iloc[0])} if len(gainers) else None,
        "ice_day": min(emotion, key=lambda r: r["limit_up_ex_st"] - r["limit_down_ex_st"])["date"],
        "hot_day": max(emotion, key=lambda r: r["limit_up_ex_st"] - r["limit_down_ex_st"])["date"],
    }
    # Weekly emotion curve inside a month.
    weeks = []
    if kind == "monthly":
        em = pd.DataFrame(emotion)
        em["date"] = pd.to_datetime(em["date"])
        for (y, w), g in em.groupby([em["date"].dt.isocalendar().year, em["date"].dt.isocalendar().week]):
            dd = pd.DatetimeIndex(g["date"])
            wb = panel.dates[panel.dates < dd[0]][-1]
            wr = (ch.loc[dd[-1]] / ch.loc[wb] - 1)
            wr = wr[alive & ~is_st]
            weeks.append({"week": f"{dd[0]:%m.%d}–{dd[-1]:%m.%d}", "days": len(dd),
                          "limit_up_avg": float(g["limit_up_ex_st"].mean()), "limit_down_avg": float(g["limit_down_ex_st"].mean()),
                          "max_streak": int(g["max_streak"].max()), "median_ret": float(wr.median()),
                          "stage": pd.Series([classify_emotion(r) for r in g.to_dict("records")]).mode().iloc[0],
                          "top": _records(t.assign(wret=wr).dropna(subset=["wret"]).sort_values("wret", ascending=False)
                                          .head(5).reset_index()[["symbol", "name", "wret"]])})

    weeks_idx = []
    wk = pd.Series(days, index=days)
    for _, g in wk.groupby([days.isocalendar().year, days.isocalendar().week]):
        weeks_idx.append(pd.DatetimeIndex(g.values))

    # ---- events summary (SOP 10.3 / 11) ----
    ev = _events(days[0], days[-1])
    ev_sum = ev.groupby("event").agg(n=("symbol", "size"), stocks=("symbol", "nunique")).reset_index() \
        .sort_values("n", ascending=False) if len(ev) else pd.DataFrame(columns=["event", "n", "stocks"])
    ma = _ma_reaction(ev, panel, days)
    fc = _forecasts(days[0], days[-1])
    fc_sum = fc["kind"].value_counts().to_dict() if len(fc) else {}

    # ---- modes (SOP 9.1, quantified where the lake allows) ----
    modes = _modes(t, up_all, streak_all, panel, days, ev, fc, ret)

    # ---- YTD doublers (SOP 5.2 年内翻倍累计名单) ----
    y0 = panel.dates[panel.dates < pd.Timestamp(year=b.year, month=1, day=1)]
    ytd = []
    if len(y0):
        yb = ch.loc[y0[-1]]
        yret = end / yb - 1
        yy = pd.DataFrame({"symbol": ch.columns, "name": names.values, "ytd": yret.values,
                           "is_st": is_st.values}).dropna(subset=["ytd"])
        yy = yy[yy["ytd"] >= 1].sort_values("ytd", ascending=False)
        prev_ytd = ch.loc[prev_end] / yb - 1
        yy["new"] = (prev_ytd.reindex(yy["symbol"]) < 1).to_numpy()
        dropped = prev_ytd[(prev_ytd >= 1) & (yret < 1)].index
        ytd = {"count": int(len(yy)), "new": int(yy["new"].sum()), "items": _records(yy.head(100)),
               "dropped": [{"symbol": s, "name": names.get(s), "ytd": _fmt(yret.get(s))} for s in dropped]}

    keep = ["rank", "symbol", "name", "ret", "l2h", "low_date", "high_date", "limit_up_days", "max_streak",
            "one_word", "limit_down_days", "float_cap0", "float_cap1", "board", "sw", "industry", "concepts",
            "cap_bucket", "shape", "pos_250_before", "vol_ratio", "is_st", "is_new", "driver_hint", "theme", "catalyst", "web"]

    def rec(df):
        return _records(df[[c for c in keep if c in df.columns]]) if len(df) else []

    out = {
        "kind": kind, "label": label, "start": str(days[0])[:10], "end": str(days[-1])[:10],
        "prev_end": str(prev_end)[:10], "trading_days": len(days),
        "summary": {"label": label, "trading_days": len(days), "median_ret": breadth["median"],
                    "up_share": breadth["up_share"], "doubled_cc": breadth["doubled_cc"],
                    "doubled_l2h": breadth["doubled_l2h"], "avg_amount": volume["avg_daily"],
                    "top": gainers["name"].head(5).tolist()},
        "indices": index_table(days[0], days[-1], prev_end),
        "valuation": valuation_table(days[-1]) if kind == "monthly" else [],
        "macro": macro_table(days[-1]) if kind == "monthly" else {},
        "margin": margin_table(days, prev_end),
        "breadth": breadth, "volume": volume, "style": {"board_median": by_board, "cap_median": by_cap},
        "emotion": emotion, "weeks": weeks, "leaders": leaders,
        "gainers": rec(gainers), "losers": rec(losers), "doubled_l2h": rec(doubled_lh), "doubled_cc": rec(doubled_cc),
        "doubled_new": rec(doubled_new), "doubled_delist": rec(doubled_delist),
        "concepts": concept_table(t, panel, days, weeks_idx),
        "side_st": rec(side_st), "side_new": rec(side_new),
        "profile": profile, "genes": genes, "ytd_doubled": ytd,
        "industries": industry_table(days[0], days[-1], prev_end, prev_days[0] if len(prev_days) else days[0]),
        "events": _records(ev_sum), "ma_reaction": ma, "forecasts": fc_sum, "modes": modes,
        "risk": _risk_list(ev, names), "supply": supply_table(panel, days, md),
        "method": {
            "ret": "区间涨幅 = 期末后复权收盘 / 上期末后复权收盘 − 1（与前复权口径一致）",
            "l2h": "低点到高点 = 区间内任一日最高价 / 该日及之前区间内最低价 − 1 的最大值（后复权）；区间内新上市、退市整理期的股票单列",
            "concepts": "题材 = 新浪概念板块（成分为 2026-10 下载时的成分，非历史成分），板块涨幅 = 成分股区间涨幅中位数",
            "main_list": f"主榜剔除期末 ST、期初上市不足 {MIN_LIST_DAYS} 个交易日的新股",
            "limit": "涨跌停按当日交易所涨跌幅限制和收盘价判断（主板 10%，ST 5%/2026-07-06 起 10%，创业板/科创板 20%，北交所 30%）；涨停/跌停家数含 ST，另给不含 ST 的口径（与东方财富涨停池一致）",
            "float_cap": "流通市值 ≈ 收盘价 × 成交量 / 换手率",
            "catalyst": "催化来自本地公告库（标题分类），时间窗为区间开始前 30 天至区间结束",
        },
    }
    return out


def concept_table(t: pd.DataFrame, panel: Panel, days, weeks_idx, top: int = 20) -> list[dict]:
    """题材强度（SOP 7.2）：新浪概念板块按成分股区间涨幅中位数排序。"""
    p = RAW / "concepts.parquet"
    if not p.exists():
        return []
    c = pd.read_parquet(p)
    from data.symbols import normalize
    c["symbol"] = c["code"].map(lambda x: normalize(x) if str(x).isdigit() else None)
    c = c[c["symbol"].isin(t.index)]
    ch = panel.close_h
    rows = []
    wk_ret = []
    for w in weeks_idx:
        wb = panel.dates[panel.dates < w[0]][-1]
        wk_ret.append(ch.loc[w[-1]] / ch.loc[wb] - 1)
    for name, g in c.groupby("concept"):
        syms = g["symbol"].unique()
        if len(syms) < 8:
            continue
        r = t.loc[syms]
        med = r["ret"].median()
        leader = r["ret"].idxmax()
        pos = r[r["ret"] > 0]
        core = pos["float_cap0"].idxmax() if len(pos) else None  # 中军: largest float cap among gainers
        zt = int(r["limit_up_days"].sum())
        weekly = [float(w.reindex(syms).median()) for w in wk_ret]
        rows.append({"concept": name, "n": len(syms), "median_ret": med, "mean_ret": r["ret"].mean(),
                     "up_share": float((r["ret"] > 0).mean()), "limit_up_days": zt,
                     "leader": leader, "leader_name": r.at[leader, "name"], "leader_ret": r.at[leader, "ret"],
                     "core": core, "core_name": r.at[core, "name"] if core else None,
                     "core_ret": r.at[core, "ret"] if core else None,
                     "weekly_median": weekly})
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    # Active weeks: weeks in which the concept was in the top 15 by weekly median return.
    W = np.array(df["weekly_median"].tolist())
    act = np.zeros(len(df), dtype=int)
    for j in range(W.shape[1]):
        col = np.nan_to_num(W[:, j], nan=-9)
        act[np.argsort(-col)[:15]] += 1
    df["active_weeks"] = act
    df = df.sort_values("median_ret", ascending=False)
    out = pd.concat([df.head(top), df.tail(8)]).drop_duplicates("concept")
    recs = _records(out.drop(columns=["weekly_median"]))
    for r0, w in zip(recs, out["weekly_median"]):
        r0["weekly_median"] = [_fmt(x) for x in w]
    return recs


def supply_table(panel: Panel, days, md: MarketData) -> dict:
    """SOP Step 2.4 市场供需：产业资本增减持、限售解禁、新股上市（金额按公告日附近收盘价估算）."""
    out = {}
    try:
        from fundlab.store import FundStore
        fs = FundStore()
        a, b = days[0], days[-1]
        h = fs.table("holder")
        h = h[(h["ann_date"] >= a) & (h["ann_date"] <= b) & ~h["is_company_plan"]]
        px = panel.close.ffill()
        def amt(r):
            if r.symbol not in px.columns:
                return np.nan
            i = min(int(px.index.searchsorted(r.ann_date)), len(px) - 1)
            return r.shares_wan * 1e4 * px[r.symbol].iloc[i]
        h = h.assign(amount=[amt(r) for r in h.itertuples()])
        inc, dec = h[h["direction"] > 0]["amount"].sum(), h[h["direction"] < 0]["amount"].sum()
        out["insider"] = {"buy": float(inc), "sell": float(dec), "net": float(inc - dec),
                          "buy_n": int(h[h["direction"] > 0]["symbol"].nunique()),
                          "sell_n": int(h[h["direction"] < 0]["symbol"].nunique())}
        u = fs.table("unlock")
        u = u[(u["date"] >= a) & (u["date"] <= b)]
        big = u.sort_values("value", ascending=False).head(5)
        names = panel.stocks["name"]
        out["unlock"] = {"value": float(u["value"].sum()), "n": int(u["symbol"].nunique()),
                         "top": [{"name": names.get(r.symbol), "date": str(r.date)[:10], "value": float(r.value)} for r in big.itertuples()]}
    except Exception:
        pass
    st = md.stocks()
    ld = pd.to_datetime(st["list_date"], errors="coerce")
    new = st[(ld >= days[0]) & (ld <= days[-1])]
    out["ipo"] = {"n": int(len(new)), "names": new["name"].tolist()[:20]}
    return out


def _holders_change(symbols) -> dict:
    p0, p1 = RAW / "gdhs" / "20260331.parquet", RAW / "gdhs" / "20260630.parquet"
    if not (p0.exists() and p1.exists()):
        return {}
    g = pd.read_parquet(p1)
    g["code"] = g["代码"].astype(str).str.zfill(6)
    codes = [s[:6] for s in symbols]
    x = g[g["code"].isin(codes)]
    if x.empty:
        return {}
    chg = pd.to_numeric(x["股东户数-增减比例"], errors="coerce")
    return {"n": int(chg.notna().sum()), "median_chg": float(chg.median()), "fell_share": float((chg < 0).mean()),
            "note": "股东户数 2026-03-31 → 2026-06-30 变化（下降 = 筹码集中）"}


def _ma_reaction(ev: pd.DataFrame, panel: Panel, days) -> dict:
    """Average next-day / 5-day return after first-time restructuring / control-change notices in the period."""
    out = {}
    ch = panel.close_h
    for kind, codes in (("重组首次披露", ["ma_plan"]), ("重组草案", ["ma_draft"]), ("重组获批", ["ma_approved"]),
                        ("重组终止", ["ma_terminated"]), ("控制权变更", ["control_change"])):
        e = ev[ev["event"].isin(codes)].drop_duplicates("symbol")
        r1, r5, names = [], [], []
        for r in e.itertuples():
            if r.symbol not in ch.columns:
                continue
            i = int(panel.dates.searchsorted(r.ann_date, side="right"))  # first trading day after the notice
            if i >= len(panel.dates) or i < 1:
                continue
            p0 = ch[r.symbol].iloc[i - 1]
            s = ch[r.symbol].iloc[i:i + 5]
            if not np.isfinite(p0) or s.dropna().empty:
                continue
            r1.append(s.dropna().iloc[0] / p0 - 1)
            r5.append(s.dropna().iloc[-1] / p0 - 1)
            names.append(r.symbol)
        out[kind] = {"n": len(r1), "next_day": float(np.mean(r1)) if r1 else None,
                     "day5": float(np.mean(r5)) if r5 else None,
                     "up_share": float(np.mean([x > 0 for x in r1])) if r1 else None}
    return out


def _risk_list(ev: pd.DataFrame, names: pd.Series) -> list[dict]:
    r = ev[ev["event"].isin(["investigation", "delist_risk", "delist_final", "st_imposed", "audit_adverse",
                              "funds_occupied"])]
    r = r.sort_values("ann_date").drop_duplicates(["symbol", "event"])
    return [{"date": str(x.ann_date)[:10], "symbol": x.symbol, "name": names.get(x.symbol), "event": x.event,
             "title": x.title[:80]} for x in r.itertuples()][:80]


def _modes(t, up_all, streak_all, panel, days, ev, fc, ret) -> list[dict]:
    """SOP 9.1 mode table with numbers the local data supports."""
    ch = panel.close_h
    nxt = ch.shift(-1) / ch - 1
    rows = []

    def add(name, value, evidence, reps):
        rows.append({"mode": name, "value": _fmt(value), "evidence": evidence, "examples": reps})

    # 连板接力: promotion rate from 2+ boards to the next level.
    s2 = streak_all.loc[days].shift(1)
    now = streak_all.loc[days]
    cand = (s2 >= 2).sum().sum()
    promo = ((s2 >= 2) & (now > s2)).sum().sum()
    add("连板接力（高标）", promo / cand if cand else None, f"2 板以上晋级率（{int(promo)}/{int(cand)}）",
        t.sort_values("max_streak", ascending=False).head(3)["name"].tolist())
    # 低位首板: next-day return after first limit-up.
    first = (now == 1)
    vals = nxt.loc[days][first].stack()
    add("低位首板/补涨", float(vals.mean()) if len(vals) else None, "首板次日平均涨幅", [])
    # 小微盘 / 大盘 by cap bucket median.
    cm = t.groupby("cap_bucket")["ret"].median()
    add("小微盘", cm.get("<30亿"), "流通市值 < 30 亿个股区间涨幅中位数", [])
    add("机构抱团/白马", cm.get(">500亿"), "流通市值 > 500 亿个股区间涨幅中位数", [])
    # 并购重组 / 控制权: stocks with such notices in the period.
    for name, codes in (("并购重组/控制权变更", ["ma_plan", "ma_draft", "ma_progress", "ma_approved",
                                                   "control_change", "stake_transfer"]),):
        syms = set(ev.loc[ev["event"].isin(codes), "symbol"]) & set(t.index)
        v = t.loc[list(syms), "ret"]
        add(name, float(v.median()) if len(v) else None, f"区间内有相关公告的 {len(v)} 只个股涨幅中位数",
            t.loc[list(syms)].sort_values("ret", ascending=False).head(3)["name"].tolist() if len(v) else [])
    good = set(fc.loc[fc["kind"].isin(["扭亏", "预增", "减亏"]), "symbol"]) & set(t.index) if len(fc) else set()
    v = t.loc[list(good), "ret"] if good else pd.Series(dtype=float)
    add("困境反转/业绩拐点", float(v.median()) if len(v) else None, f"区间内预增/扭亏/减亏预告 {len(v)} 只涨幅中位数",
        t.loc[list(good)].sort_values("ret", ascending=False).head(3)["name"].tolist() if len(v) else [])
    bj = t[t["board"] == "北交所"]["ret"]
    add("北交所", float(bj.median()) if len(bj) else None, "北交所个股涨幅中位数", [])
    nw = t[t["is_new"]]["ret"]
    add("新股/次新", float(nw.median()) if len(nw) else None, f"上市不足 {MIN_LIST_DAYS} 日个股 {len(nw)} 只涨幅中位数", [])
    return rows


# ---- public API -------------------------------------------------------------------------
def monthly(month: str, top_n: int = 50, md: MarketData | None = None, save: bool = True) -> dict:
    a = pd.Timestamp(f"{month}-01")
    b = a + pd.offsets.MonthEnd(0)
    r = compute(a, b, "monthly", month, top_n, md)
    if save:
        store.save_stats("monthly", month, r)
    return r


def weekly(day: str, top_n: int = 30, md: MarketData | None = None, save: bool = True) -> dict:
    d = pd.Timestamp(day)
    a = d - pd.Timedelta(days=d.weekday())
    b = a + pd.Timedelta(days=6)
    y, w, _ = a.isocalendar()
    label = f"{y}-W{w:02d}"
    r = compute(a, b, "weekly", label, top_n, md)
    if save:
        store.save_stats("weekly", label, r)
    return r
