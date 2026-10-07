"""Historical stock names and ST (risk-warning) status.

Stored in the lake as

    meta/names.parquet          one row per name / ST segment:
                                symbol, start, end (NULL = still in effect), name, is_st, source
    meta/name_coverage.parquet  one row per symbol: how its history was obtained
                                symbol, method, former_names, chain_hash, updated_at, error

Where the dates come from (in order of preference):

    szse      深交所“证券简称变更”全表（一次下载，覆盖所有深市股票，含退市）
    tushare   tushare namechange（需要 TUSHARE_TOKEN，覆盖沪深京）
    baostock  逐只查询日线 isST 标记（沪深，不含北交所；只有 ST 与否，没有名称）

Eastmoney's 曾用名 chain (undated) is downloaded for every symbol first. A stock
whose chain has never contained ST/PT is marked ``never_st`` and needs no
per-symbol query, which keeps the slow baostock path to a few hundred SH stocks.

``method`` values: szse / tushare / baostock / never_st are authoritative;
``undated`` (ever ST but no dated source) and ``missing`` fall back to the
current stock name.
"""
from __future__ import annotations

import hashlib
import io
import logging
import time
import unicodedata
import warnings
from datetime import date, timedelta
from typing import Callable, Iterable

import numpy as np
import pandas as pd
import pyarrow as pa

from data import config
from data import symbols as sym
from data.store import Lake, now_iso

log = logging.getLogger(__name__)

SZSE_URL = "https://www.szse.cn/api/report/ShowReport"
EM_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

AUTHORITATIVE = {"szse", "tushare", "baostock", "never_st"}

SEGMENT_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("start", pa.date32()),
    ("end", pa.date32()),
    ("name", pa.string()),
    ("is_st", pa.bool_()),
    ("source", pa.string()),
])
COVERAGE_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("method", pa.string()),
    ("former_names", pa.string()),
    ("chain_hash", pa.string()),
    ("updated_at", pa.string()),
    ("error", pa.string()),
])


# ---- name helpers -----------------------------------------------------------

def clean_name(name) -> str:
    """Full-width -> half-width, drop spaces ("ST 国 农" -> "ST国农")."""
    if name is None or (isinstance(name, float) and np.isnan(name)):
        return ""
    return unicodedata.normalize("NFKC", str(name)).replace(" ", "").replace("　", "").strip()


def is_st_name(name) -> bool:
    """ST, *ST, SST, S*ST and the 1999-2002 PT (特别转让) all trade under risk-warning limits."""
    n = clean_name(name).upper()
    return "ST" in n or n.startswith("PT")


def _hash(names: Iterable[str]) -> str:
    return hashlib.sha1("→".join(names).encode("utf-8")).hexdigest()[:16]


# ---- downloads --------------------------------------------------------------

def fetch_eastmoney_former_names(timeout: float = 20) -> dict[str, list[str]]:
    """{symbol: [oldest name, ..., current name]} for every A-share eastmoney knows (~25k rows, ~15 s)."""
    import requests

    out: dict[str, list[str]] = {}
    page, pages = 1, 1
    s = requests.Session()
    s.headers["User-Agent"] = UA
    while page <= pages:
        r = s.get(EM_URL, params={
            "reportName": "RPT_F10_BASIC_ORGINFO", "columns": "SECUCODE,SECURITY_NAME_ABBR,FORMERNAME",
            "pageSize": 500, "pageNumber": page, "sortColumns": "SECUCODE", "sortTypes": 1,
        }, timeout=timeout)
        r.raise_for_status()
        res = (r.json() or {}).get("result") or {}
        pages = int(res.get("pages") or 0)
        for d in res.get("data") or []:
            code = str(d.get("SECUCODE") or "")
            if not sym.is_a_share(code):
                continue
            chain = [clean_name(x) for x in str(d.get("FORMERNAME") or "").split("→") if clean_name(x)]
            cur = clean_name(d.get("SECURITY_NAME_ABBR"))
            if cur and (not chain or chain[-1] != cur):
                chain.append(cur)
            out[sym.normalize(code)] = chain
        page += 1
    if not out:
        raise RuntimeError("eastmoney returned no former-name data")
    return out


