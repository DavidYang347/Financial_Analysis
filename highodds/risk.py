"""仓位与组合风控(文档第 15、17 节)。纯函数,状态由 book.py 持有。"""
from __future__ import annotations

from dataclasses import dataclass


def gap_buffer(board: str, is_st: bool, binary: bool, cfg) -> float:
    """跳空缓冲:主板 5%;ST、创业板、科创板 10%;北交所和二元事件 15%。"""
    if board == "北交所" or binary:
        return cfg.gap_bj_event
    if is_st or board in ("创业板", "科创板"):
        return cfg.gap_high
    return cfg.gap_main


def risk_budget(conviction: str, binary: bool, is_st: bool, mcap_yi: float | None, cfg) -> float:
    """单笔风险预算:低确信 0.5%,标准 1.0%,高确信 1.5%;二元事件、ST、小盘股减半。"""
    b = {"high": cfg.risk_high, "standard": cfg.risk_std, "low": cfg.risk_low}.get(conviction, 0.0)
    if binary:
        b *= 0.5
    if is_st:
        b *= 0.5
    if mcap_yi is not None and mcap_yi < cfg.small_cap_mcap:
        b *= 0.5
    return b


def position_size(budget: float, downside: float, gap: float) -> float:
    """单票仓位 = 单笔风险预算 ÷ (证伪下行 + 跳空缓冲)。"""
    return budget / (downside + gap) if downside + gap > 0 else 0.0


def kelly_cap(p_win: float, R: float, fraction: float = 0.25) -> float:
    """f* = p - (1 - p) / R,实操 1/4 凯利,作为仓位上限的合理性检查。"""
    if R <= 0:
        return 0.0
    return max(0.0, fraction * (p_win - (1 - p_win) / R))


def bucket_of(binary: bool, conviction: str, odds: float, cfg) -> str:
    """杠铃组合三层:核心仓 / 事件仓 / 期权仓。
    期权仓 = 低确信但赔率够高(文档:概率低但赔率 ≥ 8:1,如重整、摘帽博弈),或评分不足的试错。"""
    if conviction == "low" or (binary and odds >= 8):
        return "option"
    return "event" if binary else "core"


@dataclass
class Breaker:
    """回撤熔断(文档 17.2)。"""
    peak: float = 0.0
    level: int = 0
    frozen_until: int = -1   # 交易日序号
    applied: int = 0         # 该次回撤已执行过的最高级别动作,避免同一次回撤里反复减仓

    def update(self, equity: float, day_no: int, cfg) -> tuple[int, int, float]:
        """返回 (当前级别, 本次新触发的级别(0=没有), 回撤)。创新高则重置。"""
        if equity > self.peak:
            self.peak = equity
            self.level, self.applied = 0, 0
        dd = 1 - equity / self.peak if self.peak > 0 else 0.0
        lvl = 3 if dd >= cfg.dd3 else (2 if dd >= cfg.dd2 else (1 if dd >= cfg.dd1 else 0))
        self.level = lvl
        fresh = lvl if lvl > self.applied else 0
        if fresh:
            self.applied = lvl
            if lvl == 3:
                self.frozen_until = day_no + cfg.freeze_days
        return lvl, fresh, dd

    def entries_allowed(self, day_no: int) -> bool:
        return self.level == 0 and day_no > self.frozen_until

    def risk_cap(self, cfg) -> float:
        return cfg.risk_dd2 if self.level >= 2 else cfg.max_total_risk
