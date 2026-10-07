"""Merge the raw downloads into point-in-time tables under ``<lake>/fundamentals/``.

Every row carries the date it became public (``ann_date``); nothing here looks
at data a strategy could not have seen on that day.

    fin.parquet         one row per (symbol, period): statements merged, ann_date = latest of the four
    forecast.parquet    earnings pre-announcements
    pledge.parquet      weekly pledge ratio (date = the Friday of the snapshot)
    holder.parquet      major holders' / executives' buys and sells
    repurchase.parquet  buy-back plans
    unlock.parquet      restricted-share releases (a calendar: ``date`` is known in advance)
    notices.parquet     classified announcement titles (see fundamentals.events)

Known limitations (documented in the README):
  * Eastmoney serves the *current* value of old periods, so later restatements leak
    into old rows. ``ann_date`` is the latest announcement date Eastmoney shows,
    which delays (never advances) a restated row.
  * Old BSE codes (43/83/87) are dropped; only symbols present in the stock list are kept.
"""
from __future__ import annotations

import glob
from pathlib import Path

import numpy as np
import pandas as pd

from data import config
from data import symbols as sym
from fundamentals.fetch import RAW_DIR

OUT_DIR = config.LAKE_DIR / "fundamentals"

FIN_MAP = {
    "fin_yjbb": {"每股收益": "eps", "营业总收入-营业总收入": "revenue", "营业总收入-同比增长": "rev_yoy",
                 "净利润-净利润": "np_parent", "净利润-同比增长": "np_yoy", "每股净资产": "bvps",
                 "净资产收益率": "roe", "每股经营现金流量": "ocf_ps", "销售毛利率": "gross_margin",
                 "所处行业": "industry", "最新公告日期": "ann_yjbb"},
    "fin_zcfz": {"资产-货币资金": "cash", "资产-应收账款": "ar", "资产-存货": "inventory",
                 "资产-总资产": "total_assets", "负债-总负债": "total_liab", "资产负债率": "debt_ratio",
                 "股东权益合计": "equity", "公告日期": "ann_zcfz"},
    "fin_lrb": {"净利润": "np_total", "营业利润": "op_profit", "利润总额": "total_profit",
                "营业总支出-财务费用": "fin_expense", "公告日期": "ann_lrb"},
    "fin_xjll": {"经营性现金流-现金流量净额": "ocf", "投资性现金流-现金流量净额": "icf",
                 "融资性现金流-现金流量净额": "fcf", "公告日期": "ann_xjll"},
}


def to_symbol(code) -> str | None:
    try:
        s = sym.normalize(str(code).zfill(6))
    except ValueError:
        return None
    return s if sym.is_a_share(s) else None


def _read(dataset: str) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(RAW_DIR / dataset / "*.parquet"))):
        df = pd.read_parquet(f)
        if len(df):
            df["_key"] = Path(f).stem
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _known_symbols() -> set[str]:
    from data.query import MarketData
    return set(MarketData().stocks()["symbol"])


def _code_col(df: pd.DataFrame) -> str:
    return "股票代码" if "股票代码" in df.columns else "代码"


def _write(df: pd.DataFrame, name: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_DIR / f"{name}.parquet", index=False)
    print(f"  {name}: {len(df)} rows")


