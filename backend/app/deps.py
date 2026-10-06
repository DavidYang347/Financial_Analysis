"""Shared dependencies: one MarketData instance and one maintenance job at a time."""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime
from functools import lru_cache

from data.query import MarketData


@lru_cache(maxsize=1)
def market_data() -> MarketData:
    return MarketData()


PHASE_LABELS = {
    "meta": "刷新股票列表和交易日历",
    "update": "下载新增日线",
    "download": "下载日线",
    "check corporate actions": "检查除权除息",
    "factors": "重算复权因子",
    "factors(resumed)": "计算复权因子",
    "consolidate": "合并分区",
}


class MaintenanceJob:
    """Runs one data-maintenance task in a background thread (incremental update or repair).

    The lake lock (data.maintenance.runs.lake_lock) also blocks a CLI run that
    starts at the same time, and vice versa.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state: dict = {"status": "idle"}

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.state)

    def start(self, kind: str, symbols: list[str] | None = None, refresh_meta: bool = True) -> dict:
        from data.maintenance.runs import is_locked, new_run_id

        md = market_data()
        with self._lock:
            if self.state.get("status") == "running":
                raise RuntimeError("an update is already running")
            if is_locked(md.lake):
                raise RuntimeError("another data update is running (probably from the command line)")
            run_id = new_run_id("incremental" if kind == "update" else kind)
            self.state = {
                "status": "running", "run_id": run_id, "kind": kind, "symbols": symbols,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "phase": "starting", "phase_label": "准备中", "done": 0, "total": 0,
            }
        threading.Thread(target=self._run, args=(run_id, kind, symbols, refresh_meta), daemon=True).start()
        return self.snapshot()

    def _progress(self, phase: str, done: int, total: int) -> None:
        with self._lock:
            self.state.update(phase=phase, phase_label=PHASE_LABELS.get(phase, phase), done=done, total=total)

    def _run(self, run_id: str, kind: str, symbols: list[str] | None, refresh_meta: bool) -> None:
        from data.maintenance.runs import LakeBusy, capture_log, lake_lock
        from data.maintenance.sync import incremental_update, repair

        md = market_data()
        t0 = time.time()
        result: dict
        try:
            with lake_lock(md.lake), capture_log(md.lake, run_id):
                if kind == "repair":
                    rep = repair(md.lake, symbols=symbols, run_id=run_id, progress=self._progress)
                else:
                    rep = incremental_update(md.lake, symbols=symbols, refresh_meta=refresh_meta,
                                             run_id=run_id, progress=self._progress)
            result = {"status": "finished", "report": rep.as_dict()}
        except LakeBusy as e:
            result = {"status": "failed", "error": str(e)}
        except Exception as e:  # surfaced to the UI; the full trace is in the run log
            result = {"status": "failed", "error": f"{type(e).__name__}: {e}",
                      "trace": traceback.format_exc()[-2000:]}
        md.refresh_views()
        with self._lock:
            self.state = {**self.state, **result, "elapsed_sec": round(time.time() - t0, 1),
                          "finished_at": datetime.now().isoformat(timespec="seconds")}


@lru_cache(maxsize=1)
def maintenance_job() -> MaintenanceJob:
    return MaintenanceJob()
