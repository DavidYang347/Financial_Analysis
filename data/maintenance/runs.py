"""Run bookkeeping shared by the CLI and the API.

Every maintenance run gets a run_id, a log file under ``meta/runs/<run_id>.log``
and one summary line in ``meta/update_log.jsonl`` (written by sync.py).
A file lock makes sure only one process writes to the lake at a time.
"""
from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from data.store import Lake

RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z]+(?:-[0-9]+)?$")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class LakeBusy(RuntimeError):
    """Another maintenance run holds the lake lock."""


def new_run_id(mode: str) -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{mode}"


def runs_dir(lake: Lake) -> Path:
    return lake.meta_dir / "runs"


def log_path(lake: Lake, run_id: str) -> Path:
    if not RUN_ID_RE.match(run_id):
        raise ValueError(f"invalid run id: {run_id!r}")
    return runs_dir(lake) / f"{run_id}.log"


@contextlib.contextmanager
def lake_lock(lake: Lake):
    """Exclusive, non-blocking lock on the lake. Raises LakeBusy if held."""
    lake.meta_dir.mkdir(parents=True, exist_ok=True)
    path = lake.meta_dir / ".maintenance.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise LakeBusy("another data update is running") from e
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def is_locked(lake: Lake) -> bool:
    try:
        with lake_lock(lake):
            return False
    except LakeBusy:
        return True


@contextlib.contextmanager
def capture_log(lake: Lake, run_id: str, level: int = logging.INFO):
    """Copy all log records emitted during the run into the run's log file."""
    path = log_path(lake, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger()
    old_level = root.level
    if root.level > level or root.level == logging.NOTSET:
        root.setLevel(level)
    root.addHandler(handler)
    try:
        yield path
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        handler.close()


def list_runs(lake: Lake, limit: int = 50, offset: int = 0) -> tuple[int, list[dict]]:
    """Run summaries, newest first. Older entries without run_id get a stable synthetic id."""
    records = lake.read_log(limit=100_000)
    for i, r in enumerate(records):
        if not r.get("run_id"):
            r["run_id"] = f"legacy-{i}"
        r["has_log"] = _has_log(lake, r["run_id"])
        r.pop("failed", None)
    records.reverse()
    return len(records), records[offset: offset + limit]


def get_run(lake: Lake, run_id: str) -> dict | None:
    records = lake.read_log(limit=100_000)
    for i, r in enumerate(records):
        rid = r.get("run_id") or f"legacy-{i}"
        if rid == run_id:
            r["run_id"] = rid
            r["has_log"] = _has_log(lake, rid)
            return r
    return None


def read_log(lake: Lake, run_id: str, offset: int = 0, max_bytes: int = 256_000) -> tuple[str, int]:
    """Returns (text, next_offset). Use next_offset to tail a running log."""
    path = log_path(lake, run_id)
    if not path.exists():
        return "", 0
    with open(path, "rb") as f:
        f.seek(max(0, offset))
        chunk = f.read(max_bytes)
    return chunk.decode("utf-8", errors="replace"), max(0, offset) + len(chunk)


def _has_log(lake: Lake, run_id: str) -> bool:
    try:
        return log_path(lake, run_id).exists()
    except ValueError:
        return False
