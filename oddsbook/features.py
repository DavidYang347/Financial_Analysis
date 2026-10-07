"""Point-in-time cross-section for one signal day.

Everything here is computed from information public by the close of
``today``: bars up to today, reports and notices with ``ann_date <= today``,
month-end valuation history up to today.

Price basis: per-share values stored on cards (target, falsify price, buy
line) are on the hfq (后复权) basis, so they stay comparable with later
prices across dividends, bonus shares and splits. A per-share value from a
report for period ``p`` becomes hfq with ``value x F(p)`` (F = hfq factor).
hfq prices use the cumulative factor up to each day, so they carry no future
information.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from fundlab.store import FundStore

FINANCIAL_INDUSTRIES = ("银行", "证券", "保险", "多元金融")


class PriceBook:
    """Wide daily frames (index = date, columns = symbol) loaded once: raw close, hfq close, amount, turnover.

    Always read through ``upto(today)``, which cuts off everything after today.
    """

    def __init__(self, close: pd.DataFrame, close_h: pd.DataFrame, amount: pd.DataFrame, turnover: pd.DataFrame):
        self.close, self.close_h, self.amount, self.turnover = close, close_h, amount, turnover
        self.dates = close.index

    @classmethod
    def load(cls, md, start, end) -> "PriceBook":
        df = md.sql("""
            SELECT symbol, date, close, close * adj_factor AS close_h, amount, turnover
            FROM daily_hfq WHERE date BETWEEN ? AND ?
        """, [pd.Timestamp(start).date(), pd.Timestamp(end).date()])
        df["date"] = pd.to_datetime(df["date"])
        wide = {c: df.pivot(index="date", columns="symbol", values=c).sort_index() for c in
                ("close", "close_h", "amount", "turnover")}
        cols = wide["close"].columns
        return cls(*(wide[c].reindex(columns=cols) for c in ("close", "close_h", "amount", "turnover")))

    def upto(self, today, n: int | None = None) -> dict[str, pd.DataFrame]:
        i = int(self.dates.searchsorted(pd.Timestamp(today), side="right"))
        a = 0 if n is None else max(0, i - n)
        return {"close": self.close.iloc[a:i], "close_h": self.close_h.iloc[a:i],
                "amount": self.amount.iloc[a:i], "turnover": self.turnover.iloc[a:i]}

    def last(self, today, field: str = "close_h") -> pd.Series:
        """Values on ``today`` (NaN for suspended symbols)."""
        i = int(self.dates.searchsorted(pd.Timestamp(today), side="right")) - 1
        if i < 0 or self.dates[i] != pd.Timestamp(today):
            return pd.Series(dtype=float)
        return getattr(self, field).iloc[i]


def snapshot(today, stocks: pd.DataFrame, st_set: set, fs: FundStore, pb: PriceBook,
             only: set | None = None) -> pd.DataFrame:
    """One row per symbol trading today (index = symbol). ``only`` restricts it to a few symbols (cheap)."""
    t = pd.Timestamp(today)
    w = pb.upto(t, 250)
    close, close_h = w["close"], w["close_h"]
    if close.empty or close.index[-1] != t:
        return pd.DataFrame()
    syms = close.columns[close.iloc[-1].notna()]
    if only is not None:
        syms = syms[syms.isin(list(only))]
    c, ch = close[syms], close_h[syms]
    s = pd.DataFrame(index=pd.Index(syms, name="symbol"))
    s["close"] = c.iloc[-1]
    s["close_h"] = ch.iloc[-1]
    s["F"] = s["close_h"] / s["close"]
    s["bars"] = c.notna().sum()
    s["amount20"] = w["amount"][syms].tail(20).mean()
    s["turnover20"] = w["turnover"][syms].tail(20).mean()
    chf = ch.ffill()
    for n in (5, 20, 60, 120):
        s[f"ret{n}"] = chf.iloc[-1] / chf.iloc[-(n + 1)] - 1 if len(chf) > n else np.nan

    st = stocks.set_index("symbol")
    s["name"] = st["name"].reindex(s.index)
    s["board"] = st["board"].reindex(s.index)
    s["exchange"] = st["exchange"].reindex(s.index)
    s["is_st"] = s.index.isin(st_set)
    s["star_st"] = s["name"].fillna("").str.contains(r"\*ST") & s["is_st"]

    # ---- reports public by today ----
    fin_all = fs.table("fin")
    fin_all = fin_all[(fin_all["ann_date"] <= t) & fin_all["symbol"].isin(s.index)]
    fin_all = fin_all.sort_values(["symbol", "period"])
    g_all = fin_all.groupby("symbol", sort=False)
    last_fin = g_all.tail(1).set_index("symbol")
    cols = ["period", "ann_date", "industry", "np_ttm", "np_ded_ttm", "revenue_ttm", "ocf_ttm", "np_q", "ocf_q",
            "revenue_q", "bvps", "equity", "goodwill", "cash", "total_liab", "ap", "adv_receipts", "debt_ratio",
            "current_ratio", "shares", "f_period", "gross_margin", "total_assets"]
    s = s.join(last_fin[cols].rename(columns={"period": "fin_period", "ann_date": "fin_ann"}))
    s["industry"] = s["industry"].fillna("未知")
    s["is_financial"] = s["industry"].str.contains("|".join(FINANCIAL_INDUSTRIES))
    sh = s["shares"].where(s["shares"] > 0)
    fp = s["f_period"].fillna(1.0)
    s["shares_now"] = sh * s["F"] / fp       # share count on today's basis (after bonus shares / splits)
    s["mcap"] = s["close"] * s["shares_now"]
    bv = s["bvps"].where(s["bvps"].notna(), s["equity"] / sh)
    s["bvps_h"] = bv * fp
    s["tbvps_h"] = (s["equity"] - s["goodwill"].fillna(0)) / sh * fp
    s["eps_ttm_h"] = s["np_ttm"] / sh * fp
    # Interest-bearing debt is not in the free tables: all liabilities except trade payables and
    # advance receipts count as debt (conservative).
    debt = s["total_liab"] - s["ap"].fillna(0) - s["adv_receipts"].fillna(0)
    s["net_cash"] = s["cash"] - debt.clip(lower=0)
    s["pb"] = np.where(s["bvps_h"] > 0, s["close_h"] / s["bvps_h"], np.nan)
    s["pe"] = np.where(s["np_ttm"] > 0, s["mcap"] / s["np_ttm"], np.nan)

    # Last two quarters: operating cash flow before profit.
    q2 = g_all.tail(2)
    g2 = q2.groupby("symbol")
    s["ocf_q_pos2"] = ((g2["ocf_q"].min() > 0) & (g2.size() == 2)).reindex(s.index).fillna(False).astype(bool)
    s["np_q_prev"] = g2["np_q"].first().reindex(s.index)
    # Total liabilities falling over the last three reports.
    q3 = g_all.tail(3)
    first3 = q3.groupby("symbol")["total_liab"].first()
    last3 = q3.groupby("symbol")["total_liab"].last()
    mono = q3.groupby("symbol")["total_liab"].apply(lambda x: len(x) == 3 and x.is_monotonic_decreasing)
    s["liab_falling"] = (mono & (last3 < first3 * 0.95)).reindex(s.index).fillna(False).astype(bool)
    # Same period a year earlier (TTM profit trend).
    idx = fin_all.set_index(["symbol", "period"])["np_ttm"]
    key = pd.MultiIndex.from_arrays([s.index, s["fin_period"] - pd.DateOffset(years=1)])
    s["np_ttm_ly"] = idx.reindex(key).to_numpy()

    # ---- annual history ----
    fy = fin_all[fin_all["period"].dt.month == 12]
    fy5 = fy.groupby("symbol").tail(5)
    gf = fy5.groupby("symbol")
    s["fy_np"] = gf["np"].last().reindex(s.index)
    s["fy_rev"] = gf["revenue"].last().reindex(s.index)
    fy_last = fy5.groupby("symbol").tail(1).set_index("symbol")
    s["fy_min_profit"] = fy_last[["total_profit", "np", "np_ded"]].min(axis=1, skipna=True).reindex(s.index)

    def loss_streak(x: pd.Series) -> int:
        n = 0
        for v in reversed(x.tolist()):
            if pd.notna(v) and v < 0:
                n += 1
            else:
                break
        return n

    s["loss_years"] = gf["np"].apply(loss_streak).reindex(s.index).fillna(0).astype(int)
    pos = fy5[fy5["np"] > 0]
    s["norm_np_hist"] = pos.groupby("symbol")["np"].mean().reindex(s.index)
    s["pos_years"] = pos.groupby("symbol").size().reindex(s.index).fillna(0).astype(int)
    fy3 = fy.groupby("symbol").tail(3).groupby("symbol")
    s["ocf_fy3"] = fy3["ocf"].sum(min_count=1).reindex(s.index)
    s["np_fy3"] = fy3["np"].sum(min_count=1).reindex(s.index)

    # ---- latest forecast for a period not yet reported ----
    fc = fs.table("forecast")
    s["fc_period"], s["fc_kind"], s["fc_value"], s["fc_date"] = pd.NaT, None, np.nan, pd.NaT
    if not fc.empty:
        fc = fc[(fc["ann_date"] <= t) & (fc["metric"] == "np") & (fc["ann_date"] >= t - pd.Timedelta(days=200))
                & fc["symbol"].isin(s.index)]
        fc = fc.sort_values(["symbol", "ann_date"]).groupby("symbol").tail(1).set_index("symbol")
        fc = fc[fc["period"] > s["fin_period"].reindex(fc.index)]
        s["fc_period"] = fc["period"].reindex(s.index)
        s["fc_kind"] = fc["kind"].reindex(s.index)
        s["fc_value"] = fc["value"].reindex(s.index)
        s["fc_date"] = fc["ann_date"].reindex(s.index)

    # ---- valuation history (month ends up to today, current share basis) ----
    val = fs.table("valuation")
    val = val[(val["month_end"] <= t) & val["symbol"].isin(s.index)]
    pbh = val[(val["pb"] > 0) & (val["pb"] < 50)]
    gp = pbh.groupby("symbol")["pb"]
    s["pb_min"] = gp.min().reindex(s.index)
    s["pb_med"] = gp.median().reindex(s.index)
    s["pb_months"] = gp.size().reindex(s.index).fillna(0)
    now = pbh["symbol"].map(s["pb"])
    s["pb_pct"] = (pbh["pb"] < now).groupby(pbh["symbol"]).mean().reindex(s.index)

    # Industry medians at the latest month end (full cross-section, independent of ``only``).
    s["ind_pe"], s["ind_pb"] = np.nan, np.nan
    vall = fs.table("valuation")
    vall = vall[vall["month_end"] <= t]
    if len(vall):
        lm = vall[vall["month_end"] == vall["month_end"].max()]
        ind = fs.table("fin")
        ind = ind[ind["ann_date"] <= t].drop_duplicates("symbol", keep="last").set_index("symbol")["industry"]
        lm = lm.assign(industry=lm["symbol"].map(ind))
        ind_pe = lm[(lm["pe_ttm"] > 0) & (lm["pe_ttm"] < 100)].groupby("industry")["pe_ttm"].median()
        ind_pb = lm[(lm["pb"] > 0) & (lm["pb"] < 30)].groupby("industry")["pb"].median()
        s["ind_pe"] = s["industry"].map(ind_pe)
        s["ind_pb"] = s["industry"].map(ind_pb)

    # ---- pledge (company level, weekly) ----
    pl = fs.table("pledge")
    s["pledge"] = np.nan
    if not pl.empty:
        pl = pl[(pl["ann_date"] <= t) & (pl["ann_date"] >= t - pd.Timedelta(days=60))]
        s["pledge"] = pl.sort_values("ann_date").groupby("symbol")["ratio"].last().reindex(s.index)
    return s


def industry_turns(fs: FundStore, today) -> pd.DataFrame:
    """Channel F proxy: per industry, median single-quarter gross margin and revenue yoy by report period.

    An industry "turns" (底部企稳) when its median single-quarter gross margin
    rose in each of the last two reported quarters and median revenue growth
    stopped falling. It "rolls over" when the margin fell two quarters in a
    row. Only reports public by ``today`` count, and a quarter only counts once
    at least 60% of the industry has reported it.
    """
    t = pd.Timestamp(today)
    f = fs.table("fin")
    f = f[(f["ann_date"] <= t) & (f["period"] >= t - pd.DateOffset(years=3))].sort_values(["symbol", "period"]).copy()
    prev_cost = f.groupby("symbol")["cost"].shift(1)
    cost_q = np.where(f["period"].dt.month == 3, f["cost"], f["cost"] - prev_cost)
    f["gm_q"] = np.where(f["revenue_q"] > 0, 1 - cost_q / f["revenue_q"], np.nan)
    f.loc[(f["gm_q"] < -1) | (f["gm_q"] > 1), "gm_q"] = np.nan
    f["rev_yoy"] = f.groupby("symbol")["revenue_q"].pct_change(4, fill_method=None)
    stats = f.groupby(["industry", "period"]).agg(gm=("gm_q", "median"), rev_yoy=("rev_yoy", "median"),
                                                   n=("symbol", "nunique")).reset_index()
    full = f.groupby("industry")["symbol"].nunique()
    stats["coverage"] = stats["n"] / stats["industry"].map(full)
    stats = stats[stats["coverage"] >= 0.6].sort_values(["industry", "period"])
    out = []
    for ind, x in stats.groupby("industry"):
        if len(x) < 4 or full.get(ind, 0) < 5:
            continue
        gm, ry = x["gm"].to_numpy(), x["rev_yoy"].to_numpy()
        up2 = gm[-1] > gm[-2] > gm[-3]
        rev_ok = not np.isfinite(ry[-1]) or not np.isfinite(ry[-2]) or ry[-1] >= ry[-2] - 0.02
        # A turn from a low: the quarter before the rise was at or below the industry's own median.
        from_low = gm[-3] <= np.nanmedian(gm)
        out.append({"industry": ind, "period": x["period"].iloc[-1], "gm": gm[-1], "gm_prev": gm[-2],
                    "rev_yoy": ry[-1], "turning": bool(up2 and rev_ok and from_low),
                    "rolling_over": bool(gm[-1] < gm[-2] < gm[-3])})
    return pd.DataFrame(out, columns=["industry", "period", "gm", "gm_prev", "rev_yoy", "turning", "rolling_over"])
