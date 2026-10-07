"""Downloads raw fundamentals and announcements (resumable, rate-limited).

Raw files live in ``data/lake/fundamentals/_raw/<kind>/`` (one parquet per
report period, per day or one ``all.parquet``) and are only ever appended or
refreshed; ``fundlab.build`` turns them into point-in-time tables.

Every fetcher skips files that already exist, except the most recent ones that
can still change (current report periods, the last few days of notices,
snapshot tables), so ``python -m fundlab fetch`` is both the first download and
the incremental update. ``--budget`` stops cleanly after N seconds; run it
again to continue.
"""
from __future__ import annotations

import logging
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from data import config

log = logging.getLogger(__name__)
warnings.filterwarnings("ignore")

RAW = config.LAKE_DIR / "fundamentals" / "_raw"

# Notice types from the "全部" feed that never matter for this strategy (dropped to keep files small).
NOISE_TYPES = {
    "独立董事述职报告", "管理办法/制度", "议事规则/实施细则", "ESG公告", "社会责任报告", "独立董事候选人声明",
    "独立董事提名人声明", "股东大会资料", "召开股东大会通知", "监事会决议公告", "投资理财", "募集资金使用情况报告",
    "募集资金使用进展情况", "公司章程", "公司章程修订", "法律意见书", "内部控制报告", "担保年度额度预计",
    "调研活动", "分配预案", "分配方案实施", "分配方案决议公告", "股东大会决议公告",
}
# Notice categories downloaded per day (akshare stock_notice_report symbol -> raw dir).
NOTICE_KINDS = {"持股变动": "notice_holding", "资产重组": "notice_restructure", "风险提示": "notice_risk",
                "重大事项": "notice_major", "融资公告": "notice_finance", "信息变更": "notice_info",
                "全部": "notice_all"}
# The full feed is ~30 pages a day; only fetch it in annual-report season, when audit opinions,
# 非标 explanations and impairment notices are published (they have no smaller category).
FULL_FEED_WINDOWS = [((1, 15), (2, 5)), ((4, 1), (5, 6))]


class Budget:
    def __init__(self, seconds: float | None) -> None:
        self.t0 = time.time()
        self.seconds = seconds

    def left(self) -> bool:
        return self.seconds is None or time.time() - self.t0 < self.seconds


def _ak():
    import akshare as ak  # slow import; only when downloading
    return ak


def _save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df = df.copy()
    for c in df.columns:  # mixed object columns break parquet
        if df[c].dtype == object:
            df[c] = df[c].map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def report_periods(start_year: int, today: date) -> list[str]:
    out = []
    for y in range(start_year, today.year + 1):
        for md in ("0331", "0630", "0930", "1231"):
            p = date(y, int(md[:2]), int(md[2:]))
            if p <= today:
                out.append(f"{y}{md}")
    return out


def _period_is_open(period: str, today: date) -> bool:
    """Reports for a period keep arriving until well after its statutory deadline (late filers, restatements)."""
    p = date(int(period[:4]), int(period[4:6]), int(period[6:]))
    return (today - p).days < 400


def fetch_periodic(budget: Budget, start_year: int = 2016, today: date | None = None) -> dict:
    """Quarterly tables keyed by report period: 业绩报表, 资产负债表, 利润表, 现金流量表, 业绩预告, 预约披露."""
    ak = _ak()
    today = today or date.today()
    jobs = {
        "fin_yjbb": lambda p: ak.stock_yjbb_em(date=p),
        "fin_zcfz": lambda p: ak.stock_zcfz_em(date=p),
        "fin_lrb": lambda p: ak.stock_lrb_em(date=p),
        "fin_xjll": lambda p: ak.stock_xjll_em(date=p),
        "forecast": lambda p: ak.stock_yjyg_em(date=p),
        "disclosure": lambda p: ak.stock_yysj_em(symbol="沪深A股", date=p),
    }
    done = 0
    for kind, fn in jobs.items():
        for p in report_periods(start_year, today):
            if not budget.left():
                return {"periodic": done, "complete": False}
            path = RAW / kind / f"{p}.parquet"
            if path.exists() and not _period_is_open(p, today):
                continue
            if path.exists() and time.time() - path.stat().st_mtime < 20 * 3600:
                continue  # refreshed today already
            try:
                df = fn(p)
            except Exception as e:  # empty periods raise inside akshare
                log.warning("%s %s: %s", kind, p, e)
                df = pd.DataFrame()
            _save(df if df is not None else pd.DataFrame(), path)
            done += 1
            time.sleep(0.3)
    return {"periodic": done, "complete": True}


