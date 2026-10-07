"""Resumable downloaders for the fundamental / announcement datasets.

Every unit of work (one report period, one day of announcements, one week of
pledge data ...) is saved as its own small parquet file under
``<lake>/fundamentals/_raw/<dataset>/<key>.parquet``. A unit that already
exists is skipped, so the job can be killed and restarted at any time, and
``--budget`` makes a run stop politely after N seconds.

Datasets (all from akshare / Eastmoney):

    fin_yjbb fin_zcfz fin_lrb fin_xjll   financial statements by report period
    forecast                             earnings pre-announcements (业绩预告)
    notice                               every announcement title of a trading day
    pledge                               weekly share-pledge ratio
    holder_inc / holder_dec              major holders' / executives' buy / sell
    repurchase                           buy-back plans
    unlock                               restricted-share release detail
"""
from __future__ import annotations

import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterator

os.environ.setdefault("TQDM_DISABLE", "1")

import pandas as pd

from data import config

RAW_DIR = config.LAKE_DIR / "fundamentals" / "_raw"
FIRST_PERIOD = date(2016, 12, 31)   # ~9 years of statements: enough for a multi-year PB percentile
NOTICE_START = date(2023, 1, 3)     # control-change precursors (A2) look back up to 24 months
PLEDGE_START = date(2024, 6, 7)

# Eastmoney announcement categories worth keeping. The "全部" feed is ~1000-4000 rows a day and
# gets throttled; these three carry everything channels A / C / E need.
NOTICE_CATEGORIES = {"notice_restructure": "资产重组", "notice_risk": "风险提示", "notice_holding": "持股变动"}

DATASETS = ["fin_yjbb", "fin_zcfz", "fin_lrb", "fin_xjll", "forecast", "disclosure", *NOTICE_CATEGORIES, "pledge",
            "holder_inc", "holder_dec", "repurchase", "unlock"]


def _ak():
    import akshare as ak
    return ak


def quarter_ends(start: date = FIRST_PERIOD, end: date | None = None) -> list[date]:
    end = end or date.today()
    out = []
    for y in range(start.year, end.year + 1):
        for m, d in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q = date(y, m, d)
            if start <= q <= end:
                out.append(q)
    return out


def fridays(start: date, end: date) -> list[date]:
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def trading_days(start: date, end: date) -> list[date]:
    from data.query import MarketData
    cal = MarketData().calendar(start, end)
    return [d for d in cal if d <= date.today()]


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


# ---- unit definitions --------------------------------------------------------------
Unit = tuple[str, str, Callable[[], pd.DataFrame], bool]  # dataset, key, fetch fn, is_recent


