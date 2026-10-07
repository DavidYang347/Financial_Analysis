"""Raw downloads -> point-in-time tables in ``data/lake/fundlab/``.

Point-in-time rule: every row carries ``ann_date``, the first day the
information was public. A strategy that reviews after the close of ``t`` and
trades at the next open may use rows with ``ann_date <= t`` (see fundlab.store).

Publish dates of financial reports. Eastmoney's period tables carry the
*latest update* date (restatements push it a year later), so it can't be
used as is. Order of preference: actual disclosure date from the 预约披露
table, baostock pubDate (delisted stocks), the eastmoney date when it is no
later than the statutory deadline, the statutory deadline itself.

Known simplification: values are as reported *now*; a later restatement
overwrites the original figure (no as-first-reported vintage in free data).
"""
from __future__ import annotations

import glob
import logging
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from data import config
from fundlab import events as ev
from fundlab.fetch import RAW

log = logging.getLogger(__name__)
OUT = config.LAKE_DIR / "fundlab"


# ---- helpers ------------------------------------------------------------------
def to_symbol(code) -> str | None:
    from data.symbols import normalize
    try:
        return normalize(str(code).zfill(6))
    except ValueError:
        return None


def _symbols(s: pd.Series) -> pd.Series:
    """Vectorised code -> 600000.SH (cached per unique code)."""
    u = {c: to_symbol(c) for c in pd.unique(s.astype(str))}
    return s.astype(str).map(u)


def _read_dir(kind: str, pattern: str = "*.parquet", tag: bool = False) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(RAW / kind / pattern))):
        try:
            df = pd.read_parquet(f)
        except Exception as e:
            log.warning("skip %s: %s", f, e)
            continue
        if df.empty:
            continue
        if tag:
            df["_file"] = Path(f).stem
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _dt(s) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.normalize()


def statutory_deadline(period: pd.Series) -> pd.Series:
    """Q1 -> 04-30, H1 -> 08-31, Q3 -> 10-31, FY -> next 04-30."""
    p = pd.to_datetime(period)
    m = p.dt.month
    y = p.dt.year
    out = pd.Series(pd.NaT, index=p.index, dtype="datetime64[ns]")
    for mon, (dy, dm, dd) in {3: (0, 4, 30), 6: (0, 8, 31), 9: (0, 10, 31), 12: (1, 4, 30)}.items():
        k = m == mon
        out[k] = pd.to_datetime(dict(year=y[k] + dy, month=dm, day=dd))
    return out


def _save(df: pd.DataFrame, name: str) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{name}.parquet"
    tmp = p.with_suffix(".tmp")
    df.reset_index(drop=True).to_parquet(tmp, index=False)
    tmp.replace(p)
    info = {"rows": int(len(df))}
    if "ann_date" in df and len(df):
        info["from"], info["to"] = str(df["ann_date"].min())[:10], str(df["ann_date"].max())[:10]
    return info


# ---- financials -----------------------------------------------------------------
ABSTRACT_MAP = {
    ("常用指标", "归母净利润"): "np",
    ("常用指标", "营业总收入"): "revenue",
    ("常用指标", "营业成本"): "cost",
    ("常用指标", "净利润"): "np_total",
    ("常用指标", "扣非净利润"): "np_ded",
    ("常用指标", "股东权益合计(净资产)"): "equity",
    ("常用指标", "商誉"): "goodwill",
    ("常用指标", "经营现金流量净额"): "ocf",
    ("常用指标", "基本每股收益"): "eps",
    ("常用指标", "每股净资产"): "bvps",
    ("常用指标", "资产负债率"): "debt_ratio",
    ("常用指标", "毛利率"): "gross_margin",
    ("每股指标", "每股营业总收入"): "rev_ps",
    ("财务风险", "流动比率"): "current_ratio",
}


