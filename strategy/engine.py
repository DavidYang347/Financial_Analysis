"""Daily backtest engine for target-weight strategies.

Each trading day:

1. Apply corporate actions to holdings (share count follows the hfq factor).
2. If orders are pending from the previous signal (fill="next_open"), fill
   them at today's open; orders blocked yesterday are retried.
3. Mark holdings to today's close and record equity.
4. On a rebalance day, call ``rebalance(ctx, params)`` with data up to today's
   close. With fill="close" the new targets are filled immediately at the
   close; otherwise they are filled at the next day's open.

A-share rules: T+1 (shares bought today cannot be sold today), board lots,
no buying at limit-up / selling at limit-down, no trading while suspended,
commission with a minimum per order, stamp tax on sells, and slippage.
Delisted holdings are liquidated at their last close.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from strategy import rules
from strategy.context import StrategyContext
from strategy.data import MarketHistory
from strategy.portfolio import Portfolio, Position

REBALANCE_CHOICES = ("daily", "weekly", "monthly", "every_n")
FILL_CHOICES = ("next_open", "close")


class BacktestError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


@dataclass
class BacktestSettings:
    start: date
    end: date
    initial_cash: float = 1_000_000.0
    rebalance: str = "monthly"        # daily | weekly | monthly | every_n
    every_n: int = 5                  # trading days between rebalances when rebalance=every_n
    fill: str = "next_open"           # next_open | close
    commission: float = 0.00025
    min_commission: float = 5.0
    stamp_tax: float = 0.0005
    slippage: float = 0.0005
    tolerance: float = 0.01           # skip adjustments smaller than this share of equity
    benchmark: str = "equal"          # equal (全市场等权) | none | a symbol like 600000.SH
    risk_free: float = 0.0            # annual, for Sharpe / Sortino
    lookback: int = 120               # bars of history the strategy needs before ``start``

    def validate(self) -> None:
        if self.start > self.end:
            raise BacktestError("开始日期不能晚于结束日期")
        if self.initial_cash < 10_000:
            raise BacktestError("初始资金至少 1 万元")
        if self.rebalance not in REBALANCE_CHOICES:
            raise BacktestError(f"调仓频率只能是 {REBALANCE_CHOICES}")
        if self.fill not in FILL_CHOICES:
            raise BacktestError(f"成交方式只能是 {FILL_CHOICES}")
        if not 1 <= self.every_n <= 250:
            raise BacktestError("调仓间隔需要在 1~250 个交易日之间")
        for k in ("commission", "stamp_tax", "slippage", "tolerance"):
            v = getattr(self, k)
            if not 0 <= v < 0.1:
                raise BacktestError(f"{k} 需要在 0~10% 之间")
        if not 0 <= self.min_commission <= 100:
            raise BacktestError("最低佣金需要在 0~100 元之间")
        if not 0 <= self.lookback <= 2000:
            raise BacktestError("历史窗口需要在 0~2000 之间")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"], d["end"] = self.start.isoformat(), self.end.isoformat()
        return d


def rebalance_days(days: list[date], how: str, every_n: int = 5) -> set[date]:
    """Signal days: every day, the last trading day of each week/month, or every n-th day.

    The first day of the range is always a signal day, so the portfolio is
    built right away instead of sitting in cash until the first period end.
    """
    if how == "daily":
        return set(days)
    if how == "every_n":
        return set(days[::every_n])
    out = {days[0]} if days else set()
    for a, b in zip(days, days[1:] + [None]):
        if b is None:
            out.add(a)
        elif how == "weekly" and a.isocalendar()[:2] != b.isocalendar()[:2]:
            out.add(a)
        elif how == "monthly" and (a.year, a.month) != (b.year, b.month):
            out.add(a)
    return out


@dataclass
class Order:
    symbol: str
    target: float  # target weight


@dataclass
class EngineResult:
    settings: dict
    equity: pd.DataFrame
    trades: pd.DataFrame
    holdings: pd.DataFrame
    logs: list[str] = field(default_factory=list)
    signals: int = 0
    extras: dict = field(default_factory=dict)  # name -> DataFrame from the strategy's finalize()


TRADE_COLUMNS = ["date", "symbol", "name", "side", "shares", "price", "amount", "commission", "tax",
                 "pnl", "status", "reason"]


class Engine:
    def __init__(self, strategy_fn: Callable, params: dict, settings: BacktestSettings, md,
                 progress: Callable[[int, int], None] | None = None,
                 cancelled: Callable[[], bool] | None = None,
                 finalize: Callable | None = None) -> None:
        settings.validate()
        self.fn = strategy_fn
        self.finalize = finalize
        self.params = params
        self.s = settings
        self.md = md
        self.progress = progress or (lambda done, total: None)
        self.cancelled = cancelled or (lambda: False)
        self.fees = rules.Fees(settings.commission, settings.min_commission, settings.stamp_tax, settings.slippage)

    # ---- helpers -------------------------------------------------------------
    def _limits(self, sym: str, row, d: date) -> tuple[float, float]:
        """(limit-up, limit-down) for ``row``; infinite when no limit applies."""
        ref = row["prev_close"]
        if not np.isfinite(ref) or ref <= 0 or row["bar_no"] < rules.NEW_LISTING_FREE_DAYS:
            return math.inf, -math.inf
        pct = rules.limit_pct(sym, d, self.hist.st.is_st(sym, d))
        if not math.isfinite(pct):
            return math.inf, -math.inf
        return rules.limit_prices(ref, pct)

    def _one_price_board(self, sym: str, row) -> int:
        """+1 for a one-price limit-up day (一字涨停), -1 for limit-down, else 0.

        Safety net for stocks without dated ST history (their past ST periods
        are unknown, so a one-price +5% day may have been an ST limit move).
        Stocks with history rely on the exact limit price instead.
        """
        if self.hist.st.is_covered(sym):
            return 0
        ref = row["prev_close"]
        if row["high"] - row["low"] > 1e-6 or not np.isfinite(ref) or ref <= 0 or row["bar_no"] < rules.NEW_LISTING_FREE_DAYS:
            return 0
        r = row["close"] / ref - 1
        return 1 if r > 0.045 else (-1 if r < -0.045 else 0)

    def _trade(self, d, sym, side, shares, price, amount, commission, tax, pnl, status, reason=""):
        if status == "blocked":
            # A blocked order is retried every day; record it once per signal, not once per day.
            key = (sym, side, reason)
            if key in self._blocked_seen:
                return
            self._blocked_seen.add(key)
        self.trades.append({
            "date": d, "symbol": sym, "name": self.hist.names.get(sym, ""), "side": side,
            "shares": round(shares, 2), "price": round(price, 4), "amount": round(amount, 2),
            "commission": commission, "tax": tax, "pnl": None if pnl is None else round(pnl, 2),
            "status": status, "reason": reason,
        })

    # ---- order execution -----------------------------------------------------
    def _execute(self, i: int, targets: dict[str, float], at: str, only: set[str] | None = None) -> set[str]:
        """Move the portfolio towards ``targets`` at today's open or close. Returns symbols that were blocked."""
        d = self.hist.all_days[i]
        day = self.hist.day(i)
        pf = self.pf
        price_col = "open" if at == "open" else "close"

        def px(sym: str) -> float | None:
            if sym in day.index:
                return float(day.at[sym, price_col])
            return None

        # Equity at the fill price (last close for symbols without a bar today).
        eq = pf.cash + sum(p.shares * (px(s) or p.price) for s, p in pf.positions.items())
        blocked: set[str] = set()
        syms = set(targets) | set(pf.positions)
        if only is not None:
            syms &= only

        sells, buys = [], []
        for sym in sorted(syms):
            w = targets.get(sym, 0.0)
            pos = pf.positions.get(sym)
            p = px(sym)
            cur_val = pos.shares * (p or pos.price) if pos else 0.0
            tgt_val = w * eq
            delta = tgt_val - cur_val
            closing = pos is not None and w <= 0
            if not closing and abs(delta) < self.s.tolerance * eq:
                continue
            (sells if delta < 0 else buys).append((sym, delta, tgt_val))

        # Sells first, so their cash can fund the buys.
        for sym, delta, tgt_val in sorted(sells, key=lambda x: (x[1], x[0])):
            pos = pf.positions[sym]
            p = px(sym)
            if p is None:
                self._trade(d, sym, "sell", 0, pos.price, 0, 0, 0, None, "blocked", "停牌")
                blocked.add(sym)
                continue
            if pos.last_buy >= d:
                self._trade(d, sym, "sell", 0, p, 0, 0, 0, None, "blocked", "T+1，当日买入不能卖出")
                blocked.add(sym)
                continue
            row = day.loc[sym]
            up, down = self._limits(sym, row, d)
            if p <= down + 1e-6 or self._one_price_board(sym, row) < 0:
                self._trade(d, sym, "sell", 0, p, 0, 0, 0, None, "blocked", "跌停，卖不出")
                blocked.add(sym)
                continue
            fill = max(p * (1 - self.fees.slippage), down if math.isfinite(down) else 0.0)
            want = pos.shares if tgt_val <= 0 else -delta / fill
            q = rules.round_sell(sym, want, pos.shares)
            if q <= 0:
                continue
            amount = q * fill
            comm, tax = self.fees.commission_for(amount), self.fees.tax_for(amount)
            pnl = amount - comm - tax - pos.cost * q
            pf.cash += amount - comm - tax
            pos.shares -= q
            self._trade(d, sym, "sell", q, fill, amount, comm, tax, pnl, "filled")
            if pos.shares <= 1e-6:
                del pf.positions[sym]

        if buys:
            need = sum(delta for _, delta, _ in buys)
            scale = min(1.0, pf.cash / (need * (1 + self.fees.commission))) if need > 0 else 0.0
            for sym, delta, _ in sorted(buys, key=lambda x: (-x[1], x[0])):
                p = px(sym)
                if p is None:
                    self._trade(d, sym, "buy", 0, 0, 0, 0, 0, None, "blocked", "停牌或无行情")
                    blocked.add(sym)
                    continue
                row = day.loc[sym]
                up, down = self._limits(sym, row, d)
                if p >= up - 1e-6 or self._one_price_board(sym, row) > 0:
                    self._trade(d, sym, "buy", 0, p, 0, 0, 0, None, "blocked", "涨停，买不进")
                    blocked.add(sym)
                    continue
                fill = min(p * (1 + self.fees.slippage), up)
                budget = min(delta * scale, pf.cash)
                q = rules.round_buy(sym, budget / (fill * (1 + self.fees.commission)))
                step = 100 if rules.board_of(sym) in ("主板", "创业板") else 1
                while q > 0 and q * fill + self.fees.commission_for(q * fill) > pf.cash + 1e-6:
                    q -= step
                    if q < rules.min_buy_shares(sym):
                        q = 0
                if q <= 0:
                    continue
                amount = q * fill
                comm = self.fees.commission_for(amount)
                pf.cash -= amount + comm
                pos = pf.positions.get(sym)
                if pos is None:
                    pf.positions[sym] = Position(sym, q, (amount + comm) / q, d, d, float(row["close"]),
                                                 float(row["adj_factor"]), float(row["close"]))
                else:
                    pos.cost = (pos.cost * pos.shares + amount + comm) / (pos.shares + q)
                    pos.shares += q
                    pos.last_buy = d
                self._trade(d, sym, "buy", q, fill, amount, comm, 0.0, None, "filled")
        return blocked

    def _liquidate_delisted(self, i: int) -> None:
        d = self.hist.all_days[i]
        for sym in sorted(self.pf.positions):
            last = self.hist.last_date.get(sym)
            if sym in self.hist.delisted and last is not None and last < d:
                pos = self.pf.positions.pop(sym)
                amount = pos.shares * pos.price
                comm, tax = self.fees.commission_for(amount), self.fees.tax_for(amount)
                self.pf.cash += amount - comm - tax
                self._trade(d, sym, "sell", pos.shares, pos.price, amount, comm, tax,
                            amount - comm - tax - pos.cost * pos.shares, "filled", f"退市，按最后交易日 {last} 收盘价清算")

    def _apply_factors(self, day: pd.DataFrame) -> None:
        """Ex-rights adjustment at the open: scale shares by the factor step."""
        for s, p in self.pf.positions.items():
            if s in day.index:
                f = float(day.at[s, "adj_factor"])
                if p.factor > 0 and abs(f / p.factor - 1) > 1e-9:
                    k = f / p.factor
                    p.shares *= k
                    p.cost /= k
                    p.peak /= k
                p.factor = f

    # ---- main loop -----------------------------------------------------------
    def run(self) -> EngineResult:
        s = self.s
        self.hist = MarketHistory(self.md, s.start, s.end, warmup=s.lookback + 5)
        days = self.hist.days
        self.pf = Portfolio(cash=float(s.initial_cash))
        self.trades: list[dict] = []
        self._blocked_seen: set[tuple] = set()
        ctx = StrategyContext(self.hist, self.pf)
        signal_days = rebalance_days(days, s.rebalance, s.every_n)
        bench = self.hist.benchmark(s.benchmark)

        pending: dict[str, float] | None = None   # targets to fill at the next open
        retry: tuple[dict[str, float], set[str]] | None = None  # blocked part of the last targets
        eq_rows, hold_rows = [], []
        signals = 0
        t_last = 0.0
        first_i = self.hist.index[days[0]]

        for k, d in enumerate(days):
            if self.cancelled():
                raise Cancelled()
            i = first_i + k
            day = self.hist.day(i)
            self._apply_factors(day)
            self._liquidate_delisted(i)

            if pending is not None:
                blocked = self._execute(i, pending, "open")
                retry = (pending, blocked) if blocked else None
                pending = None
            elif retry is not None:
                tgt, syms = retry
                blocked = self._execute(i, tgt, "open" if s.fill == "next_open" else "close", only=syms)
                retry = (tgt, blocked) if blocked else None

            self.pf.mark(day)

            if d in signal_days and k < len(days) - (1 if s.fill == "next_open" else 0):
                ctx._set_day(i)
                out = self.fn(ctx, dict(self.params))
                targets = _clean_targets(out, self.pf)
                if targets is not None:
                    signals += 1
                    retry = None
                    self._blocked_seen = set()
                    if s.fill == "close":
                        blocked = self._execute(i, targets, "close")
                        retry = (targets, blocked) if blocked else None
                    else:
                        pending = targets

            eq = self.pf.equity()
            mv = self.pf.market_value()
            eq_rows.append({"date": d, "equity": eq, "cash": self.pf.cash, "market_value": mv,
                            "positions": len(self.pf.positions)})
            for p in self.pf.positions.values():
                hold_rows.append({"date": d, "symbol": p.symbol, "shares": round(p.shares, 2),
                                  "price": p.price, "value": round(p.shares * p.price, 2),
                                  "weight": p.shares * p.price / eq if eq > 0 else 0.0,
                                  "pnl_pct": (p.price / p.cost - 1) * 100 if p.cost > 0 else 0.0})
            now = time.time()
            if now - t_last > 0.5 or k == len(days) - 1:
                self.progress(k + 1, len(days))
                t_last = now

        equity = pd.DataFrame(eq_rows)
        if bench is not None:
            equity["benchmark"] = (1 + bench.to_numpy()).cumprod() * float(s.initial_cash)
        trades = pd.DataFrame(self.trades, columns=TRADE_COLUMNS)
        holdings = pd.DataFrame(hold_rows, columns=["date", "symbol", "shares", "price", "value", "weight", "pnl_pct"])
        extras = {}
        if self.finalize is not None:
            ctx.trades = trades  # finalize() may pair fills with its own decision log
            out = self.finalize(ctx, dict(self.params)) or {}
            extras = {str(k): v for k, v in out.items() if isinstance(v, pd.DataFrame)}
        return EngineResult(settings=s.to_dict(), equity=equity, trades=trades, holdings=holdings,
                            logs=ctx.logs, signals=signals, extras=extras)


def _clean_targets(out, pf: Portfolio) -> dict[str, float] | None:
    """Normalize what rebalance() returned. ``None`` = keep current holdings."""
    if out is None:
        return None
    if isinstance(out, pd.Series):
        out = out.to_dict()
    if not isinstance(out, dict):
        raise BacktestError("rebalance() 需要返回 {股票代码: 目标权重} 字典、pandas.Series 或 None")
    t: dict[str, float] = {}
    for k, v in out.items():
        try:
            w = float(v)
        except (TypeError, ValueError) as e:
            raise BacktestError(f"{k} 的权重 {v!r} 不是数字") from e
        if not np.isfinite(w):
            continue
        if w < 0:
            raise BacktestError(f"{k} 的权重为负数；回测只支持做多")
        if w > 0:
            t[str(k)] = w
    total = sum(t.values())
    if total > 1 + 1e-6:  # more than fully invested: scale down instead of borrowing
        t = {k: v / total for k, v in t.items()}
    return t