def _in_windows(d: date, windows) -> bool:
    return any(date(d.year, *a) <= d <= date(d.year, *b) for a, b in windows)


def fetch_notices(budget: Budget, start: date, today: date | None = None, kinds: list[str] | None = None,
                  workers: int = 2, pause: float = 0.0, windows=None) -> dict:
    """Daily announcement lists (eastmoney). Two workers: three or more get rate-limited."""
    ak = _ak()
    today = today or date.today()
    kinds = kinds or list(NOTICE_KINDS)
    todo = []
    d = start
    while d <= today:
        if windows and not _in_windows(d, windows):
            d += timedelta(days=1)
            continue
        for k in kinds:
            path = RAW / NOTICE_KINDS[k] / f"{d:%Y%m%d}.parquet"
            fresh = (today - d).days > 7  # recent days may still gain late notices
            if path.exists() and fresh:
                continue
            if path.exists() and time.time() - path.stat().st_mtime < 6 * 3600:
                continue
            todo.append((k, d, path))
        d += timedelta(days=1)

    def one(k, d, path):
        for attempt in range(3):
            try:
                df = ak.stock_notice_report(symbol=k, date=f"{d:%Y%m%d}")
                break
            except Exception as e:
                # akshare raises these on days without notices (weekends, holidays)
                if "No objects" in str(e) or "concat" in str(e) or isinstance(e, KeyError):
                    df = pd.DataFrame()  # no notices that day
                    break
                log.warning("notice %s %s attempt %d: %s: %s", k, d, attempt, type(e).__name__, str(e)[:120])
                time.sleep(3 + 5 * attempt)
        else:
            time.sleep(10)  # rate-limited: back off before the next day
            return False
        time.sleep(pause)
        if df is None:
            df = pd.DataFrame()
        if k == "全部" and len(df):
            df = df[~df["公告类型"].isin(NOISE_TYPES)]
        _save(df.reset_index(drop=True), path)
        return True

    ok = failed = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        it = iter(todo)
        for job in it:
            futs[ex.submit(one, *job)] = job
            if len(futs) >= workers * 2:
                break
        while futs:
            for f in as_completed(list(futs)):
                futs.pop(f)
                if f.result():
                    ok += 1
                else:
                    failed += 1
                if budget.left():
                    nxt = next(it, None)
                    if nxt is not None:
                        futs[ex.submit(one, *nxt)] = nxt
                break
    remaining = len(todo) - ok - failed
    return {"notices": ok, "failed": failed, "remaining": remaining, "complete": remaining == 0 and failed == 0}


def fetch_snapshots(budget: Budget, today: date | None = None) -> dict:
    """Tables that come as one full snapshot: insider trades, buybacks. Refreshed once a day."""
    ak = _ak()
    jobs = {
        "holder_inc": lambda: ak.stock_ggcg_em(symbol="股东增持"),
        "holder_dec": lambda: ak.stock_ggcg_em(symbol="股东减持"),
        "repurchase": lambda: ak.stock_repurchase_em(),
    }
    done = 0
    for kind, fn in jobs.items():
        path = RAW / kind / "all.parquet"
        if path.exists() and time.time() - path.stat().st_mtime < 20 * 3600:
            continue
        if not budget.left():
            return {"snapshots": done, "complete": False}
        try:
            _save(fn(), path)
            done += 1
        except Exception as e:
            log.warning("%s: %s", kind, e)
    return {"snapshots": done, "complete": True}