def _abstract() -> pd.DataFrame:
    raw = _read_dir("abstract")
    if raw.empty:
        return pd.DataFrame(columns=["symbol", "period"])
    raw["key"] = list(zip(raw["选项"], raw["指标"]))
    raw = raw[raw["key"].isin(ABSTRACT_MAP)]
    raw["field"] = raw["key"].map(ABSTRACT_MAP)
    wide = raw.pivot_table(index=["symbol", "period"], columns="field", values="value", aggfunc="first").reset_index()
    wide.columns.name = None
    wide["period"] = pd.to_datetime(wide["period"], format="%Y%m%d")
    return wide


def _em_period(kind: str, cols: dict[str, str], date_col: str) -> pd.DataFrame:
    raw = _read_dir(kind, tag=True)
    if raw.empty:
        return pd.DataFrame(columns=["symbol", "period"])
    out = pd.DataFrame({"symbol": _symbols(raw["股票代码"]), "period": pd.to_datetime(raw["_file"], format="%Y%m%d")})
    for src, dst in cols.items():
        if src in raw:
            out[dst] = raw[src] if dst == "industry" else pd.to_numeric(raw[src], errors="coerce")
    out[f"em_date_{kind}"] = _dt(raw[date_col])
    return out.dropna(subset=["symbol"]).drop_duplicates(["symbol", "period"], keep="last")


