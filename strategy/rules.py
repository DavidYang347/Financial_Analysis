"""A-share trading rules used by the backtest engine.

Covers board lot sizes, daily price limits, and fees. Rules are simplified
where noted; see strategy/strategies/README.md for the full list.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

# ChiNext moved from 10% to 20% limits with the registration reform.
CHINEXT_20PCT_FROM = date(2020, 8, 24)
# Main-board ST / *ST limit moved from 5% to 10% (沪深交易所新版《交易规则》).
MAIN_ST_10PCT_FROM = date(2026, 7, 6)
# Price limits started on 1996-12-16; before that trading was unlimited.
LIMITS_FROM = date(1996, 12, 16)
# Bars a new listing needs before price limits are applied (IPO days have
# no or wider limits; skipping them avoids blocking trades that were legal).
NEW_LISTING_FREE_DAYS = 5


def board_of(symbol: str) -> str:
    code, ex = symbol.split(".")
    if ex == "BJ":
        return "北交所"
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301", "302")):
        return "创业板"
    return "主板"


def limit_pct(symbol: str, day: date, is_st: bool = False) -> float:
    """Daily price limit as a fraction of the reference price (inf = no limit)."""
    if day < LIMITS_FROM:
        return math.inf
    board = board_of(symbol)
    if board == "北交所":
        return 0.30
    if board == "科创板":
        return 0.20
    if board == "创业板":
        return 0.20 if day >= CHINEXT_20PCT_FROM else (0.05 if is_st else 0.10)
    return 0.05 if is_st and day < MAIN_ST_10PCT_FROM else 0.10


def limit_prices(ref: float, pct: float) -> tuple[float, float]:
    """(limit-up, limit-down), rounded half-up to 0.01 like the exchanges do."""
    up = math.floor(ref * (1 + pct) * 100 + 0.5) / 100
    down = math.floor(ref * (1 - pct) * 100 + 0.5) / 100
    return up, down


def min_buy_shares(symbol: str) -> int:
    """STAR market orders start at 200 shares; everything else at 100."""
    return 200 if board_of(symbol) == "科创板" else 100


def round_buy(symbol: str, shares: float) -> int:
    """Largest valid buy quantity not above ``shares``.

    Main board / ChiNext: multiples of 100. STAR: >= 200, then 1-share steps.
    BSE: >= 100, then 1-share steps.
    """
    board = board_of(symbol)
    if board in ("主板", "创业板"):
        return int(shares // 100) * 100
    q = int(math.floor(shares + 1e-9))
    return q if q >= min_buy_shares(symbol) else 0


def round_sell(symbol: str, shares: float, held: float) -> float:
    """Valid sell quantity for a partial sell of ``shares`` out of ``held``.

    Partial sells use the buy step (100 shares on main board / ChiNext). If
    what would remain is below one lot, the whole position is sold (odd lots
    may only be sold in one go).
    """
    if shares >= held - 1e-9:
        return held
    board = board_of(symbol)
    q = float(int(shares // 100) * 100) if board in ("主板", "创业板") else float(math.floor(shares + 1e-9))
    if q <= 0:
        return 0.0
    min_left = 200 if board == "科创板" else 100
    if held - q < min_left:
        return held
    return q


@dataclass
class Fees:
    commission: float = 0.00025   # both sides
    min_commission: float = 5.0   # yuan per order
    stamp_tax: float = 0.0005     # sell side only
    slippage: float = 0.0005      # fraction of price, against you

    def commission_for(self, amount: float) -> float:
        if amount <= 0:
            return 0.0
        return round(max(amount * self.commission, self.min_commission), 2)

    def tax_for(self, amount: float) -> float:
        return round(amount * self.stamp_tax, 2)