def units(dataset: str) -> Iterator[Unit]:
    ak = _ak()
    today = date.today()
    recent_cut = today - timedelta(days=12)
    if dataset.startswith("fin_") or dataset == "forecast":
        fn = {"fin_yjbb": ak.stock_yjbb_em, "fin_zcfz": ak.stock_zcfz_em, "fin_lrb": ak.stock_lrb_em,
              "fin_xjll": ak.stock_xjll_em, "forecast": ak.stock_yjyg_em}[dataset]
        qs = quarter_ends()
        for q in qs:
            # the two most recent periods keep changing while companies are still reporting
            recent = q >= qs[-2]
            yield dataset, _ymd(q), (lambda f=fn, q=q: f(date=_ymd(q))), recent
    elif dataset == "disclosure":
        # Original (first) disclosure date of every periodic report: the clean point-in-time date.
        label = {3: "一季", 6: "半年报", 9: "三季", 12: "年报"}
        for q in quarter_ends():
            name = f"{q.year}{label[q.month]}"
            yield dataset, _ymd(q), (lambda n=name: ak.stock_report_disclosure(market="沪深京", period=n)), q >= quarter_ends()[-2]
    elif dataset in NOTICE_CATEGORIES:
        cat = NOTICE_CATEGORIES[dataset]
        for d in trading_days(NOTICE_START, today):
            yield dataset, _ymd(d), (lambda d=d, c=cat: ak.stock_notice_report(symbol=c, date=_ymd(d))), d >= recent_cut
    elif dataset == "pledge":
        for d in fridays(PLEDGE_START, today):
            yield dataset, _ymd(d), (lambda d=d: ak.stock_gpzy_pledge_ratio_em(date=_ymd(d))), d >= recent_cut
    elif dataset == "holder_inc":
        yield dataset, "all", (lambda: ak.stock_ggcg_em(symbol="股东增持")), True
    elif dataset == "holder_dec":
        yield dataset, "all", (lambda: ak.stock_ggcg_em(symbol="股东减持")), True
    elif dataset == "repurchase":
        yield dataset, "all", ak.stock_repurchase_em, True
    elif dataset == "unlock":
        d = date(2024, 6, 1)
        while d <= today + timedelta(days=90):
            nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
            end = nxt - timedelta(days=1)
            yield dataset, d.strftime("%Y%m"), (
                lambda a=d, b=end: ak.stock_restricted_release_detail_em(start_date=_ymd(a), end_date=_ymd(b))
            ), end >= recent_cut
            d = nxt
    else:
        raise ValueError(f"unknown dataset {dataset!r}; choose from {DATASETS}")


def raw_path(dataset: str, key: str) -> Path:
    return RAW_DIR / dataset / f"{key}.parquet"


def _fetch_with_retry(fn: Callable[[], pd.DataFrame], tries: int = 4) -> pd.DataFrame:
    err: Exception | None = None
    for i in range(tries):
        try:
            df = fn()
            return df if df is not None else pd.DataFrame()
        except Exception as e:  # network hiccups and Eastmoney throttling
            err = e
            msg = str(e)
            # an empty result for a day without data comes back as an exception in some akshare versions
            if "No tables found" in msg or "Length mismatch" in msg or "'NoneType'" in msg:
                return pd.DataFrame()
            time.sleep(1.5 * (i + 1) + random.random())
    raise RuntimeError(f"{type(err).__name__}: {str(err)[:150]}")


def fetch(dataset: str, budget: float = 150.0, workers: int = 3, refresh: bool = False,
          part: tuple[int, int] | None = None, log=print) -> dict:
    """Download the missing units of ``dataset``. Returns counts; ``remaining`` > 0 means run again."""
    t0 = time.time()
    todo: list[Unit] = []
    total = 0
    for u in units(dataset):
        total += 1
        if part and (total - 1) % part[1] != part[0]:
            continue
        if raw_path(dataset, u[1]).exists() and not (refresh and u[3]):
            continue
        todo.append(u)
    (RAW_DIR / dataset).mkdir(parents=True, exist_ok=True)
    stats = {"dataset": dataset, "total": total, "todo": len(todo), "done": 0, "failed": 0, "remaining": 0}
    errors: list[str] = []
    lock = threading.Lock()
    stop = threading.Event()

    def work(u: Unit) -> None:
        if stop.is_set() or time.time() - t0 > budget:
            return
        _, key, fn, _ = u
        try:
            time.sleep(random.random() * 0.4)
            df = _fetch_with_retry(fn)
            df = df.astype({c: "string" for c in df.columns if df[c].dtype == object}) if len(df) else df
            tmp = raw_path(dataset, key).with_suffix(".tmp")
            df.to_parquet(tmp, index=False)
            tmp.replace(raw_path(dataset, key))
            with lock:
                stats["done"] += 1
        except Exception as e:
            with lock:
                stats["failed"] += 1
                errors.append(f"{key}: {e}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, todo))
    stats["remaining"] = stats["todo"] - stats["done"] - stats["failed"]
    stats["elapsed"] = round(time.time() - t0, 1)
    if errors:
        stats["errors"] = errors[:5]
    log(stats)
    return stats