def build_fin() -> pd.DataFrame:
    ab = _abstract()
    yj = _em_period("fin_yjbb", {"所处行业": "industry", "每股收益": "em_eps", "每股净资产": "em_bvps",
                                 "净利润-净利润": "em_np", "营业总收入-营业总收入": "em_revenue",
                                 "每股经营现金流量": "em_ocf_ps", "销售毛利率": "em_gm"}, "最新公告日期")
    zc = _em_period("fin_zcfz", {"资产-货币资金": "cash", "资产-应收账款": "ar", "资产-存货": "inventory",
                                 "资产-总资产": "total_assets", "负债-应付账款": "ap", "负债-预收账款": "adv_receipts",
                                 "负债-总负债": "total_liab", "股东权益合计": "em_equity"}, "公告日期")
    lr = _em_period("fin_lrb", {"营业利润": "op_profit", "利润总额": "total_profit",
                                "营业总支出-财务费用": "fin_expense", "净利润": "em_np_total"}, "公告日期")
    xj = _em_period("fin_xjll", {"经营性现金流-现金流量净额": "em_ocf", "投资性现金流-现金流量净额": "icf",
                                 "融资性现金流-现金流量净额": "fcf_fin"}, "公告日期")
    keys = pd.concat([d[["symbol", "period"]] for d in (ab, yj, zc, lr, xj)]).drop_duplicates()
    fin = keys
    for d in (ab, yj, zc, lr, xj):
        fin = fin.merge(d, on=["symbol", "period"], how="left")
    fin = fin[fin["period"] >= "2014-12-31"]
    # eastmoney's 业绩报表 includes NEEQ (新三板) companies whose codes look like old BSE codes.
    from data.query import MarketData
    listed = set(MarketData().stocks()["symbol"])
    fin = fin[fin["symbol"].isin(listed)]

    # Fill gaps in the Sina abstract from eastmoney.
    for a, b in (("np", "em_np"), ("revenue", "em_revenue"), ("ocf", "em_ocf"), ("eps", "em_eps"),
                 ("bvps", "em_bvps"), ("gross_margin", "em_gm"), ("equity", "em_equity"), ("np_total", "em_np_total")):
        if b in fin:
            fin[a] = fin[a].fillna(fin[b]) if a in fin else fin[b]
    if "debt_ratio" in fin:
        dr = fin["total_liab"] / fin["total_assets"] * 100
        fin["debt_ratio"] = fin["debt_ratio"].fillna(dr)

    # baostock for delisted stocks: publish dates + basic numbers when nothing else exists.
    bs = _read_dir("bs_fin")
    if not bs.empty:
        b = pd.DataFrame({"symbol": bs["symbol"], "period": _dt(bs["profit_statDate"]),
                          "bs_date": _dt(bs["profit_pubDate"]),
                          "bs_np": pd.to_numeric(bs["profit_netProfit"], errors="coerce"),
                          "bs_rev": pd.to_numeric(bs.get("profit_MBRevenue"), errors="coerce"),
                          "bs_shares": pd.to_numeric(bs.get("profit_totalShare"), errors="coerce"),
                          "bs_debt": pd.to_numeric(bs.get("balance_liabilityToAsset"), errors="coerce") * 100})
        b = b.dropna(subset=["period"]).drop_duplicates(["symbol", "period"])
        fin = fin.merge(b, on=["symbol", "period"], how="outer")
        fin["np"] = fin["np"].fillna(fin["bs_np"])
        fin["revenue"] = fin["revenue"].fillna(fin["bs_rev"])
        fin["debt_ratio"] = fin["debt_ratio"].fillna(fin["bs_debt"])
    else:
        fin["bs_date"] = pd.NaT
        fin["bs_shares"] = np.nan

    # ---- publish date ----
    dis = _read_dir("disclosure", tag=True)
    if not dis.empty:
        d = pd.DataFrame({"symbol": _symbols(dis["股票代码"]), "period": pd.to_datetime(dis["_file"], format="%Y%m%d"),
                          "dis_date": _dt(dis["实际披露"])}).dropna().drop_duplicates(["symbol", "period"])
        fin = fin.merge(d, on=["symbol", "period"], how="left")
    else:
        fin["dis_date"] = pd.NaT
    deadline = statutory_deadline(fin["period"])
    em_dates = fin[[c for c in fin.columns if c.startswith("em_date_")]].min(axis=1)
    em_ok = em_dates.where(em_dates <= deadline + pd.Timedelta(days=5))
    fin["ann_date"] = fin["dis_date"].fillna(fin["bs_date"]).fillna(em_ok).fillna(deadline)
    fin["ann_src"] = np.select([fin["dis_date"].notna(), fin["bs_date"].notna(), em_ok.notna()],
                               ["disclosure", "baostock", "eastmoney"], "deadline")
    # A report can't be public before its period ends.
    fin = fin[fin["ann_date"] > fin["period"]]

    # ---- shares (period-end) ----
    shares = fin["revenue"] / fin["rev_ps"]
    alt = fin["np"] / fin["eps"]
    shares = shares.where((fin["rev_ps"].abs() > 1e-6) & (shares > 0), alt.where(fin["eps"].abs() >= 0.01))
    fin["shares"] = shares.where(shares > 0).fillna(fin["bs_shares"])

    fin = fin.sort_values(["symbol", "period"]).reset_index(drop=True)
    fin = _add_ttm(fin)
    # hfq factor at period end: per-share value x f_period is on the hfq price basis,
    # so it can be compared with hfq prices on any later day (splits and bonus shares included).
    af = MarketData().sql("SELECT symbol, date, adj_factor FROM adj_factor ORDER BY date")
    af["date"] = pd.to_datetime(af["date"]).astype("datetime64[ns]")
    fin = pd.merge_asof(fin.sort_values("period"), af.rename(columns={"date": "period", "adj_factor": "f_period"}),
                        on="period", by="symbol", direction="backward")
    fin["f_period"] = fin["f_period"].fillna(1.0)
    fin = fin.sort_values(["symbol", "period"]).reset_index(drop=True)
    fin["industry"] = fin.groupby("symbol")["industry"].transform(lambda s: s.ffill().bfill())
    keep = ["symbol", "period", "ann_date", "ann_src", "industry", "revenue", "cost", "np", "np_total", "np_ded",
            "ocf", "icf", "eps", "bvps", "equity", "goodwill", "cash", "ar", "inventory", "total_assets",
            "total_liab", "ap", "adv_receipts", "debt_ratio", "gross_margin", "current_ratio", "op_profit",
            "total_profit", "fin_expense", "shares", "f_period",
            "np_ttm", "np_ded_ttm", "revenue_ttm", "ocf_ttm", "np_q", "np_ded_q", "revenue_q", "ocf_q"]
    for c in keep:
        if c not in fin:
            fin[c] = np.nan
    return fin[keep]


