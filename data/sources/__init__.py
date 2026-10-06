"""Source registry and the fallback chain."""
from __future__ import annotations

import logging
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import pandas as pd

from data import config
from data.sources.akshare_source import AkshareSource
from data.sources.baostock_source import BaostockSource
from data.sources.base import DataSource, SourceError, SourceUnavailable
from data.sources.eastmoney import EastmoneySource
from data.sources.mootdx_source import MootdxSource
from data.sources.tencent import TencentSource
from data.sources.tushare_source import TushareSource

log = logging.getLogger(__name__)

REGISTRY: dict[str, type[DataSource]] = {
    "tencent": TencentSource,
    "mootdx": MootdxSource,
    "eastmoney": EastmoneySource,
    "baostock": BaostockSource,
    "akshare": AkshareSource,
    "tushare": TushareSource,
}

__all__ = ["REGISTRY", "SourceChain", "ChainResult", "DataSource", "SourceError", "SourceUnavailable"]


@dataclass
class ChainResult:
    data: object
    source: str | None
    errors: dict[str, str] = field(default_factory=dict)
    # Sources that answered successfully but had no data (e.g. not yet trading).
    empty_answers: int = 0


class SourceChain:
    """Tries sources in order. A source that errors repeatedly is skipped for the rest of the run."""

    def __init__(self, order: list[str] | None = None, instances: dict[str, DataSource] | None = None,
                 max_consecutive_errors: int = config.SOURCE_MAX_CONSECUTIVE_ERRORS) -> None:
        self.order = order or list(config.DAILY_SOURCE_ORDER)
        unknown = [n for n in self.order if n not in REGISTRY]
        if unknown:
            raise ValueError(f"unknown sources: {unknown}; available: {list(REGISTRY)}")
        self._instances: dict[str, DataSource] = dict(instances or {})
        self._disabled: dict[str, str] = {}
        self._consecutive: Counter = Counter()
        self._lock = threading.Lock()
        self.max_consecutive_errors = max_consecutive_errors
        self.used: Counter = Counter()

    def source(self, name: str) -> DataSource:
        with self._lock:
            if name not in self._instances:
                self._instances[name] = REGISTRY[name]()
            return self._instances[name]

    def disabled(self) -> dict[str, str]:
        return dict(self._disabled)

    def _ok(self, name: str) -> None:
        with self._lock:
            self._consecutive[name] = 0

    def _fail(self, name: str, err: Exception) -> None:
        with self._lock:
            if isinstance(err, SourceUnavailable):
                if name not in self._disabled:
                    log.warning("source %s disabled: %s", name, err)
                self._disabled[name] = str(err)
                return
            self._consecutive[name] += 1
            if self._consecutive[name] >= self.max_consecutive_errors and name not in self._disabled:
                self._disabled[name] = f"{self._consecutive[name]} consecutive errors, last: {err}"
                log.warning("source %s disabled for this run: %s", name, self._disabled[name])

    def run(self, call: Callable[[DataSource], object], order: list[str] | None = None,
            is_empty: Callable[[object], bool] | None = None,
            max_empty_answers: int | None = None) -> ChainResult:
        """Call ``call(source)`` down the chain until one returns non-empty data.

        ``max_empty_answers``: stop after this many sources answered successfully
        but with no data (e.g. a suspended stock has no new bars; there is no
        point asking all six sources).
        """
        is_empty = is_empty or (lambda x: x is None or (isinstance(x, pd.DataFrame) and x.empty) or
                                (isinstance(x, list) and not x))
        errors: dict[str, str] = {}
        empty_answers = 0
        for name in order or self.order:
            if name in self._disabled:
                continue
            if max_empty_answers is not None and empty_answers >= max_empty_answers:
                break
            try:
                result = call(self.source(name))
            except NotImplementedError:
                continue
            except SourceUnavailable as e:
                # Unsupported operation for this source -> skip without penalizing it.
                if "does not provide" in str(e):
                    continue
                self._fail(name, e)
                errors[name] = str(e)[:300]
                continue
            except Exception as e:  # network, parse errors, library bugs
                self._fail(name, e)
                errors[name] = f"{type(e).__name__}: {e}"[:300]
                continue
            self._ok(name)
            if not is_empty(result):
                with self._lock:
                    self.used[name] += 1
                return ChainResult(result, name, errors, empty_answers)
            empty_answers += 1
        return ChainResult(None, None, errors, empty_answers)

    # ---- convenience wrappers -----------------------------------------
    def daily(self, symbol: str, start: date, end: date, max_empty_answers: int | None = None) -> ChainResult:
        return self.run(lambda s: s.fetch_daily(symbol, start, end), max_empty_answers=max_empty_answers)

    def close(self) -> None:
        for inst in self._instances.values():
            try:
                inst.close()
            except Exception:
                pass
