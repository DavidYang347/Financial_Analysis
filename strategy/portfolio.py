"""Cash and positions for one backtest.

Positions are held in shares on the raw (unadjusted) price basis. On an
ex-rights/ex-dividend day the share count is scaled by the change in the hfq
factor, which is the same as reinvesting the dividend and receiving the bonus
shares. Value therefore stays continuous across corporate actions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass
class Position:
    symbol: str
    shares: float
    cost: float              # average cost per current share (fees included)
    entry_date: date
    last_buy: date
    price: float             # last known raw close
    factor: float            # hfq factor of ``price``
    peak: float = 0.0        # highest close since entry, on the current share basis


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)

    def values(self) -> dict[str, float]:
        return {s: p.shares * p.price for s, p in self.positions.items()}

    def market_value(self) -> float:
        return sum(p.shares * p.price for p in self.positions.values())

    def equity(self) -> float:
        return self.cash + self.market_value()

    def mark(self, day: pd.DataFrame) -> None:
        """Update prices from one day's bars and apply corporate actions. Suspended stocks keep their last price."""
        if not self.positions:
            return
        for s, p in self.positions.items():
            if s not in day.index:
                continue
            row = day.loc[s]
            f = float(row["adj_factor"])
            if p.factor > 0 and abs(f / p.factor - 1) > 1e-9:
                k = f / p.factor
                p.shares *= k
                p.cost /= k
                p.peak /= k
            p.factor = f
            p.price = float(row["close"])
            p.peak = max(p.peak, p.price)

    def positions_frame(self, today: date) -> pd.DataFrame:
        cols = ["symbol", "shares", "price", "value", "weight", "cost", "pnl_pct", "entry_date", "days_held"]
        if not self.positions:
            return pd.DataFrame(columns=cols)
        eq = self.equity()
        rows = []
        for s, p in self.positions.items():
            v = p.shares * p.price
            rows.append({
                "symbol": s, "shares": p.shares, "price": p.price, "value": v,
                "weight": v / eq if eq > 0 else 0.0, "cost": p.cost,
                "pnl_pct": (p.price / p.cost - 1) * 100 if p.cost > 0 else 0.0,
                "entry_date": p.entry_date, "days_held": (today - p.entry_date).days,
            })
        return pd.DataFrame(rows, columns=cols)