def _add_ttm(fin: pd.DataFrame) -> pd.DataFrame:
    """TTM and single-quarter values from year-to-date figures."""
    f = fin.set_index(["symbol", "period"])
    y = fin["period"].dt.year.to_numpy()
    q_end = fin["period"]
    prev_fy = pd.to_datetime(dict(year=y - 1, month=12, day=31))
    prev_same = q_end - pd.DateOffset(years=1)
    prev_same = prev_same + pd.offsets.MonthEnd(0)
    prev_q = (q_end - pd.offsets.QuarterEnd(1))
    for c in ("np", "np_ded", "revenue", "ocf"):
        s = f[c]
        a = s.reindex(pd.MultiIndex.from_arrays([fin["symbol"], prev_fy])).to_numpy()
        b = s.reindex(pd.MultiIndex.from_arrays([fin["symbol"], prev_same])).to_numpy()
        cur = fin[c].to_numpy()
        is_fy = (fin["period"].dt.month == 12).to_numpy()
        fin[f"{c}_ttm"] = np.where(is_fy, cur, cur + a - b)
        p = s.reindex(pd.MultiIndex.from_arrays([fin["symbol"], prev_q])).to_numpy()
        is_q1 = (fin["period"].dt.month == 3).to_numpy()
        fin[f"{c}_q"] = np.where(is_q1, cur, cur - p)
    return fin


# ---- forecasts -----------------------------------------------------------------
def build_forecast() -> pd.DataFrame:
    raw = _read_dir("forecast", tag=True)
    if raw.empty:
        return pd.DataFrame()
    out = pd.DataFrame({
        "symbol": _symbols(raw["股票代码"]), "period": pd.to_datetime(raw["_file"], format="%Y%m%d"),
        "ann_date": _dt(raw["公告日期"]), "metric": raw["预测指标"].astype(str), "kind": raw["预告类型"].astype(str),
        "value": pd.to_numeric(raw["预测数值"], errors="coerce"), "pct": pd.to_numeric(raw["业绩变动幅度"], errors="coerce"),
        "prior": pd.to_numeric(raw["上年同期值"], errors="coerce"), "reason": raw["业绩变动原因"].astype(str).str[:300],
    })
    out = out[out["metric"].str.contains("净利润")].dropna(subset=["symbol", "ann_date"])
    out["metric"] = np.where(out["metric"].str.contains("扣除"), "np_ded", "np")
    return out.drop_duplicates(["symbol", "period", "ann_date", "metric"]).sort_values(["ann_date", "symbol"])


# ---- announcements -----------------------------------------------------------------
NOTICE_DIRS = ["notice_holding", "notice_restructure", "notice_risk", "notice_major", "notice_finance",
               "notice_info", "notice_all"]


def build_events() -> pd.DataFrame:
    frames = []
    for d in NOTICE_DIRS:
        raw = _read_dir(d)
        if raw.empty:
            continue
        frames.append(pd.DataFrame({"code": raw["代码"].astype(str), "ann_date": _dt(raw["公告日期"]),
                                    "title": raw["公告标题"].astype(str), "category": raw["公告类型"].astype(str),
                                    "src": d}))
    if not frames:
        return pd.DataFrame(columns=["symbol", "ann_date", "event", "channel", "title", "category"])
    n = pd.concat(frames, ignore_index=True)
    n = n.drop_duplicates(["code", "ann_date", "title"])
    n["event"] = ev.classify(n)
    n = n.dropna(subset=["event"])
    n["symbol"] = _symbols(n["code"])
    n["channel"] = n["event"].map(ev.EVENT_CHANNEL)
    n = n.dropna(subset=["symbol", "ann_date"])
    cols = ["symbol", "ann_date", "event", "channel", "title", "category", "src"]
    return n[cols].sort_values(["ann_date", "symbol"]).reset_index(drop=True)


