"""Runs a backtest and keeps every result on disk.

    data/lake/backtests/history.jsonl        one summary line per run
    data/lake/backtests/<run_id>/report.json  settings, params, metrics, monthly returns, logs
    data/lake/backtests/<run_id>/equity.parquet / trades.parquet / holdings.parquet
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import traceback
from dataclasses import fields
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from data.query import MarketData
from screening.params import ParamError, resolve_params
from strategy import metrics
from strategy.engine import BacktestError, BacktestSettings, Cancelled, Engine
from strategy.registry import Strategy

RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9_]+(?:-[0-9]+)?$")


class BacktestStore:
    def __init__(self, md: MarketData) -> None:
        self.dir = md.lake.root / "backtests"
        self._lock = threading.Lock()

    @property
    def history_path(self) -> Path:
        return self.dir / "history.jsonl"

    def run_dir(self, run_id: str) -> Path:
        if not RUN_ID_RE.match(run_id or ""):
            raise ValueError(f"invalid run id {run_id!r}")
        return self.dir / run_id

    def new_run_id(self, sid: str) -> str:
        base = f"{datetime.now():%Y%m%d-%H%M%S}-{sid}"
        rid, i = base, 1
        while (self.dir / rid).exists():
            i += 1
            rid = f"{base}-{i}"
        (self.dir / rid).mkdir(parents=True)
        return rid

    def save(self, record: dict, report: dict | None, frames: dict[str, pd.DataFrame] | None) -> None:
        with self._lock:
            d = self.run_dir(record["run_id"])
            d.mkdir(parents=True, exist_ok=True)
            for name, df in (frames or {}).items():
                df.to_parquet(d / f"{name}.parquet", index=False)
            if report is not None:
                (d / "report.json").write_text(json.dumps(report, ensure_ascii=False, default=str), encoding="utf-8")
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def history(self, strategy_id: str | None = None, limit: int = 50) -> list[dict]:
        if not self.history_path.exists():
            return []
        out = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if strategy_id and r.get("strategy_id") != strategy_id:
                continue
            out.append(r)
        out = [r for r in out if (self.dir / r["run_id"]).exists() or r.get("status") != "ok"]
        return list(reversed(out))[:limit]

    def record(self, run_id: str) -> dict | None:
        return next((r for r in self.history(limit=100_000) if r["run_id"] == run_id), None)

    def report(self, run_id: str) -> dict | None:
        p = self.run_dir(run_id) / "report.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def frame(self, run_id: str, name: str) -> pd.DataFrame | None:
        """equity / trades / holdings, or any extra table the strategy exported from finalize()."""
        if not re.match(r"^[a-z][a-z0-9_]{0,40}$", name or ""):
            raise ValueError(name)
        p = self.run_dir(run_id) / f"{name}.parquet"
        return pd.read_parquet(p) if p.exists() else None

    def extra_frames(self, run_id: str) -> list[str]:
        d = self.run_dir(run_id)
        return sorted(p.stem for p in d.glob("*.parquet") if p.stem not in ("equity", "trades", "holdings"))


def build_settings(strategy: Strategy, given: dict, md: MarketData) -> BacktestSettings:
    """Script defaults < request values. Missing dates: last 3 years up to the latest bar."""
    names = {f.name for f in fields(BacktestSettings)}
    unknown = sorted(set(given) - names)
    if unknown:
        raise BacktestError(f"未知的回测设置: {', '.join(unknown)}")
    merged = {**strategy.defaults, **{k: v for k, v in given.items() if v is not None and v != ""}}
    latest = md.latest_date()
    if latest is None:
        raise BacktestError("行情库为空，先在数据管理里完成数据下载")
    end = _date(merged.get("end")) or latest
    end = min(end, latest)
    start = _date(merged.get("start")) or date(end.year - 3, end.month, min(end.day, 28))
    merged.update(start=start, end=end)
    try:
        s = BacktestSettings(**merged)
        for f in fields(BacktestSettings):  # field types are strings under `from __future__ import annotations`
            v = getattr(s, f.name)
            if f.type == "float":
                setattr(s, f.name, float(v))
            elif f.type == "int":
                if float(v) != int(float(v)):
                    raise ValueError(f"{f.name} 需要整数")
                setattr(s, f.name, int(float(v)))
            elif f.type == "str":
                setattr(s, f.name, str(v).strip())
    except (TypeError, ValueError) as e:
        raise BacktestError(f"回测设置无效: {e}") from e
    if isinstance(s.benchmark, str) and s.benchmark not in ("equal", "none", ""):
        from data import symbols as sym
        try:
            s.benchmark = sym.normalize(s.benchmark)
        except ValueError as e:
            raise BacktestError(f"基准 {s.benchmark} 不是有效的股票代码；可以用 equal（全市场等权）或 none") from e
    s.validate()
    return s


def _date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError as e:
        raise BacktestError(f"日期 {v!r} 无效") from e


def _json_records(df: pd.DataFrame) -> list[dict]:
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
        elif df[c].dtype == object:
            df[c] = df[c].map(lambda v: v.isoformat() if isinstance(v, date) else v)
        elif pd.api.types.is_float_dtype(df[c]):
            df[c] = df[c].replace([np.inf, -np.inf], np.nan)
    return df.astype(object).where(df.notna(), None).to_dict("records")


def run_backtest(strategy: Strategy, md: MarketData, params: dict | None, settings: dict | None,
                 store: BacktestStore | None = None, progress=None, cancelled=None,
                 run_id: str | None = None) -> dict:
    """Run synchronously and save the result. Returns the history record."""
    if not strategy.ok:
        raise BacktestError(f"{strategy.id} 加载失败: {strategy.error}")
    resolved = resolve_params(strategy.params, params)  # raises ParamError
    s = build_settings(strategy, dict(settings or {}), md)
    store = store or BacktestStore(md)
    run_id = run_id or store.new_run_id(strategy.id)
    t0 = time.time()
    record = {
        "run_id": run_id, "strategy_id": strategy.id, "strategy_name": strategy.name,
        "strategy_version": strategy.version, "params": resolved, "settings": s.to_dict(),
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    report, frames = None, None
    try:
        res = Engine(strategy.module.rebalance, resolved, s, md, progress=progress, cancelled=cancelled,
                     finalize_fn=getattr(strategy.module, "finalize", None)).run()
        m = metrics.compute(res.equity, res.trades, s.initial_cash, s.risk_free)
        report = {"metrics": m, "monthly": metrics.monthly_returns(res.equity, s.initial_cash),
                  "logs": res.logs, "signals": res.signals}
        frames = {"equity": res.equity, "trades": res.trades, "holdings": res.holdings, **res.extra}
        record.update(status="ok", metrics={k: m.get(k) for k in (
            "total_return", "annual_return", "max_drawdown", "sharpe", "excess_return", "trades", "final_equity")})
    except Cancelled:
        record.update(status="cancelled", error="已取消")
    except (BacktestError, ParamError) as e:
        record.update(status="failed", error=str(e))
    except Exception as e:
        record.update(status="failed", error=f"{type(e).__name__}: {e}",
                      trace=traceback.format_exc(limit=8)[-4000:])
    record["elapsed_sec"] = round(time.time() - t0, 2)
    if record["status"] != "ok":
        shutil.rmtree(store.run_dir(run_id), ignore_errors=True)
    store.save(record, report and {**record, **report}, frames)
    return record


def load_report(store: BacktestStore, run_id: str) -> dict | None:
    rec = store.record(run_id)
    if rec is None:
        return None
    rep = store.report(run_id) or {}
    eq = store.frame(run_id, "equity")
    out = {**rec, **rep, "equity": []}
    if eq is not None and not eq.empty:
        init = float(rec["settings"]["initial_cash"])
        eq = eq.copy()
        eq["nav"] = eq["equity"] / init
        eq["drawdown"] = metrics.drawdown(eq["nav"])
        if "benchmark" in eq.columns:
            eq["benchmark_nav"] = eq["benchmark"] / init
            eq["benchmark_drawdown"] = metrics.drawdown(eq["benchmark_nav"])
        for c in eq.columns:
            if c not in ("date", "positions") and pd.api.types.is_float_dtype(eq[c]):
                eq[c] = eq[c].round(6)
        out["equity"] = _json_records(eq)
    return out


def load_trades(store: BacktestStore, run_id: str, status: str | None = None,
                symbol: str | None = None, offset: int = 0, limit: int = 200) -> dict:
    df = store.frame(run_id, "trades")
    if df is None:
        return {"total": 0, "items": []}
    if status:
        df = df[df["status"] == status]
    if symbol:
        k = symbol.strip().upper()
        df = df[df["symbol"].str.contains(k, regex=False) | df["name"].fillna("").str.contains(symbol.strip(), regex=False)]
    return {"total": int(len(df)), "items": _json_records(df.iloc[offset: offset + limit])}


def load_holdings(store: BacktestStore, run_id: str, day: str | None = None) -> dict:
    """Holdings on ``day`` (default: last day), plus the list of available days."""
    df = store.frame(run_id, "holdings")
    eq = store.frame(run_id, "equity")
    if df is None or eq is None:
        return {"date": None, "days": [], "items": []}
    days = [str(x) for x in eq["date"]]
    target = day or (days[-1] if days else None)
    if target and target not in days:
        earlier = [d for d in days if d <= target]
        target = earlier[-1] if earlier else days[0]
    sel = df[df["date"].astype(str) == target].sort_values("weight", ascending=False)
    names = {}
    tr = store.frame(run_id, "trades")
    if tr is not None and not tr.empty:
        names = tr.drop_duplicates("symbol").set_index("symbol")["name"].to_dict()
    sel = sel.assign(name=sel["symbol"].map(names))
    row = eq[eq["date"].astype(str) == target]
    return {"date": target, "days": days, "items": _json_records(sel),
            "cash": float(row["cash"].iloc[0]) if len(row) else None,
            "equity": float(row["equity"].iloc[0]) if len(row) else None}


def trades_csv(store: BacktestStore, run_id: str) -> bytes | None:
    df = store.frame(run_id, "trades")
    if df is None:
        return None
    return ("﻿" + df.to_csv(index=False)).encode("utf-8")


class BacktestJobs:
    """Background backtests, one at a time (they are CPU and memory heavy)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state: dict = {"status": "idle"}
        self._cancel = threading.Event()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.state)

    def start(self, strategy: Strategy, md: MarketData, params: dict, settings: dict) -> dict:
        # Validate up front so bad input is a 422, not a failed background run.
        resolved = resolve_params(strategy.params, params)
        s = build_settings(strategy, dict(settings), md)
        store = BacktestStore(md)
        with self._lock:
            if self.state.get("status") == "running":
                raise BacktestError(f"已有回测在运行（{self.state.get('strategy_name')}），等它结束或先取消")
            run_id = store.new_run_id(strategy.id)
            self._cancel.clear()
            self.state = {"status": "running", "run_id": run_id, "strategy_id": strategy.id,
                          "strategy_name": strategy.name, "done": 0, "total": 0,
                          "started_at": datetime.now().isoformat(timespec="seconds")}
        threading.Thread(target=self._run, args=(strategy, md, resolved, s.to_dict(), store, run_id),
                         daemon=True).start()
        return self.snapshot()

    def cancel(self) -> dict:
        self._cancel.set()
        return self.snapshot()

    def _progress(self, done: int, total: int) -> None:
        with self._lock:
            self.state.update(done=done, total=total)

    def _run(self, strategy, md, params, settings, store, run_id) -> None:
        try:
            rec = run_backtest(strategy, md, params, settings, store, progress=self._progress,
                               cancelled=self._cancel.is_set, run_id=run_id)
        except Exception as e:  # only reached for errors outside the engine
            rec = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
        with self._lock:
            self.state = {**self.state, "status": rec.get("status", "failed"), "error": rec.get("error"),
                          "elapsed_sec": rec.get("elapsed_sec"),
                          "finished_at": datetime.now().isoformat(timespec="seconds")}


_jobs: BacktestJobs | None = None


def jobs() -> BacktestJobs:
    global _jobs
    if _jobs is None:
        _jobs = BacktestJobs()
    return _jobs