def build_fin(known: set[str]) -> None:
    merged: pd.DataFrame | None = None
    for ds, mp in FIN_MAP.items():
        df = _read(ds)
        df["symbol"] = df["股票代码"].map(to_symbol)
        df = df[df["symbol"].isin(known)].copy()
        df["period"] = pd.to_datetime(df["_key"], format="%Y%m%d")
        df = df.rename(columns=mp)[["symbol", "period", *mp.values()]]
        for c in [c for c in df.columns if c.startswith("ann_")]:
            df[c] = pd.to_datetime(df[c], errors="coerce")
        df = df.drop_duplicates(["symbol", "period"], keep="last")
        merged = df if merged is None else merged.merge(df, on=["symbol", "period"], how="outer")
    ann_cols = [c for c in merged.columns if c.startswith("ann_")]
    merged["ann_date"] = merged[ann_cols].max(axis=1)  # the whole row is public once the last statement is
    # Prefer the exchange's record of the original disclosure day: Eastmoney's "latest announcement
    # date" moves when a report is amended, which would make old rows look newer than they were.
    disc = _read("disclosure")
    if len(disc):
        disc["symbol"] = disc["股票代码"].map(to_symbol)
        disc["period"] = pd.to_datetime(disc["_key"], format="%Y%m%d")
        disc["ann_actual"] = pd.to_datetime(disc["实际披露"], errors="coerce")
        disc = disc.dropna(subset=["symbol", "ann_actual"]).drop_duplicates(["symbol", "period"])
        merged = merged.merge(disc[["symbol", "period", "ann_actual"]], on=["symbol", "period"], how="left")
        merged["ann_date"] = merged["ann_actual"].fillna(merged["ann_date"])
        merged = merged.drop(columns="ann_actual")
    merged = merged.drop(columns=ann_cols).dropna(subset=["ann_date"])
    # Per-share figures on a hfq basis: bvps_h = bvps × F(ann_date). Dividing by the factor of any later day
    # gives the figure on that day's share basis (bonus issues and splits are already in the factor).
    from data.query import MarketData
    from fundamentals.pit import AdjFactors
    merged = merged.reset_index(drop=True)
    merged["F_ann"] = AdjFactors(MarketData()).at(merged["symbol"], merged["ann_date"])
    merged["bvps_h"] = merged["bvps"] * merged["F_ann"]
    merged["eps_h"] = merged["eps"] * merged["F_ann"]
    # Share count at the report date. Parent profit / EPS is the cleanest source we have; total equity
    # includes minorities, so equity / bvps overstates shares and is only the fallback.
    eps, npp = merged["eps"], merged["np_parent"]
    by_eps = np.where(eps.abs() >= 0.03, npp / eps, np.nan)
    by_eq = np.where(merged["bvps"] > 0.05, merged["equity"] / merged["bvps"], np.nan)
    shares = pd.Series(by_eps, index=merged.index)
    shares = shares.where(shares > 1e6, pd.Series(by_eq, index=merged.index))
    merged["shares"] = shares  # NaN when neither works
    _write(merged.sort_values(["symbol", "period"]).reset_index(drop=True), "fin")


def build_forecast(known: set[str]) -> None:
    df = _read("forecast")
    df["symbol"] = df["股票代码"].map(to_symbol)
    df = df[df["symbol"].isin(known)].copy()
    out = pd.DataFrame({
        "symbol": df["symbol"], "period": pd.to_datetime(df["_key"], format="%Y%m%d"),
        "ann_date": pd.to_datetime(df["公告日期"], errors="coerce"),
        "metric": df["预测指标"], "kind": df["预告类型"], "value": pd.to_numeric(df["预测数值"], errors="coerce"),
        "pct": pd.to_numeric(df["业绩变动幅度"], errors="coerce"),
        "prior": pd.to_numeric(df["上年同期值"], errors="coerce"),
    }).dropna(subset=["ann_date"])
    _write(out.sort_values(["symbol", "ann_date"]).reset_index(drop=True), "forecast")


def build_pledge(known: set[str]) -> None:
    df = _read("pledge")
    df["symbol"] = df["股票代码"].map(to_symbol)
    df = df[df["symbol"].isin(known)]
    out = pd.DataFrame({"symbol": df["symbol"], "date": pd.to_datetime(df["_key"], format="%Y%m%d"),
                        "ratio": pd.to_numeric(df["质押比例"], errors="coerce")}).dropna()
    _write(out.drop_duplicates(["symbol", "date"]).sort_values(["symbol", "date"]).reset_index(drop=True), "pledge")


def build_holder(known: set[str]) -> None:
    frames = []
    for ds in ("holder_inc", "holder_dec"):
        df = _read(ds)
        df["symbol"] = df["代码"].map(to_symbol)
        df = df[df["symbol"].isin(known)]
        frames.append(pd.DataFrame({
            "symbol": df["symbol"], "ann_date": pd.to_datetime(df["公告日"], errors="coerce"),
            "start": pd.to_datetime(df["变动开始日"], errors="coerce"),
            "end": pd.to_datetime(df["变动截止日"], errors="coerce"),
            "direction": df["持股变动信息-增减"], "holder": df["股东名称"],
            "pct_total": pd.to_numeric(df["持股变动信息-占总股本比例"], errors="coerce"),
            "pct_after": pd.to_numeric(df["变动后持股情况-占总股本比例"], errors="coerce"),
        }))
    out = pd.concat(frames, ignore_index=True).dropna(subset=["ann_date"])
    out = out[out["ann_date"] >= "2023-01-01"]
    _write(out.sort_values(["symbol", "ann_date"]).reset_index(drop=True), "holder")


