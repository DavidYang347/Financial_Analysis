"""Full download and incremental update of A-share daily bars + adjustment factors."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

import pandas as pd

from data import config
from data.adjust import factors_from_events, normalize_factor_points
from data.schema import DAILY_COLUMNS
from data.sources import SourceChain
from data.store import Lake, now_iso
from data.universe import build_calendar, build_universe, latest_complete_trading_day

log = logging.getLogger(__name__)

EVENT_COLS = ["date", "cash", "bonus", "rights", "rights_price"]


@dataclass
class SyncReport:
    mode: str
    started_at: str = field(default_factory=now_iso)
    finished_at: str | None = None
    target_date: str | None = None
    symbols_total: int = 0
    symbols_updated: int = 0
    symbols_failed: int = 0
    symbols_empty: int = 0
    rows_written: int = 0
    factors_recomputed: int = 0
    source_usage: dict = field(default_factory=dict)
    disabled_sources: dict = field(default_factory=dict)
    failed: list = field(default_factory=list)
    complete: bool = True
    elapsed_sec: float = 0.0
    run_id: str | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["failed"] = self.failed[:200]
        return d


ProgressCallback = Callable[[str, int, int], None]  # (phase, done, total)


class Progress:
    def __init__(self, total: int, label: str, every_sec: float = 15.0,
                 callback: ProgressCallback | None = None) -> None:
        self.total, self.label, self.every = total, label, every_sec
        self.done = 0
        self.t0 = self.last = time.time()
        self.lock = threading.Lock()
        self.callback = callback
        if callback:
            callback(label, 0, total)

    def tick(self, n: int = 1) -> None:
        with self.lock:
            self.done += n
            if self.callback:
                self.callback(self.label, self.done, self.total)
            now = time.time()
            if now - self.last >= self.every or self.done == self.total:
                self.last = now
                rate = self.done / max(now - self.t0, 1e-6)
                eta = (self.total - self.done) / rate if rate > 0 else 0
                log.info("%s: %d/%d (%.1f/s, eta %.0fs)", self.label, self.done, self.total, rate, eta)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fetch_factors(chain: SourceChain, symbol: str, bars: pd.DataFrame,
                   order: list[str] | None = None) -> tuple[pd.DataFrame | None, pd.DataFrame | None, str | None, dict]:
    """Returns (factor_points, events, source, errors). factor_points may be empty (no corporate actions)."""

    def call(src):
        if src.name in ("mootdx",):
            ev = src.fetch_adj_events(symbol)
            return factors_from_events(bars, ev), ev
        pts = src.fetch_adj_factor(symbol)
        return normalize_factor_points(pts, bars), None

    res = chain.run(call, order=order or config.FACTOR_SOURCE_ORDER, is_empty=lambda x: x is None)
    if res.data is None:
        return None, None, None, res.errors
    pts, ev = res.data
    return pts, ev, res.source, res.errors


def _refresh_meta(lake: Lake, chain: SourceChain,
                  progress: ProgressCallback | None = None) -> tuple[pd.DataFrame, list[date]]:
    if progress:
        progress("meta", 0, 1)
    cal, cal_src = build_calendar(chain)
    lake.write_calendar(cal)
    log.info("calendar: %d trading days from %s (last listed: %s)", len(cal), cal_src, cal[-1])

    stocks, errs = build_universe(chain)
    if errs:
        log.info("universe source errors: %s", errs)
    old = lake.read_stocks()
    if not old.empty:
        # Never drop symbols we already track; a source may temporarily omit them.
        missing = old[~old["symbol"].isin(stocks["symbol"])]
        if len(missing):
            stocks = pd.concat([stocks, missing], ignore_index=True)
    lake.write_stocks(stocks)
    log.info("universe: %d symbols (%d listed, %d delisted)", len(stocks),
             (stocks["status"] == "listed").sum(), (stocks["status"] == "delisted").sum())
    if progress:
        progress("meta", 1, 1)
    return stocks, cal


def _state_rows(results: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(results, columns=["symbol", "first_date", "last_date", "rows", "last_source",
                                          "factor_source", "updated_at", "last_error"])


def _merge_state(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if old.empty:
        return new
    if new.empty:
        return old
    old = old.set_index("symbol")
    new = new.set_index("symbol")
    for col in new.columns:
        if col not in old.columns:
            old[col] = None
    merged = old.copy()
    for sym_, row in new.iterrows():
        if sym_ in merged.index:
            cur = merged.loc[sym_]
            upd = row.copy()
            # keep the earliest first_date, add rows, keep non-null values
            if pd.notna(cur.get("first_date")) and pd.notna(row.get("first_date")):
                upd["first_date"] = min(cur["first_date"], row["first_date"])
            elif pd.isna(row.get("first_date")):
                upd["first_date"] = cur.get("first_date")
            if pd.isna(row.get("last_date")):
                upd["last_date"] = cur.get("last_date")
            for c in ("last_source", "factor_source"):
                if pd.isna(row.get(c)) or row.get(c) in (None, ""):
                    upd[c] = cur.get(c)
            merged.loc[sym_] = upd
        else:
            merged.loc[sym_] = row
    return merged.reset_index()


def _store_events(lake: Lake, events_by_symbol: dict[str, tuple[pd.DataFrame, str]], replace: bool) -> None:
    frames = []
    for s, (ev, src) in events_by_symbol.items():
        if ev is None:
            continue
        e = ev.copy()
        e["symbol"] = s
        e["source"] = src
        frames.append(e[["symbol"] + EVENT_COLS + ["source"]])
    new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["symbol"] + EVENT_COLS + ["source"])
    if not replace:
        cur = lake.read_events()
        cur = cur[~cur["symbol"].isin(events_by_symbol.keys())]
        new = pd.concat([cur, new], ignore_index=True)
    lake.write_events(new)


# ---------------------------------------------------------------------------
# full download
# ---------------------------------------------------------------------------

def full_download(lake: Lake | None = None, symbols: list[str] | None = None, workers: int | None = None,
                  include_delisted: bool = True, resume: bool = True, refresh_meta: bool = True,
                  time_budget: float | None = None, run_id: str | None = None,
                  progress: ProgressCallback | None = None) -> SyncReport:
    """Download complete daily history for the whole market into the lake.

    Bars are written to per-symbol staging files first, so an interrupted
    run resumes where it stopped. Year partitions are built in one pass once
    every symbol is staged.

    ``time_budget`` (seconds): stop starting new symbols after this long,
    save progress, and return with ``report.complete = False``. Re-run to continue.
    """
    lake = lake or Lake()
    lake.ensure_dirs()
    chain = SourceChain()
    report = SyncReport(mode="full", run_id=run_id)
    t0 = time.time()
    deadline = t0 + time_budget if time_budget else None

    def out_of_time() -> bool:
        return deadline is not None and time.time() > deadline

    try:
        if refresh_meta or not lake.stocks_path.exists() or not lake.calendar_path.exists():
            stocks, cal = _refresh_meta(lake, chain, progress)
        else:
            stocks, cal = lake.read_stocks(), lake.read_calendar()
        target = latest_complete_trading_day(cal)
        report.target_date = target.isoformat()

        todo = stocks if include_delisted else stocks[stocks["status"] == "listed"]
        if symbols:
            todo = todo[todo["symbol"].isin(symbols)]
        todo_syms = list(todo["symbol"])
        report.symbols_total = len(todo_syms)
        list_dates = dict(zip(stocks["symbol"], stocks["list_date"]))

        done = lake.staged_symbols() if resume else set()
        pending = [s for s in todo_syms if s not in done]
        log.info("full download: %d symbols, %d already staged, %d to fetch, target %s",
                 len(todo_syms), len(done & set(todo_syms)), len(pending), target)

        state_rows: list[dict] = []
        factor_frames: list[pd.DataFrame] = []
        events: dict[str, tuple[pd.DataFrame, str]] = {}
        lock = threading.Lock()
        prog = Progress(len(pending), "download", callback=progress)

        skipped: list[str] = []

        def work(s: str) -> None:
            if out_of_time():
                with lock:
                    skipped.append(s)
                return
            ld = list_dates.get(s)
            start = pd.Timestamp(ld).date() if pd.notna(ld) else config.EARLIEST_DATE
            start = max(config.EARLIEST_DATE, start - timedelta(days=7))
            res = chain.daily(s, start, target)
            row = {"symbol": s, "first_date": None, "last_date": None, "rows": 0, "last_source": res.source,
                   "factor_source": None, "updated_at": now_iso(), "last_error": None}
            if res.data is None:
                row["last_error"] = "; ".join(f"{k}: {v}" for k, v in res.errors.items()) or "no data from any source"
                with lock:
                    state_rows.append(row)
                    # Failed only if no source answered at all; otherwise the symbol simply
                    # has no bars yet (new IPO not trading yet, or never traded).
                    if res.empty_answers == 0:
                        report.failed.append({"symbol": s, "errors": res.errors})
                        report.symbols_failed += 1
                    else:
                        row["last_error"] = None
                        report.symbols_empty += 1
                prog.tick()
                return
            bars = res.data
            lake.write_staging(s, bars)
            pts, ev, fsrc, ferr = _fetch_factors(chain, s, bars)
            row.update(first_date=bars["date"].min(), last_date=bars["date"].max(), rows=len(bars), factor_source=fsrc)
            if pts is None:
                row["last_error"] = f"factor: {ferr}"
            with lock:
                state_rows.append(row)
                report.symbols_updated += 1
                report.rows_written += len(bars)
                if pts is not None and len(pts):
                    f = pts.copy()
                    f["symbol"] = s
                    f["source"] = fsrc
                    factor_frames.append(f)
                if ev is not None:
                    events[s] = (ev, fsrc)
            prog.tick()

        with ThreadPoolExecutor(workers or config.WORKERS) as ex:
            futs = [ex.submit(work, s) for s in pending]
            for f in as_completed(futs):
                f.result()

        # Symbols staged by an earlier run that was killed before saving their factors.
        resumed = [s for s in todo_syms if s in done]
        cur_state = lake.read_state()
        have_factor = set(lake.read_adj_factors()["symbol"]) | set(
            cur_state.loc[cur_state["factor_source"].notna(), "symbol"])
        need_factor = [s for s in resumed if s not in have_factor]
        if need_factor:
            import pyarrow.parquet as pq
            log.info("computing factors for %d resumed symbols", len(need_factor))
            prog2 = Progress(len(need_factor), "factors(resumed)", callback=progress)

            def fwork(s: str) -> None:
                if out_of_time():
                    with lock:
                        skipped.append(s)
                    return
                bars = pq.read_table(lake.staging_path(s)).to_pandas()
                pts, ev, fsrc, _ = _fetch_factors(chain, s, bars)
                with lock:
                    state_rows.append({"symbol": s, "first_date": bars["date"].min(), "last_date": bars["date"].max(),
                                       "rows": len(bars), "last_source": bars["source"].iloc[-1],
                                       "factor_source": fsrc, "updated_at": now_iso(),
                                       "last_error": None if pts is not None else "factor fetch failed"})
                    if pts is not None and len(pts):
                        f = pts.copy(); f["symbol"] = s; f["source"] = fsrc
                        factor_frames.append(f)
                    if ev is not None:
                        events[s] = (ev, fsrc)
                prog2.tick()

            with ThreadPoolExecutor(workers or config.WORKERS) as ex:
                list(ex.map(fwork, need_factor))

        # ---- persist ------------------------------------------------------
        new_factors = (pd.concat(factor_frames, ignore_index=True) if factor_frames
                       else pd.DataFrame(columns=["symbol", "date", "adj_factor", "source"]))
        touched = {r["symbol"] for r in state_rows if r.get("factor_source")}
        lake.upsert_adj_factors(new_factors, touched)
        _store_events(lake, events, replace=False)
        lake.write_state(_merge_state(lake.read_state(), _state_rows(state_rows)))

        if skipped:
            report.complete = False
            log.warning("time budget reached: %d symbols left; run the same command again to continue",
                        len(skipped))
        else:
            report.complete = True
            if progress:
                progress("consolidate", 0, 1)
            log.info("building year partitions from staging ...")
            total = lake.consolidate_staging()
            log.info("lake now holds %d daily rows", total)
            lake.clear_staging()
    except Exception as e:
        report.error = f"{type(e).__name__}: {e}"
        raise
    finally:
        report.source_usage = dict(chain.used)
        report.disabled_sources = chain.disabled()
        chain.close()
        report.finished_at = now_iso()
        report.elapsed_sec = round(time.time() - t0, 1)
        lake.append_log(report.as_dict())
    return report


# ---------------------------------------------------------------------------
# incremental update
# ---------------------------------------------------------------------------

def incremental_update(lake: Lake | None = None, symbols: list[str] | None = None, workers: int | None = None,
                       end: date | None = None, refresh_meta: bool = True, run_id: str | None = None,
                       progress: ProgressCallback | None = None, mode: str = "incremental") -> SyncReport:
    """Append bars since each symbol's last stored date and refresh affected adjustment factors."""
    import duckdb

    lake = lake or Lake()
    if not lake.has_daily():
        raise RuntimeError("lake is empty; run the full download first (python -m data.maintenance init)")
    chain = SourceChain()
    report = SyncReport(mode=mode, run_id=run_id)
    t0 = time.time()
    try:
        if refresh_meta:
            stocks, cal = _refresh_meta(lake, chain, progress)
        else:
            stocks, cal = lake.read_stocks(), lake.read_calendar()
        target = end or latest_complete_trading_day(cal)
        report.target_date = target.isoformat()

        state = lake.read_state()
        last_by_sym = {r.symbol: r.last_date for r in state.itertuples() if pd.notna(r.last_date)}
        list_dates = dict(zip(stocks["symbol"], stocks["list_date"]))
        cand = stocks[stocks["status"] == "listed"]["symbol"].tolist()
        if symbols:
            cand = [s for s in symbols if s in set(stocks["symbol"])] or list(symbols)
        todo = []
        for s in cand:
            last = last_by_sym.get(s)
            if last is None:
                ld = list_dates.get(s)
                start = pd.Timestamp(ld).date() - timedelta(days=7) if pd.notna(ld) else config.EARLIEST_DATE
                todo.append((s, max(start, config.EARLIEST_DATE), True))
            elif last < target:
                todo.append((s, last + timedelta(days=1), False))
        report.symbols_total = len(todo)
        log.info("incremental: %d symbols need bars up to %s (%d new listings)",
                 len(todo), target, sum(1 for t in todo if t[2]))

        new_frames: list[pd.DataFrame] = []
        state_rows: list[dict] = []
        lock = threading.Lock()
        prog = Progress(len(todo), "update", callback=progress)

        def work(item) -> None:
            s, start, is_new = item
            # Existing symbol with no new bars is usually suspended: stop after two empty answers.
            res = chain.daily(s, start, target, max_empty_answers=None if is_new else 2)
            with lock:
                if res.data is None:
                    if res.empty_answers == 0 and res.errors:
                        report.symbols_failed += 1
                        report.failed.append({"symbol": s, "errors": res.errors})
                        state_rows.append({"symbol": s, "first_date": None, "last_date": None, "rows": None,
                                           "last_source": None, "factor_source": None, "updated_at": now_iso(),
                                           "last_error": "; ".join(f"{k}: {v}" for k, v in res.errors.items())})
                    else:
                        report.symbols_empty += 1
                else:
                    new_frames.append(res.data)
                    report.symbols_updated += 1
                    report.rows_written += len(res.data)
                    state_rows.append({"symbol": s, "first_date": res.data["date"].min(),
                                       "last_date": res.data["date"].max(), "rows": None,
                                       "last_source": res.source, "factor_source": None,
                                       "updated_at": now_iso(), "last_error": None})
            prog.tick()

        with ThreadPoolExecutor(workers or config.WORKERS) as ex:
            list(ex.map(work, todo))

        if new_frames:
            new_bars = pd.concat(new_frames, ignore_index=True)[DAILY_COLUMNS]
            written = lake.upsert_daily(new_bars)
            log.info("wrote %d rows into partitions %s", sum(written.values()), sorted(written))
        else:
            new_bars = pd.DataFrame(columns=DAILY_COLUMNS)

        # ---- adjustment factors -------------------------------------------
        # Recompute for symbols with new bars whose corporate actions changed, or
        # whose ex-dates fall inside the newly added range, plus new listings.
        updated_syms = sorted(set(new_bars["symbol"])) if len(new_bars) else []
        stored_events = lake.read_events()
        recompute: dict[str, pd.DataFrame | None] = {}
        ev_now: dict[str, tuple[pd.DataFrame, str]] = {}
        prog3 = Progress(len(updated_syms), "check corporate actions", callback=progress)

        def check(s: str) -> None:
            res = chain.run(lambda src: src.fetch_adj_events(s), order=["mootdx"], is_empty=lambda x: x is None)
            prev_last = last_by_sym.get(s)
            new_last = max(new_bars.loc[new_bars["symbol"] == s, "date"])
            with lock:
                if res.data is None:
                    recompute[s] = None  # fall back to baostock/tushare factors
                else:
                    ev = res.data
                    old = stored_events[stored_events["symbol"] == s][EVENT_COLS].reset_index(drop=True)
                    changed = not _events_equal(old, ev)
                    in_range = prev_last is None or any(prev_last < d <= new_last for d in pd.to_datetime(ev["date"]).dt.date)
                    if changed or in_range:
                        recompute[s] = ev
                        ev_now[s] = (ev, "mootdx")
            prog3.tick()

        with ThreadPoolExecutor(workers or config.WORKERS) as ex:
            list(ex.map(check, updated_syms))

        if recompute:
            if progress:
                progress("factors", 0, len(recompute))
            log.info("recomputing adjustment factors for %d symbols", len(recompute))
            con = duckdb.connect()
            syms_sql = ",".join(f"'{s}'" for s in recompute)
            closes = con.execute(f"""
                SELECT symbol, date, close FROM read_parquet('{lake.daily_glob()}')
                WHERE symbol IN ({syms_sql}) ORDER BY symbol, date
            """).df()
            con.close()
            frames, sources = [], {}
            for s, ev in recompute.items():
                bars = closes[closes["symbol"] == s]
                if ev is not None:
                    pts, src = factors_from_events(bars, ev), "mootdx"
                else:
                    pts, ev2, src, _ = _fetch_factors(chain, s, bars, order=[n for n in config.FACTOR_SOURCE_ORDER if n != "mootdx"])
                    if pts is None:
                        continue
                if len(pts):
                    f = pts.copy(); f["symbol"] = s; f["source"] = src
                    frames.append(f)
                sources[s] = src
            new_f = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
                columns=["symbol", "date", "adj_factor", "source"])
            lake.upsert_adj_factors(new_f, set(sources))
            _store_events(lake, ev_now, replace=False)
            report.factors_recomputed = len(sources)
            for r in state_rows:
                if r["symbol"] in sources:
                    r["factor_source"] = sources[r["symbol"]]

        # ---- state ------------------------------------------------------------
        if state_rows:
            st = _state_rows(state_rows)
            if len(new_bars):
                counts = new_bars.groupby("symbol").size()
                st["rows_added"] = st["symbol"].map(counts).fillna(0).astype(int)
            merged = _merge_state(state, st.drop(columns=["rows_added"], errors="ignore"))
            if len(new_bars):
                add = merged["symbol"].map(counts).fillna(0).astype(int)
                merged["rows"] = pd.to_numeric(merged["rows"], errors="coerce").fillna(0).astype(int) + add
            lake.write_state(merged)
        log.info("done: %d symbols updated, %d rows written, %d failed",
                 report.symbols_updated, report.rows_written, report.symbols_failed)
    except Exception as e:
        report.error = f"{type(e).__name__}: {e}"
        raise
    finally:
        report.source_usage = dict(chain.used)
        report.disabled_sources = chain.disabled()
        chain.close()
        report.finished_at = now_iso()
        report.elapsed_sec = round(time.time() - t0, 1)
        lake.append_log(report.as_dict())
    return report


def _events_equal(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    if len(a) != len(b):
        return False
    if len(a) == 0:
        return True
    a = a.copy(); b = b.copy()
    for x in (a, b):
        x["date"] = pd.to_datetime(x["date"]).dt.date
    a = a.sort_values("date").reset_index(drop=True)
    b = b.sort_values("date").reset_index(drop=True)
    if list(a["date"]) != list(b["date"]):
        return False
    for c in EVENT_COLS[1:]:
        if ((pd.to_numeric(a[c]) - pd.to_numeric(b[c])).abs() > 1e-6).any():
            return False
    return True


def repair(lake: Lake | None = None, symbols: list[str] | None = None, workers: int | None = None,
           run_id: str | None = None, progress: ProgressCallback | None = None) -> SyncReport:
    """Delete and re-download the full history of specific symbols."""
    lake = lake or Lake()
    if not symbols:
        raise ValueError("repair needs --symbols")
    log.info("repair: deleting stored bars for %s", ", ".join(symbols))
    lake.delete_symbols(set(symbols))
    st = lake.read_state()
    lake.write_state(st[~st["symbol"].isin(symbols)])
    return incremental_update(lake, symbols=symbols, workers=workers, refresh_meta=False,
                              run_id=run_id, progress=progress, mode="repair")
