"""Runs a screener and keeps a history of results.

Results are stored under ``data/lake/screens/<run_id>.parquet`` with a summary
line in ``data/lake/screens/history.jsonl``.
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from data.query import MarketData
from screening.context import ScreenContext
from screening.params import ParamError, resolve_params
from screening.registry import Screener

RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9_]+(?:-[0-9]+)?$")
MAX_RESULT_ROWS = 2000


class ScreenError(RuntimeError):
    pass


class ScreenStore:
    def __init__(self, md: MarketData) -> None:
        self.dir = md.lake.root / "screens"
        self._lock = threading.Lock()

    @property
    def history_path(self) -> Path:
        return self.dir / "history.jsonl"

    def result_path(self, run_id: str) -> Path:
        if not RUN_ID_RE.match(run_id):
            raise ValueError(f"invalid run id {run_id!r}")
        return self.dir / f"{run_id}.parquet"

    def new_run_id(self, sid: str) -> str:
        base = f"{datetime.now():%Y%m%d-%H%M%S}-{sid}"
        rid, i = base, 1
        while (self.dir / f"{rid}.parquet").exists():
            i += 1
            rid = f"{base}-{i}"
        return rid

    def save(self, record: dict, result: pd.DataFrame | None) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            if result is not None:
                result.to_parquet(self.result_path(record["run_id"]), index=False)
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def history(self, screener_id: str | None = None, limit: int = 50) -> list[dict]:
        if not self.history_path.exists():
            return []
        out = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if screener_id and r.get("screener_id") != screener_id:
                continue
            out.append(r)
        return list(reversed(out))[:limit]

    def get(self, run_id: str) -> tuple[dict | None, pd.DataFrame | None]:
        rec = next((r for r in self.history(limit=100_000) if r["run_id"] == run_id), None)
        if rec is None:
            return None, None
        p = self.result_path(run_id)
        return rec, (pd.read_parquet(p) if p.exists() else None)


def _clean_result(df: pd.DataFrame, ctx: ScreenContext) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        raise ScreenError("screen() 必须返回 pandas.DataFrame")
    if "symbol" not in df.columns:
        raise ScreenError("screen() 的返回结果缺少 symbol 列")
    df = df.drop_duplicates("symbol").reset_index(drop=True)
    names = ctx.stocks().set_index("symbol")["name"]
    if "name" not in df.columns:
        df.insert(1, "name", df["symbol"].map(names))
    # JSON-friendly types
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
        elif df[c].dtype == object:
            df[c] = df[c].map(lambda v: v.isoformat() if isinstance(v, date) else v)
        elif pd.api.types.is_float_dtype(df[c]):
            df[c] = df[c].replace([np.inf, -np.inf], np.nan).round(4)
    return df.head(MAX_RESULT_ROWS)


def run_screener(screener: Screener, md: MarketData, params: dict | None = None,
                 as_of: date | None = None, store: ScreenStore | None = None) -> dict:
    if not screener.ok:
        raise ScreenError(f"{screener.id} 加载失败: {screener.error}")
    resolved = resolve_params(screener.params, params)  # raises ParamError
    store = store or ScreenStore(md)
    run_id = store.new_run_id(screener.id)
    t0 = time.time()
    record = {
        "run_id": run_id, "screener_id": screener.id, "screener_name": screener.name,
        "screener_version": screener.version, "params": resolved,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    result = None
    try:
        ctx = ScreenContext(md, as_of=as_of)
        record["as_of"] = ctx.as_of.isoformat()
        result = _clean_result(screener.module.screen(ctx, dict(resolved)), ctx)
        record.update(status="ok", count=int(len(result)))
    except (ScreenError, ParamError) as e:
        record.update(status="failed", error=str(e))
    except Exception as e:
        record.update(status="failed", error=f"{type(e).__name__}: {e}",
                      trace=traceback.format_exc(limit=6)[-3000:])
    record["elapsed_sec"] = round(time.time() - t0, 2)
    store.save(record, result if record["status"] == "ok" else None)
    return {**record, "columns": _columns(result, screener), "items": _records(result)}


def _columns(df: pd.DataFrame | None, screener: Screener) -> list[dict]:
    if df is None:
        return []
    base = {"symbol": "代码", "name": "名称"}
    return [{"key": c, "label": screener.columns.get(c) or base.get(c, c)} for c in df.columns]


def _records(df: pd.DataFrame | None) -> list[dict]:
    if df is None:
        return []
    return df.astype(object).where(df.notna(), None).to_dict("records")


def load_run(store: ScreenStore, run_id: str, screener: Screener | None) -> dict | None:
    rec, df = store.get(run_id)
    if rec is None:
        return None
    cols = _columns(df, screener) if screener else ([{"key": c, "label": c} for c in df.columns] if df is not None else [])
    return {**rec, "columns": cols, "items": _records(df)}