def notice_coverage() -> pd.DataFrame:
    """First / last day downloaded per notice feed (strategies use it to know what they can see)."""
    rows = []
    for d in NOTICE_DIRS:
        fs = sorted(glob.glob(str(RAW / d / "*.parquet")))
        if fs:
            rows.append({"src": d, "first": Path(fs[0]).stem, "last": Path(fs[-1]).stem, "days": len(fs)})
    return pd.DataFrame(rows)


# ---- holders, buybacks, unlocks, pledge ---------------------------------------------
def build_holder() -> pd.DataFrame:
    frames = []
    for kind, direction in (("holder_inc", 1), ("holder_dec", -1)):
        raw = _read_dir(kind)
        if raw.empty:
            continue
        frames.append(pd.DataFrame({
            "symbol": _symbols(raw["代码"]), "ann_date": _dt(raw["公告日"]), "start": _dt(raw["变动开始日"]),
            "end": _dt(raw["变动截止日"]), "direction": direction, "holder": raw["股东名称"].astype(str),
            "shares_wan": pd.to_numeric(raw["持股变动信息-变动数量"], errors="coerce"),
            "pct_total": pd.to_numeric(raw["持股变动信息-占总股本比例"], errors="coerce"),
            "pct_after": pd.to_numeric(raw["变动后持股情况-占总股本比例"], errors="coerce"),
        }))
    if not frames:
        return pd.DataFrame()
    h = pd.concat(frames, ignore_index=True).dropna(subset=["symbol", "ann_date"])
    h = h[h["ann_date"] >= "2015-01-01"]
    h["is_company_plan"] = h["holder"].str.contains("员工持股|股权激励|回购专用")
    return h.drop_duplicates().sort_values(["ann_date", "symbol"])


def build_repurchase() -> pd.DataFrame:
    raw = _read_dir("repurchase")
    if raw.empty:
        return pd.DataFrame()
    r = pd.DataFrame({
        "symbol": _symbols(raw["股票代码"]),
        # 最新公告日期 moves with every progress notice; the plan start is the first public date.
        "ann_date": _dt(raw["回购起始时间"]),
        "price_cap": pd.to_numeric(raw["计划回购价格区间"], errors="coerce"),
        "pct_low": pd.to_numeric(raw["占公告前一日总股本比例-下限"], errors="coerce"),
        "pct_high": pd.to_numeric(raw["占公告前一日总股本比例-上限"], errors="coerce"),
        "amount_low": pd.to_numeric(raw["计划回购金额区间-下限"], errors="coerce"),
        "amount_high": pd.to_numeric(raw["计划回购金额区间-上限"], errors="coerce"),
    })
    return r.dropna(subset=["symbol", "ann_date"]).drop_duplicates().sort_values(["ann_date", "symbol"])


def build_unlock() -> pd.DataFrame:
    raw = _read_dir("unlock")
    if raw.empty:
        return pd.DataFrame()
    u = pd.DataFrame({
        "symbol": _symbols(raw["股票代码"]), "date": _dt(raw["解禁时间"]), "kind": raw["限售股类型"].astype(str),
        "shares": pd.to_numeric(raw["实际解禁数量"], errors="coerce"),
        "value": pd.to_numeric(raw["实际解禁市值"], errors="coerce"),
        "ratio": pd.to_numeric(raw["占解禁前流通市值比例"], errors="coerce"),
    })
    return u.dropna(subset=["symbol", "date"]).drop_duplicates().sort_values(["date", "symbol"])