def fetch_unlock(budget: Budget, start: date, today: date | None = None) -> dict:
    """Lock-up expiries, one file per month (including the next 3 months, which are known in advance)."""
    ak = _ak()
    today = today or date.today()
    m = date(start.year, start.month, 1)
    end = date(today.year + (today.month + 3 - 1) // 12, (today.month + 3 - 1) % 12 + 1, 1)
    done = 0
    while m <= end:
        nxt = date(m.year + m.month // 12, m.month % 12 + 1, 1)
        path = RAW / "unlock" / f"{m:%Y%m}.parquet"
        stale = (today - m).days < 70
        if (not path.exists() or (stale and time.time() - path.stat().st_mtime > 20 * 3600)) and budget.left():
            try:
                df = ak.stock_restricted_release_detail_em(start_date=f"{m:%Y%m%d}",
                                                           end_date=f"{nxt - timedelta(days=1):%Y%m%d}")
            except Exception as e:
                log.warning("unlock %s: %s", m, e)
                df = pd.DataFrame()
            _save(df if df is not None else pd.DataFrame(), path)
            done += 1
        m = nxt
    return {"unlock": done}


def fetch_pledge(budget: Budget, start: date, today: date | None = None) -> dict:
    """Company pledge ratio, published weekly (Fridays)."""
    ak = _ak()
    today = today or date.today()
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    done = 0
    while d <= today and budget.left():
        path = RAW / "pledge" / f"{d:%Y%m%d}.parquet"
        if not path.exists() or ((today - d).days < 10 and time.time() - path.stat().st_mtime > 20 * 3600):
            try:
                df = ak.stock_gpzy_pledge_ratio_em(date=f"{d:%Y%m%d}")
            except Exception:
                df = pd.DataFrame()  # holiday weeks have no data
            _save(df if df is not None else pd.DataFrame(), path)
            done += 1
            time.sleep(0.3)
        d += timedelta(days=7)
    return {"pledge": done}


def fetch_delisted_financials(budget: Budget, since: date, md=None) -> dict:
    """baostock ratios (with real publish dates) for stocks delisted since ``since``.

    Eastmoney's period tables drop delisted companies, which would make the
    backtest blind to exactly the stocks most likely to blow up. baostock keeps
    them; it reports ratios (ROE, net profit, revenue, liability/asset, CFO/NP,
    total shares) rather than full statements, enough for the veto and odds models.
    """
    import baostock as bs
    from data.query import MarketData
    md = md or MarketData()
    st = md.stocks()
    st = st[(st["status"] == "delisted") & (pd.to_datetime(st["delist_date"], errors="coerce") >= pd.Timestamp(since))]
    st = st[st["exchange"].isin(["SH", "SZ"])]
    out_dir = RAW / "bs_fin"
    todo = [s for s in st["symbol"] if not (out_dir / f"{s}.parquet").exists()]
    if not todo:
        return {"delisted": 0, "complete": True}
    bs.login()
    done = 0
    try:
        for s in todo:
            if not budget.left():
                break
            code = ("sh." if s.endswith(".SH") else "sz.") + s[:6]
            rows = []
            for y in range(since.year - 1, date.today().year + 1):
                for q in (1, 2, 3, 4):
                    if date(y, 3 * q, 1) > date.today():
                        break
                    rec = {"year": y, "quarter": q}
                    for name, fn in (("profit", bs.query_profit_data), ("balance", bs.query_balance_data),
                                     ("cash", bs.query_cash_flow_data)):
                        rs = fn(code=code, year=y, quarter=q)
                        while rs.error_code == "0" and rs.next():
                            rec.update({f"{name}_{k}": v for k, v in zip(rs.fields, rs.get_row_data())})
                    if len(rec) > 2:
                        rows.append(rec)
            df = pd.DataFrame(rows)
            df.insert(0, "symbol", s)
            _save(df, out_dir / f"{s}.parquet")
            done += 1
    finally:
        bs.logout()
    return {"delisted": done, "complete": done == len(todo)}


def fetch_abstract(budget: Budget, since: date = date(2024, 1, 1), workers: int = 2, md=None) -> dict:
    """Sina financial abstract per stock (扣非净利润, 商誉, 净资产, 经营现金流 ... by report period).

    Covers delisted stocks as well, which the eastmoney period tables drop.
    No publish date in this source: fundlab.build takes it from the 预约披露
    table (actual disclosure date) or, failing that, the statutory deadline.
    A file is refreshed when it is more than 3 days old and a newer report
    period may have been published since.
    """
    ak = _ak()
    from data.query import MarketData
    md = md or MarketData()
    st = md.stocks()
    dl = pd.to_datetime(st["delist_date"], errors="coerce")
    st = st[(st["status"] == "listed") | (dl >= pd.Timestamp(since))]
    out_dir = RAW / "abstract"
    today = date.today()
    todo = []
    for s in st["symbol"]:
        p = out_dir / f"{s}.parquet"
        if p.exists():
            age = time.time() - p.stat().st_mtime
            in_season = today.month in (1, 3, 4, 7, 8, 10)
            if age < 3 * 86400 or not in_season and age < 30 * 86400:
                continue
        todo.append((s, p))

    def one(s, p):
        for attempt in range(3):
            try:
                df = ak.stock_financial_abstract(symbol=s[:6])
                break
            except Exception as e:
                if isinstance(e, (KeyError, IndexError, ValueError)):
                    df = pd.DataFrame()
                    break
                time.sleep(2 + 3 * attempt)
        else:
            return False
        if df is None or df.empty:
            _save(pd.DataFrame({"symbol": [s]}).iloc[:0], p)
            return True
        df = df.drop_duplicates(["选项", "指标"]) if "选项" in df else df
        keep = [c for c in df.columns if c in ("选项", "指标") or (c.isdigit() and c >= "20150101")]
        long = df[keep].melt(id_vars=["选项", "指标"], var_name="period", value_name="value")
        long.insert(0, "symbol", s)
        long["value"] = pd.to_numeric(long["value"], errors="coerce")
        _save(long.dropna(subset=["value"]).reset_index(drop=True), p)
        return True

    ok = failed = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        it = iter(todo)
        futs = {ex.submit(one, *j): j for j in [next(it) for _ in range(min(len(todo), workers * 2))]}
        while futs:
            for f in as_completed(list(futs)):
                futs.pop(f)
                ok, failed = (ok + 1, failed) if f.result() else (ok, failed + 1)
                nxt = next(it, None) if budget.left() else None
                if nxt is not None:
                    futs[ex.submit(one, *nxt)] = nxt
                break
    remaining = len(todo) - ok - failed
    return {"abstract": ok, "failed": failed, "remaining": remaining, "complete": remaining == 0}


def fetch_all(budget_sec: float | None = None, start: date = date(2023, 1, 1),
              only: list[str] | None = None) -> dict:
    """Everything the strategy needs. ``start`` is the first notice day (two years of event history before 2025)."""
    b = Budget(budget_sec)
    today = date.today()
    out: dict = {}
    steps = {
        "snapshots": lambda: fetch_snapshots(b, today),
        "periodic": lambda: fetch_periodic(b, 2016, today),
        "unlock": lambda: fetch_unlock(b, date(2024, 1, 1), today),
        "pledge": lambda: fetch_pledge(b, date(2024, 6, 1), today),
        "delisted": lambda: fetch_delisted_financials(b, date(2024, 1, 1)),
        "abstract": lambda: fetch_abstract(b, date(2024, 1, 1)),
        "notices": lambda: fetch_notices(b, start, today, kinds=["持股变动", "资产重组", "风险提示"]),
        "notices_more": lambda: fetch_notices(b, date(2024, 7, 1), today, kinds=["重大事项", "融资公告", "信息变更"]),
        "notices_all": lambda: fetch_notices(b, date(2024, 7, 1), today, kinds=["全部"], workers=1, pause=1.0,
                                             windows=FULL_FEED_WINDOWS),
    }
    for k, fn in steps.items():
        if only and k not in only:
            continue
        if not b.left():
            out[k] = "skipped (budget)"
            continue
        out[k] = fn()
    return out