def build_repurchase(known: set[str]) -> None:
    df = _read("repurchase")
    df["symbol"] = df["股票代码"].map(to_symbol)
    df = df[df["symbol"].isin(known)]
    out = pd.DataFrame({
        "symbol": df["symbol"], "ann_date": pd.to_datetime(df["最新公告日期"], errors="coerce"),
        "start": pd.to_datetime(df["回购起始时间"], errors="coerce"), "progress": df["实施进度"],
        "price_cap": pd.to_numeric(df["计划回购价格区间"], errors="coerce"),
        "pct_low": pd.to_numeric(df["占公告前一日总股本比例-下限"], errors="coerce"),
        "pct_high": pd.to_numeric(df["占公告前一日总股本比例-上限"], errors="coerce"),
        "amount_low": pd.to_numeric(df["计划回购金额区间-下限"], errors="coerce"),
    })
    # Only the latest announcement date is served, so use the plan start date as the public date
    # when it is earlier (plans are announced before they start, never after).
    out["ann_date"] = out[["ann_date", "start"]].min(axis=1)
    _write(out.dropna(subset=["ann_date"]).sort_values(["symbol", "ann_date"]).reset_index(drop=True), "repurchase")


def build_unlock(known: set[str]) -> None:
    df = _read("unlock")
    df["symbol"] = df["股票代码"].map(to_symbol)
    df = df[df["symbol"].isin(known)]
    out = pd.DataFrame({
        "symbol": df["symbol"], "date": pd.to_datetime(df["解禁时间"], errors="coerce"),
        "kind": df["限售股类型"], "shares": pd.to_numeric(df["实际解禁数量"], errors="coerce"),
        "value": pd.to_numeric(df["实际解禁市值"], errors="coerce"),
        "ratio": pd.to_numeric(df["占解禁前流通市值比例"], errors="coerce"),
    }).dropna(subset=["date"])
    _write(out.sort_values(["symbol", "date"]).reset_index(drop=True), "unlock")


def build_notices(known: set[str]) -> None:
    from fundamentals import events
    frames = []
    for ds in ("notice_restructure", "notice_risk", "notice_holding"):
        df = _read(ds)
        df["symbol"] = df["代码"].map(to_symbol)
        frames.append(pd.DataFrame({
            "symbol": df["symbol"], "ann_date": pd.to_datetime(df["公告日期"], errors="coerce"),
            "title": df["公告标题"].astype(str), "category": df["公告类型"].astype(str), "src": ds,
        }))
    out = pd.concat(frames, ignore_index=True)
    out = out[out["symbol"].isin(known)].dropna(subset=["ann_date"])
    out = out.drop_duplicates(["symbol", "ann_date", "title"])
    out["event"] = events.classify_series(out["title"], out["category"])
    _write(out.sort_values(["symbol", "ann_date"]).reset_index(drop=True), "notices")


def build_all() -> None:
    known = _known_symbols()
    print("building point-in-time tables ->", OUT_DIR)
    build_fin(known)
    build_forecast(known)
    build_pledge(known)
    build_holder(known)
    build_repurchase(known)
    build_unlock(known)
    try:
        build_notices(known)
    except ImportError:
        print("  notices: fundamentals.events not available yet, skipped")


def status() -> None:
    print("raw:")
    for d in sorted(p for p in RAW_DIR.glob("*") if p.is_dir()):
        print(f"  {d.name:20s} {len(list(d.glob('*.parquet'))):4d} files")
    print("built:")
    for f in sorted(OUT_DIR.glob("*.parquet")):
        print(f"  {f.stem:20s} {len(pd.read_parquet(f)):8d} rows")