def fetch_szse_name_changes(timeout: float = 60) -> pd.DataFrame:
    """All SZSE short-name changes: symbol, date, before, after (one xlsx, ~7.5k rows)."""
    import requests

    r = requests.get(SZSE_URL, params={"SHOWTYPE": "xlsx", "CATALOGID": "SSGSGMXX", "TABKEY": "tab2"},
                     headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # openpyxl style warning
        df = pd.read_excel(io.BytesIO(r.content), dtype=str)
    need = {"变更日期", "证券代码", "变更前简称", "变更后简称"}
    if not need <= set(df.columns):
        raise RuntimeError(f"unexpected SZSE columns: {list(df.columns)}")
    df["证券代码"] = df["证券代码"].str.strip().str.zfill(6)
    df = df[df["证券代码"].map(sym.is_a_share)]
    out = pd.DataFrame({
        "symbol": df["证券代码"].map(sym.normalize),
        "date": pd.to_datetime(df["变更日期"], errors="coerce").dt.date,
        "before": df["变更前简称"].map(clean_name),
        "after": df["变更后简称"].map(clean_name),
    }).dropna(subset=["date"])
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)


# ---- segment builders -------------------------------------------------------

def segments_from_changes(symbol: str, changes: pd.DataFrame, current_name: str,
                          list_date: date | None, source: str) -> pd.DataFrame:
    """Name segments from dated change events (columns date, before, after)."""
    start0 = list_date or config.EARLIEST_DATE
    if not changes.empty and changes["date"].min() <= start0:
        start0 = config.EARLIEST_DATE  # the stored list date is later than a rename: trust the exchange
    rows = []
    if changes.empty:
        rows.append((start0, None, current_name))
    else:
        ch = changes.sort_values("date").drop_duplicates("date", keep="last")
        first = ch.iloc[0]
        prev_start, prev_name = start0, first["before"] or current_name
        for r in ch.itertuples():
            d = r.date
            if d > prev_start:
                rows.append((prev_start, d - timedelta(days=1), prev_name))
            prev_start, prev_name = max(d, prev_start), r.after
        rows.append((prev_start, None, prev_name))
    return pd.DataFrame([{"symbol": symbol, "start": s, "end": e, "name": n, "is_st": is_st_name(n),
                          "source": source} for s, e, n in rows])


def segments_from_flags(symbol: str, flags: pd.DataFrame, source: str = "baostock") -> pd.DataFrame:
    """ST segments from a daily 0/1 flag series (columns date, is_st). Names are unknown (NULL)."""
    f = flags.dropna().sort_values("date").reset_index(drop=True)
    if f.empty:
        return pd.DataFrame(columns=SEGMENT_SCHEMA.names)
    run = (f["is_st"] != f["is_st"].shift()).cumsum()
    g = f.groupby(run).agg(start=("date", "first"), is_st=("is_st", "first")).reset_index(drop=True)
    g["end"] = list(g["start"].iloc[1:].map(lambda d: d - timedelta(days=1))) + [None]
    g["symbol"], g["name"], g["source"] = symbol, None, source
    return g[SEGMENT_SCHEMA.names]


def segments_from_intervals(symbol: str, df: pd.DataFrame, source: str = "tushare") -> pd.DataFrame:
    """tushare namechange rows (name, start_date, end_date) -> segments."""
    if df is None or df.empty:
        return pd.DataFrame(columns=SEGMENT_SCHEMA.names)
    d = pd.DataFrame({
        "start": pd.to_datetime(df["start_date"], format="%Y%m%d", errors="coerce").dt.date,
        "end": pd.to_datetime(df["end_date"], format="%Y%m%d", errors="coerce").dt.date,
        "name": df["name"].map(clean_name),
    }).dropna(subset=["start"]).sort_values("start").drop_duplicates("start", keep="last").reset_index(drop=True)
    # Make segments contiguous: each ends the day before the next starts; the last stays open.
    nxt = list(d["start"].iloc[1:].map(lambda x: x - timedelta(days=1))) + [None]
    d["end"] = nxt
    d["symbol"], d["is_st"], d["source"] = symbol, d["name"].map(is_st_name), source
    return d[SEGMENT_SCHEMA.names]


# ---- storage ----------------------------------------------------------------

def read_segments(lake: Lake) -> pd.DataFrame:
    p = lake.meta_dir / "names.parquet"
    if not p.exists():
        return SEGMENT_SCHEMA.empty_table().to_pandas()
    return pd.read_parquet(p)


def read_coverage(lake: Lake) -> pd.DataFrame:
    p = lake.meta_dir / "name_coverage.parquet"
    if not p.exists():
        return COVERAGE_SCHEMA.empty_table().to_pandas()
    return pd.read_parquet(p)