def build_pledge() -> pd.DataFrame:
    raw = _read_dir("pledge")
    if raw.empty:
        return pd.DataFrame()
    p = pd.DataFrame({"symbol": _symbols(raw["股票代码"]), "ann_date": _dt(raw["交易日期"]),
                      "ratio": pd.to_numeric(raw["质押比例"], errors="coerce")})
    return p.dropna().drop_duplicates(["symbol", "ann_date"]).sort_values(["ann_date", "symbol"])


# ---- valuation history -----------------------------------------------------------
def build_valuation(fin: pd.DataFrame, md=None) -> pd.DataFrame:
    """Month-end PB / PE(TTM) / market cap per symbol, all on the information known at that month end.

    Per-share book value and earnings are restated to the current share basis
    with the hfq factor: value_per_share(t) = value_per_share(period) * F(period end) / F(t),
    so bonus shares and splits after the period don't distort the ratios.
    """
    from data.query import MarketData
    md = md or MarketData()
    px = md.sql("""
        WITH m AS (
            SELECT symbol, date, close, adj_factor, row_number() OVER (
                PARTITION BY symbol, year(date), month(date) ORDER BY date DESC) AS rn
            FROM daily_hfq WHERE date >= DATE '2015-06-30'
        )
        SELECT symbol, date, close, adj_factor FROM m WHERE rn = 1 ORDER BY date
    """)
    px["date"] = pd.to_datetime(px["date"]).astype("datetime64[ns]")
    af = md.sql("SELECT symbol, date, adj_factor FROM adj_factor ORDER BY date")
    af["date"] = pd.to_datetime(af["date"]).astype("datetime64[ns]")
    f = fin[["symbol", "period", "ann_date", "bvps", "np_ttm", "shares", "equity", "goodwill"]].copy()
    f = f.dropna(subset=["ann_date"]).sort_values("period")
    f = pd.merge_asof(f, af.rename(columns={"date": "period", "adj_factor": "f_period"}),
                      on="period", by="symbol", direction="backward")
    f["f_period"] = f["f_period"].fillna(1.0)
    # Information known at ann_date; at each month end take the latest announced period.
    f = f.sort_values(["ann_date", "period"]).drop_duplicates(["symbol", "ann_date"], keep="last")
    f = f.sort_values("ann_date")
    v = pd.merge_asof(px.sort_values("date"), f.rename(columns={"ann_date": "date"}), on="date", by="symbol",
                      direction="backward", allow_exact_matches=False)
    k = v["f_period"] / v["adj_factor"]
    v["bvps_now"] = v["bvps"] * k
    v["shares_now"] = v["shares"] / k
    v["mcap"] = v["close"] * v["shares_now"]
    v["pb"] = np.where(v["bvps_now"] > 0, v["close"] / v["bvps_now"], np.nan)
    v["pe_ttm"] = np.where(v["np_ttm"] > 0, v["mcap"] / v["np_ttm"], np.nan)
    out = v[["symbol", "date", "close", "pb", "pe_ttm", "mcap"]].dropna(subset=["pb", "pe_ttm", "mcap"], how="all")
    return out.rename(columns={"date": "month_end"}).reset_index(drop=True)


def build_all() -> dict:
    out: dict = {}
    fin = build_fin()
    out["fin"] = _save(fin, "fin")
    out["forecast"] = _save(build_forecast(), "forecast")
    out["events"] = _save(build_events(), "events")
    out["holder"] = _save(build_holder(), "holder")
    out["repurchase"] = _save(build_repurchase(), "repurchase")
    out["unlock"] = _save(build_unlock(), "unlock")
    out["pledge"] = _save(build_pledge(), "pledge")
    out["valuation"] = _save(build_valuation(fin), "valuation")
    cov = notice_coverage()
    _save(cov, "notice_coverage")
    out["notice_coverage"] = cov.to_dict("records")
    return out
