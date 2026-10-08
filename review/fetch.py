"""External data for market reviews, cached in data/lake/reviews/_raw/ (resumable).

Sources that work without a browser JS runtime (checked 2026-10):
    中证指数官网   stock_zh_index_hist_csindex   上证 / 科创50 / 沪深300 / 中证500 / 1000 / 2000 / 北证50 / 红利 + 滚动PE
    腾讯           stock_zh_index_daily_tx       深证成指 / 创业板指
    申万           index_hist_sw / index_component_sw / sw_index_first_info   一级行业指数、成分、估值
    东方财富       stock_zt_pool_em / _dtgc_em / _zbgc_em / _previous_em       涨停 / 跌停 / 炸板 / 昨日涨停池（只保留近一个月左右）
    交易所         stock_margin_sse / stock_margin_szse                        两融余额
    统计局 / 央行  macro_china_pmi / cpi / ppi / money_supply / shrzgm         宏观
    中债           bond_china_yield                                           10 年国债
"""
from __future__ import annotations

import logging
import time
import warnings
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from data import config

warnings.filterwarnings("ignore")
log = logging.getLogger(__name__)
RAW = config.LAKE_DIR / "reviews" / "_raw"

# name, source, code
INDICES = [
    ("上证指数", "cs", "000001"), ("深证成指", "tx", "sz399001"), ("创业板指", "tx", "sz399006"),
    ("科创50", "cs", "000688"), ("沪深300", "cs", "000300"), ("中证500", "cs", "000905"),
    ("中证1000", "cs", "000852"), ("中证2000", "cs", "932000"), ("北证50", "cs", "899050"),
    ("中证红利", "cs", "000922"),
]


def _ak():
    import akshare as ak
    return ak


def _save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def _fresh(path: Path, hours: float = 20) -> bool:
    return path.exists() and time.time() - path.stat().st_mtime < hours * 3600


def fetch_indices(start: str = "2024-01-01") -> dict:
    ak = _ak()
    rows = []
    for name, src, code in INDICES:
        try:
            if src == "cs":
                df = ak.stock_zh_index_hist_csindex(symbol=code, start_date=start.replace("-", ""),
                                                    end_date=date.today().strftime("%Y%m%d"))
                df = pd.DataFrame({"date": pd.to_datetime(df["日期"]), "open": df["开盘"], "high": df["最高"],
                                   "low": df["最低"], "close": df["收盘"], "amount": df["成交金额"] * 1e8,
                                   "pe": df.get("滚动市盈率")})
            else:
                df = ak.stock_zh_index_daily_tx(symbol=code)
                df = pd.DataFrame({"date": pd.to_datetime(df["date"]), "open": df["open"], "high": df["high"],
                                   "low": df["low"], "close": df["close"], "amount": None,  # tencent unit unclear
                                   "pe": None})
                df = df[df["date"] >= start]
            df.insert(0, "index", name)
            rows.append(df)
        except Exception as e:
            log.warning("index %s: %s", name, e)
    out = pd.concat(rows, ignore_index=True)
    _save(out, RAW / "indices.parquet")
    return {"indices": len(out)}


def fetch_valuation_history() -> dict:
    """CSI 300 / 500 / 1000 rolling PE since 2016 (for historical percentiles)."""
    ak = _ak()
    rows = []
    for name, code in (("沪深300", "000300"), ("中证500", "000905"), ("中证1000", "000852"), ("中证2000", "932000")):
        try:
            df = ak.stock_zh_index_hist_csindex(symbol=code, start_date="20160101",
                                                end_date=date.today().strftime("%Y%m%d"))
            rows.append(pd.DataFrame({"index": name, "date": pd.to_datetime(df["日期"]), "pe": df["滚动市盈率"]}))
        except Exception as e:
            log.warning("pe %s: %s", name, e)
    out = pd.concat(rows, ignore_index=True)
    _save(out, RAW / "index_pe.parquet")
    return {"index_pe": len(out)}


def fetch_sw(start: str = "2025-12-01") -> dict:
    """申万一级行业: daily index history and constituents (current membership)."""
    ak = _ak()
    info = ak.sw_index_first_info()
    _save(info, RAW / "sw_first_info.parquet")
    hist, cons = [], []
    for code, name in zip(info["行业代码"], info["行业名称"]):
        c = code.split(".")[0]
        try:
            h = ak.index_hist_sw(symbol=c, period="day")
            h = h[pd.to_datetime(h["日期"]) >= start]
            hist.append(pd.DataFrame({"code": c, "industry": name, "date": pd.to_datetime(h["日期"]),
                                      "close": h["收盘"], "amount": h["成交额"] * 1e8}))
            m = ak.index_component_sw(symbol=c)
            cons.append(pd.DataFrame({"code": c, "industry": name, "stock": m["证券代码"].astype(str).str.zfill(6),
                                      "stock_name": m["证券名称"]}))
        except Exception as e:
            log.warning("sw %s: %s", name, e)
        time.sleep(0.2)
    _save(pd.concat(hist, ignore_index=True), RAW / "sw_hist.parquet")
    _save(pd.concat(cons, ignore_index=True), RAW / "sw_cons.parquet")
    return {"sw_hist": sum(len(x) for x in hist), "sw_cons": sum(len(x) for x in cons)}


