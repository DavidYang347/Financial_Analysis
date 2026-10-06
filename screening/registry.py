"""Discovers screener scripts.

One file in ``screening/screeners/`` = one screening method. Add a method by
adding a ``.py`` file; remove it by deleting the file. Files starting with
``_`` are ignored (use them for shared helpers).

A script defines:

    META = {
        "name": "均线多头排列",                 # display name (required)
        "description": "markdown text ...",    # what it selects and why (required)
        "tags": ["趋势"],                       # optional
        "author": "...", "version": "1.0",     # optional
        "columns": {"ma20": "MA20", ...},      # optional: result column labels
    }
    PARAMS = [Param(...), ...]                 # optional
    def screen(ctx, params) -> pandas.DataFrame   # required; must include a "symbol" column
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from screening.params import Param

SCREENERS_DIR = Path(__file__).resolve().parent / "screeners"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")


@dataclass
class Screener:
    id: str
    path: Path
    mtime: float
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    author: str = ""
    version: str = ""
    columns: dict[str, str] = field(default_factory=dict)
    params: list[Param] = field(default_factory=list)
    module: ModuleType | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.module is not None

    def summary(self) -> dict:
        return {
            "id": self.id, "name": self.name or self.id, "tags": self.tags, "author": self.author,
            "version": self.version, "file": self.path.name, "ok": self.ok, "error": self.error,
            "param_count": len(self.params), "brief": _first_line(self.description),
        }

    def detail(self) -> dict:
        return {**self.summary(), "description": self.description, "columns": self.columns,
                "params": [p.to_dict() for p in self.params]}

    def source(self) -> str:
        return self.path.read_text(encoding="utf-8")


def _first_line(md: str) -> str:
    for line in (md or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:120]
    return ""


class Registry:
    """Re-scans the directory on every access and re-imports files whose mtime changed."""

    def __init__(self, directory: Path = SCREENERS_DIR) -> None:
        self.directory = directory
        self._cache: dict[str, Screener] = {}
        self._lock = threading.Lock()

    def _load(self, sid: str, path: Path, mtime: float) -> Screener:
        s = Screener(id=sid, path=path, mtime=mtime)
        # Unique module name per file version so edits are picked up.
        digest = hashlib.sha1(f"{path}:{mtime}".encode()).hexdigest()[:10]
        mod_name = f"_screeners.{sid}_{digest}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {path.name}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
            meta = getattr(mod, "META", None)
            if not isinstance(meta, dict) or not meta.get("name"):
                raise ValueError("META 缺失或没有 name")
            if not callable(getattr(mod, "screen", None)):
                raise ValueError("没有定义 screen(ctx, params) 函数")
            params = list(getattr(mod, "PARAMS", []) or [])
            if not all(isinstance(p, Param) for p in params):
                raise ValueError("PARAMS 里只能放 Param 对象")
            keys = [p.key for p in params]
            if len(keys) != len(set(keys)):
                raise ValueError("PARAMS 里有重复的 key")
            s.module = mod
            s.name = str(meta["name"])
            s.description = str(meta.get("description", ""))
            s.tags = [str(t) for t in meta.get("tags", [])]
            s.author = str(meta.get("author", ""))
            s.version = str(meta.get("version", ""))
            s.columns = {str(k): str(v) for k, v in (meta.get("columns") or {}).items()}
            s.params = params
        except Exception as e:
            s.error = f"{type(e).__name__}: {e}"
            s.description = "```\n" + traceback.format_exc(limit=4) + "\n```"
        return s

    def scan(self) -> list[Screener]:
        with self._lock:
            found: dict[str, Screener] = {}
            if self.directory.exists():
                for path in sorted(self.directory.glob("*.py")):
                    sid = path.stem
                    if sid.startswith("_") or not ID_RE.match(sid):
                        continue
                    mtime = path.stat().st_mtime
                    cached = self._cache.get(sid)
                    found[sid] = cached if cached and cached.mtime == mtime and cached.path == path \
                        else self._load(sid, path, mtime)
            self._cache = found
            return sorted(found.values(), key=lambda s: (s.name or s.id))

    def get(self, sid: str) -> Screener | None:
        if not ID_RE.match(sid or ""):
            return None
        return next((s for s in self.scan() if s.id == sid), None)


_registry: Registry | None = None


def registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry()
    return _registry
