"""Discovers strategy scripts.

One file in ``strategy/strategies/`` = one strategy. Add a strategy by adding
a ``.py`` file; remove it by deleting the file. Files starting with ``_`` are
ignored (use them for shared helpers).

A script defines:

    META = {
        "name": "双均线择时",                   # display name (required)
        "description": "markdown text ...",    # what it does and why (required)
        "tags": ["趋势"], "author": "...", "version": "1.0",   # optional
        "defaults": {"rebalance": "daily", "lookback": 120},  # optional backtest defaults
    }
    PARAMS = [Param(...), ...]                 # optional
    def rebalance(ctx, params) -> dict[str, float] | None    # required: target weights
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from screening.registry import Registry, Screener

STRATEGIES_DIR = Path(__file__).resolve().parent / "strategies"

# Backtest settings a script may preset in META["defaults"].
DEFAULT_KEYS = {"rebalance", "every_n", "fill", "lookback", "benchmark", "initial_cash",
                "commission", "min_commission", "stamp_tax", "slippage", "tolerance", "start"}


@dataclass
class Strategy(Screener):
    defaults: dict = field(default_factory=dict)

    def detail(self) -> dict:
        d = super().detail()
        d.pop("columns", None)
        return {**d, "defaults": self.defaults}


class StrategyRegistry(Registry):
    entry = "rebalance"
    entry_signature = "rebalance(ctx, params)"
    namespace = "_strategies"
    item_cls = Strategy

    def __init__(self, directory: Path = STRATEGIES_DIR) -> None:
        super().__init__(directory)

    def validate(self, s: Strategy, mod: ModuleType) -> None:
        defaults = s.meta.get("defaults") or {}
        if not isinstance(defaults, dict):
            raise ValueError("META['defaults'] 需要是字典")
        bad = sorted(set(defaults) - DEFAULT_KEYS)
        if bad:
            raise ValueError(f"META['defaults'] 里有不认识的设置: {', '.join(bad)}")
        s.defaults = dict(defaults)


_registry: StrategyRegistry | None = None


def registry() -> StrategyRegistry:
    global _registry
    if _registry is None:
        _registry = StrategyRegistry()
    return _registry