def fetch_pools(days: list[date], budget: float = 140) -> dict:
    """Daily limit-up / limit-down / broken-limit / yesterday-limit-up pools (eastmoney keeps ~1 month)."""
    ak = _ak()
    t0 = time.time()
    done = 0
    for d in days:
        for kind, fn in (("zt", ak.stock_zt_pool_em), ("dt", ak.stock_zt_pool_dtgc_em),
                         ("zb", ak.stock_zt_pool_zbgc_em), ("prev", ak.stock_zt_pool_previous_em)):
            p = RAW / "pools" / kind / f"{d:%Y%m%d}.parquet"
            if p.exists() or time.time() - t0 > budget:
                continue
            try:
                df = fn(date=f"{d:%Y%m%d}")
            except Exception:
                df = pd.DataFrame()
            if df is not None and len(df):
                _save(df, p)
                done += 1
    return {"pools": done}


def fetch_margin(days: list[date], budget: float = 140) -> dict:
    ak = _ak()
    t0 = time.time()
    p = RAW / "margin.parquet"
    old = pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=["date", "sh", "sz"])
    have = set(pd.to_datetime(old["date"]).dt.date) if len(old) else set()
    rows = []
    try:
        sh = ak.stock_margin_sse(start_date=f"{min(days):%Y%m%d}", end_date=f"{max(days):%Y%m%d}")
        sh = dict(zip(pd.to_datetime(sh["信用交易日期"].astype(str)).dt.date, sh["融资融券余额"].astype(float)))
    except Exception as e:
        log.warning("margin sse: %s", e)
        sh = {}
    for d in days:
        if d in have or time.time() - t0 > budget:
            continue
        try:
            z = ak.stock_margin_szse(date=f"{d:%Y%m%d}")
            sz = float(z["融资融券余额"].iloc[0]) * 1e8
        except Exception:
            sz = None
        rows.append({"date": pd.Timestamp(d), "sh": sh.get(d), "sz": sz})
    out = pd.concat([old, pd.DataFrame(rows)], ignore_index=True) if rows else old
    out["date"] = pd.to_datetime(out["date"])
    _save(out.drop_duplicates("date", keep="last").sort_values("date"), p)
    return {"margin": len(rows)}


def fetch_macro() -> dict:
    ak = _ak()
    jobs = {"pmi": ak.macro_china_pmi, "cpi": ak.macro_china_cpi, "ppi": ak.macro_china_ppi,
            "m2": ak.macro_china_money_supply, "shrzgm": ak.macro_china_shrzgm}
    out = {}
    for k, fn in jobs.items():
        try:
            df = fn()
            _save(df, RAW / "macro" / f"{k}.parquet")
            out[k] = len(df)
        except Exception as e:
            log.warning("macro %s: %s", k, e)
    try:
        y = ak.bond_china_yield(start_date="20251201", end_date=date.today().strftime("%Y%m%d"))
        y = y[y["曲线名称"].str.contains("国债")]
        _save(y, RAW / "macro" / "cgb.parquet")
        out["cgb"] = len(y)
    except Exception as e:
        log.warning("cgb: %s", e)
    return out


def fetch_holders(periods=("20251231", "20260331", "20260630")) -> dict:
    """股东户数 by report period (筹码结构 for doubled stocks)."""
    ak = _ak()
    out = {}
    for p in periods:
        path = RAW / "gdhs" / f"{p}.parquet"
        if _fresh(path, 24 * 7):
            continue
        try:
            df = ak.stock_zh_a_gdhs(symbol=p)
            _save(df, path)
            out[p] = len(df)
        except Exception as e:
            log.warning("gdhs %s: %s", p, e)
    return out


def fetch_concepts(budget: float = 140) -> dict:
    """新浪概念板块的当前成分（题材归属；历史成分不可得，成分按下载当天）。"""
    ak = _ak()
    t0 = time.time()
    p = RAW / "concepts.parquet"
    old = pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=["label", "concept", "code", "name"])
    done = set(old["label"]) if len(old) else set()
    spot = ak.stock_sector_spot(indicator="概念")
    rows = [old] if len(old) else []
    n = 0
    for label, name in zip(spot["label"], spot["板块"]):
        if label in done:
            continue
        if time.time() - t0 > budget:
            break
        try:
            d = ak.stock_sector_detail(sector=label)
            rows.append(pd.DataFrame({"label": label, "concept": name, "code": d["code"].astype(str).str.zfill(6),
                                      "name": d["name"]}))
            n += 1
        except Exception as e:
            log.warning("concept %s: %s", name, e)
        time.sleep(0.3)
    out = pd.concat(rows, ignore_index=True) if rows else old
    _save(out, p)
    return {"concepts_new": n, "concepts": int(out["label"].nunique()) if len(out) else 0, "total": len(spot)}


def fetch_all(budget: float = 150) -> dict:
    from data.query import MarketData
    md = MarketData()
    days = [pd.Timestamp(x).date() for x in md.sql(
        "SELECT DISTINCT date FROM daily WHERE date >= DATE '2026-05-25' ORDER BY 1")["date"]]
    out = {}
    t0 = time.time()
    for name, fn in (("indices", fetch_indices), ("index_pe", fetch_valuation_history), ("sw", fetch_sw),
                     ("macro", fetch_macro), ("holders", fetch_holders)):
        key = RAW / ({"indices": "indices", "index_pe": "index_pe", "sw": "sw_hist"}.get(name, name) + ".parquet")
        if name in ("indices", "index_pe", "sw") and _fresh(key):
            continue
        if time.time() - t0 > budget:
            out[name] = "skipped (budget)"
            continue
        out[name] = fn()
    left = max(10.0, budget - (time.time() - t0))
    out.update(fetch_margin(days, left / 2))
    out.update(fetch_pools(days, left / 2))
    return out