def _write(lake: Lake, df: pd.DataFrame, schema: pa.Schema, name: str, sort: list[str]) -> None:
    df = df.copy()
    for c in ("start", "end"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.date
    df = df[schema.names].sort_values(sort).reset_index(drop=True)
    lake._atomic_write(pa.Table.from_pandas(df, schema=schema, preserve_index=False), lake.meta_dir / name)


# ---- per-symbol dated sources -----------------------------------------------

class _Baostock:
    """Sequential isST queries with a socket timeout and a circuit breaker.

    The baostock client blocks forever on a silent server; a socket timeout
    turns that into an error, and three failures in a row stop it for the run.
    """

    def __init__(self, timeout: float = 15) -> None:
        self.timeout = timeout
        self.bs = None
        self.fails = 0
        self.disabled: str | None = None

    def _login(self):
        import contextlib

        import baostock as bs
        from baostock.common import context
        with contextlib.redirect_stdout(io.StringIO()):
            lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login failed: {lg.error_msg}")
        sock = getattr(context, "default_socket", None)
        if sock is not None:
            sock.settimeout(self.timeout)
        self.bs = bs

    def flags(self, symbol: str) -> pd.DataFrame:
        if self.disabled:
            raise RuntimeError(self.disabled)
        import contextlib
        last = None
        for attempt in range(2):
            try:
                if self.bs is None:
                    self._login()
                with contextlib.redirect_stdout(io.StringIO()):  # the client prints socket errors
                    rs = self.bs.query_history_k_data_plus(
                        sym.to_baostock(symbol), "date,isST", start_date="1990-01-01",
                        end_date=date.today().isoformat(), frequency="d", adjustflag="3")
                    if rs.error_code != "0":
                        raise RuntimeError(f"baostock {rs.error_code}: {rs.error_msg}")
                    rows = []
                    while rs.next():
                        rows.append(rs.get_row_data())
                self.fails = 0
                df = pd.DataFrame(rows, columns=["date", "isST"])
                return pd.DataFrame({"date": pd.to_datetime(df["date"]).dt.date,
                                     "is_st": df["isST"].astype(str).eq("1")})
            except Exception as e:  # socket timeout, protocol error
                last = e
                log.info("baostock %s attempt %d failed (%s), reconnecting", symbol, attempt + 1, e)
                self.close()  # force a fresh login / socket
        self.fails += 1
        if self.fails >= 3:
            self.disabled = f"baostock stopped after 3 failures in a row: {last}"
            log.warning(self.disabled)
        raise RuntimeError(f"baostock: {last}")

    def close(self) -> None:
        """Close the socket directly; bs.logout() waits for a reply and can block on a silent server."""
        if self.bs is not None:
            try:
                from baostock.common import context
                sock = getattr(context, "default_socket", None)
                if sock is not None:
                    sock.close()
                    setattr(context, "default_socket", None)
            except Exception:
                pass
            self.bs = None


class _Tushare:
    def __init__(self) -> None:
        self.pro = None
        self.last = 0.0
        self.disabled: str | None = None if config.TUSHARE_TOKEN else "TUSHARE_TOKEN not set"

    def changes(self, symbol: str) -> pd.DataFrame:
        if self.disabled:
            raise RuntimeError(self.disabled)
        if self.pro is None:
            import tushare as ts
            self.pro = ts.pro_api(config.TUSHARE_TOKEN)
        wait = 0.35 - (time.time() - self.last)  # namechange quota is ~200/min
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        try:
            return self.pro.namechange(ts_code=sym.to_tushare(symbol),
                                       fields="ts_code,name,start_date,end_date,change_reason")
        except Exception as e:
            msg = str(e)
            if "权限" in msg or "token" in msg.lower():
                self.disabled = f"tushare namechange: {msg}"
            raise RuntimeError(f"tushare: {msg}") from e


# ---- main entry -------------------------------------------------------------

ProgressCallback = Callable[[str, int, int], None]


def refresh_names(lake: Lake | None = None, symbols: list[str] | None = None, force: bool = False,
                  time_budget: float | None = None, progress: ProgressCallback | None = None) -> dict:
    """Download / update name and ST history. Resumable: re-run to continue after a time budget.

    Without ``force``, symbols already covered by tushare/baostock are only
    re-queried when their eastmoney former-name chain changed (a new rename,
    which is how ST status changes). SZSE data is rebuilt every time (one file).
    """
    lake = lake or Lake()
    t0 = time.time()
    stocks = lake.read_stocks()
    if stocks.empty:
        raise RuntimeError("stock list is empty; run a data update first")
    stocks = stocks.set_index("symbol")
    targets = [s for s in (symbols or stocks.index.tolist()) if s in stocks.index]
    report = {"targets": len(targets), "fetched": 0, "failed": 0, "remaining": 0, "methods": {},
              "sources": {}, "errors": []}

    def out_of_time() -> bool:
        return time_budget is not None and time.time() - t0 > time_budget

    if progress:
        progress("names: eastmoney", 0, 1)
    try:
        em = fetch_eastmoney_former_names()
        report["sources"]["eastmoney"] = len(em)
        log.info("eastmoney former names: %d symbols", len(em))
    except Exception as e:
        em = {}
        report["sources"]["eastmoney"] = f"failed: {e}"
        log.warning("eastmoney former names failed: %s", e)

    if progress:
        progress("names: szse", 0, 1)
    try:
        szse = fetch_szse_name_changes()
        report["sources"]["szse"] = len(szse)
        log.info("SZSE name changes: %d rows for %d symbols", len(szse), szse["symbol"].nunique())
    except Exception as e:
        szse = None
        report["sources"]["szse"] = f"failed: {e}"
        log.warning("SZSE name changes failed: %s", e)
    szse_by = {s: g for s, g in szse.groupby("symbol")} if szse is not None else {}

    old_seg = read_segments(lake)
    old_cov = read_coverage(lake).set_index("symbol") if not read_coverage(lake).empty else None
    seg_by_old = {s: g for s, g in old_seg.groupby("symbol")} if len(old_seg) else {}

    new_segs: dict[str, pd.DataFrame] = {}
    new_cov: dict[str, dict] = {}
    pending: list[str] = []  # need a per-symbol dated source

    for s in targets:
        row = stocks.loc[s]
        cur = clean_name(row["name"])
        ld = pd.Timestamp(row["list_date"]).date() if pd.notna(row["list_date"]) else None
        chain = em.get(s) or ([cur] if cur else [])
        ever_st = any(is_st_name(n) for n in chain) or is_st_name(cur)
        h = _hash(chain)
        base = {"symbol": s, "former_names": "→".join(chain), "chain_hash": h, "updated_at": now_iso(), "error": None}
        prev = old_cov.loc[s] if old_cov is not None and s in old_cov.index else None

        if s.endswith(".SZ") and szse is not None:
            new_segs[s] = segments_from_changes(s, szse_by.get(s, pd.DataFrame(columns=["date", "before", "after"])),
                                                cur, ld, "szse")
            new_cov[s] = {**base, "method": "szse"}
        elif em and not ever_st:
            new_segs[s] = pd.DataFrame(columns=SEGMENT_SCHEMA.names)
            new_cov[s] = {**base, "method": "never_st"}
        elif (not force and prev is not None and prev["method"] in ("tushare", "baostock")
              and prev["chain_hash"] == h and s in seg_by_old):
            new_segs[s] = seg_by_old[s]
            new_cov[s] = {**base, "method": prev["method"], "updated_at": prev["updated_at"]}
        else:
            pending.append(s)
            new_cov[s] = {**base, "method": "undated" if ever_st else "missing"}

    report["pending"] = len(pending)
    log.info("names: %d symbols need a per-symbol query", len(pending))

    def flush() -> pd.DataFrame:
        """Write everything gathered so far, so an interrupted run loses at most a few symbols."""
        touched = set(new_cov)
        keep = old_seg[~old_seg["symbol"].isin(touched)] if len(old_seg) else old_seg
        frames = [f for f in [keep] + list(new_segs.values()) if len(f)]
        segs = pd.concat(frames, ignore_index=True) if frames else SEGMENT_SCHEMA.empty_table().to_pandas()
        cov_old = read_coverage(lake)
        cov = pd.concat([cov_old[~cov_old["symbol"].isin(touched)], pd.DataFrame(list(new_cov.values()))],
                        ignore_index=True)
        _write(lake, segs, SEGMENT_SCHEMA, "names.parquet", ["symbol", "start"])
        _write(lake, cov, COVERAGE_SCHEMA, "name_coverage.parquet", ["symbol"])
        report["methods"] = cov["method"].value_counts().to_dict()
        report["segments"] = int(len(segs))
        report["st_segments"] = int(segs["is_st"].sum()) if len(segs) else 0
        return segs

    flush()  # SZSE / never-ST results are saved before the slow per-symbol part starts
    ts_api, bs_api = _Tushare(), _Baostock()
    last_flush = time.time()
    try:
        for k, s in enumerate(pending):
            if out_of_time():
                report["remaining"] = len(pending) - k
                log.info("names: time budget reached, %d symbols left (run again to continue)", report["remaining"])
                break
            if progress:
                progress("names: per-symbol", k, len(pending))
            errs = []
            done = False
            if not ts_api.disabled:
                try:
                    segs = segments_from_intervals(s, ts_api.changes(s))
                    if len(segs):
                        new_segs[s], new_cov[s]["method"], done = segs, "tushare", True
                except Exception as e:
                    errs.append(str(e))
            if not done and not s.endswith(".BJ") and not bs_api.disabled:
                try:
                    segs = segments_from_flags(s, bs_api.flags(s))
                    new_segs[s], new_cov[s]["method"], done = segs, "baostock", True
                except Exception as e:
                    errs.append(str(e))
            if done:
                report["fetched"] += 1
            else:
                if errs:
                    report["failed"] += 1
                    report["errors"].append({"symbol": s, "error": "; ".join(errs)[:300]})
                new_cov[s]["error"] = "; ".join(errs)[:300] or "no dated source for this exchange"
                # Keep whatever was stored before rather than losing it.
                if s in seg_by_old and prev_method_ok(old_cov, s):
                    new_segs[s] = seg_by_old[s]
                    new_cov[s]["method"] = old_cov.loc[s, "method"]
            if time.time() - last_flush > 30:
                flush()
                last_flush = time.time()
        else:
            report["remaining"] = 0
        if progress:
            progress("names: per-symbol", len(pending) - report["remaining"], len(pending))
    finally:
        flush()
        bs_api.close()

    report["elapsed_sec"] = round(time.time() - t0, 1)
    report["complete"] = report["remaining"] == 0
    report["errors"] = report["errors"][:100]
    log.info("names done: %s", {k: v for k, v in report.items() if k != "errors"})
    return report


def prev_method_ok(old_cov: pd.DataFrame | None, s: str) -> bool:
    return old_cov is not None and s in old_cov.index and old_cov.loc[s, "method"] in AUTHORITATIVE


# ---- lookups used by queries, screening and backtests -------------------------

class StHistory:
    """Point-in-time ST lookups.

    Symbols with authoritative coverage use their stored segments; all others
    fall back to whether today's name contains ST.
    """

    def __init__(self, segments: pd.DataFrame, coverage: pd.DataFrame, stocks: pd.DataFrame) -> None:
        self.covered = set(coverage.loc[coverage["method"].isin(AUTHORITATIVE), "symbol"]) if len(coverage) else set()
        names = stocks.set_index("symbol")["name"] if len(stocks) else pd.Series(dtype=str)
        self.current_st = {s for s, n in names.items() if is_st_name(n)}
        self.fallback_st = self.current_st - self.covered
        st = segments[segments["is_st"].astype(bool)] if len(segments) else segments
        st = st[st["symbol"].isin(self.covered)] if len(st) else st
        far = np.datetime64("2262-01-01")
        self._st = pd.DataFrame({
            "symbol": st["symbol"].astype(str).to_numpy() if len(st) else np.array([], dtype=object),
            "start": pd.to_datetime(st["start"]).to_numpy() if len(st) else np.array([], dtype="datetime64[ns]"),
            "end": pd.to_datetime(st["end"]).fillna(pd.Timestamp(far)).to_numpy() if len(st)
            else np.array([], dtype="datetime64[ns]"),
        })
        self._by_sym = {s: (g["start"].to_numpy(), g["end"].to_numpy()) for s, g in self._st.groupby("symbol")}

    @property
    def has_history(self) -> bool:
        return bool(self.covered)

    def is_covered(self, symbol: str) -> bool:
        return symbol in self.covered

    def is_st(self, symbol: str, day) -> bool:
        if symbol not in self.covered:
            return symbol in self.fallback_st
        iv = self._by_sym.get(symbol)
        if iv is None:
            return False
        t = np.datetime64(pd.Timestamp(day))
        return bool(((iv[0] <= t) & (t <= iv[1])).any())

    def on(self, day) -> set[str]:
        """Symbols under risk warning on ``day``."""
        t = np.datetime64(pd.Timestamp(day))
        m = (self._st["start"].to_numpy() <= t) & (t <= self._st["end"].to_numpy())
        return set(self._st["symbol"].to_numpy()[m]) | self.fallback_st

    def mask(self, symbols: pd.Series, dates: pd.Series) -> np.ndarray:
        """Vectorized is_st for row-aligned (symbol, date) pairs."""
        symbols = symbols.astype(str).reset_index(drop=True)
        dates = pd.to_datetime(dates).reset_index(drop=True)
        out = symbols.isin(self.fallback_st).to_numpy().copy()
        if self._st.empty:
            return out
        rows = pd.DataFrame({"symbol": symbols, "date": dates, "i": np.arange(len(symbols))})
        rows = rows[rows["symbol"].isin(self._by_sym)]
        if rows.empty:
            return out
        j = rows.merge(self._st, on="symbol")
        hit = j.loc[(j["start"] <= j["date"]) & (j["date"] <= j["end"]), "i"].unique()
        out[hit] = True
        return out
