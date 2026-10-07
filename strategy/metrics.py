"""Performance statistics for a backtest report."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 244  # A-share trading days per year (approx.)


def _f(x) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, 6) if math.isfinite(v) else None


def drawdown(nav: pd.Series) -> pd.Series:
    return nav / nav.cummax() - 1


def max_drawdown_span(nav: pd.Series) -> tuple:
    """(max drawdown, peak date, trough date, recovery date or None)."""
    dd = drawdown(nav)
    if dd.empty or dd.min() >= 0:
        return 0.0, None, None, None
    trough = dd.idxmin()
    peak = nav.loc[:trough].idxmax()
    after = nav.loc[trough:]
    rec = after[after >= nav.loc[peak]]
    return float(dd.min()), peak, trough, (rec.index[0] if len(rec) else None)


def round_trips(trades: pd.DataFrame) -> pd.DataFrame:
    """Realized P&L per sell fill (each sell closes part of a position)."""
    if trades.empty:
        return trades
    t = trades[(trades["status"] == "filled") & (trades["side"] == "sell")]
    return t[t["pnl"].notna()]


def compute(equity: pd.DataFrame, trades: pd.DataFrame, initial_cash: float, risk_free: float = 0.0) -> dict:
    eq = equity.set_index("date")["equity"].astype(float)
    nav = eq / float(initial_cash)
    ret = nav.pct_change().fillna(nav.iloc[0] - 1 if len(nav) else 0.0)
    n = len(nav)
    years = n / TRADING_DAYS if n else 0
    total = float(nav.iloc[-1] - 1) if n else 0.0
    cagr = (nav.iloc[-1]) ** (1 / years) - 1 if years > 0 and nav.iloc[-1] > 0 else None
    vol = ret.std(ddof=1) * math.sqrt(TRADING_DAYS) if n > 2 else None
    rf_d = risk_free / TRADING_DAYS
    ex = ret - rf_d
    sharpe = ex.mean() / ret.std(ddof=1) * math.sqrt(TRADING_DAYS) if n > 2 and ret.std(ddof=1) > 0 else None
    downside = ret[ret < rf_d] - rf_d
    dstd = math.sqrt((downside ** 2).sum() / max(n - 1, 1)) if n > 2 else 0
    sortino = ex.mean() / dstd * math.sqrt(TRADING_DAYS) if dstd > 0 else None
    mdd, peak, trough, rec = max_drawdown_span(nav)
    calmar = cagr / abs(mdd) if cagr is not None and mdd < 0 else None

    out = {
        "start": str(nav.index[0]) if n else None, "end": str(nav.index[-1]) if n else None,
        "trading_days": n, "final_equity": _f(eq.iloc[-1]) if n else None,
        "total_return": _f(total), "annual_return": _f(cagr), "annual_volatility": _f(vol),
        "sharpe": _f(sharpe), "sortino": _f(sortino), "calmar": _f(calmar),
        "max_drawdown": _f(mdd), "max_dd_peak": str(peak) if peak else None,
        "max_dd_trough": str(trough) if trough else None, "max_dd_recovery": str(rec) if rec else None,
        "win_days": _f((ret > 0).sum() / n) if n else None,
        "best_day": _f(ret.max()) if n else None, "worst_day": _f(ret.min()) if n else None,
        "avg_positions": _f(equity["positions"].mean()) if n else None,
        "avg_exposure": _f((equity["market_value"] / equity["equity"]).mean()) if n else None,
    }

    if "benchmark" in equity.columns:
        b = equity.set_index("date")["benchmark"].astype(float) / float(initial_cash)
        bret = b.pct_change().fillna(b.iloc[0] - 1)
        btotal = float(b.iloc[-1] - 1)
        bcagr = b.iloc[-1] ** (1 / years) - 1 if years > 0 and b.iloc[-1] > 0 else None
        active = ret - bret
        te = active.std(ddof=1) * math.sqrt(TRADING_DAYS) if n > 2 else None
        cov = np.cov(ret, bret, ddof=1) if n > 2 else None
        beta = cov[0, 1] / cov[1, 1] if cov is not None and cov[1, 1] > 0 else None
        alpha = None
        if beta is not None and cagr is not None and bcagr is not None:
            alpha = (cagr - risk_free) - beta * (bcagr - risk_free)
        out.update({
            "benchmark_return": _f(btotal), "benchmark_annual_return": _f(bcagr),
            "excess_return": _f(total - btotal),
            "benchmark_max_drawdown": _f(drawdown(b).min()),
            "alpha": _f(alpha), "beta": _f(beta), "tracking_error": _f(te),
            "information_ratio": _f(active.mean() / active.std(ddof=1) * math.sqrt(TRADING_DAYS))
            if n > 2 and active.std(ddof=1) > 0 else None,
            "win_days_vs_benchmark": _f((active > 0).sum() / n) if n else None,
        })

    filled = trades[trades["status"] == "filled"] if not trades.empty else trades
    rt = round_trips(trades)
    wins = rt[rt["pnl"] > 0] if not rt.empty else rt
    losses = rt[rt["pnl"] <= 0] if not rt.empty else rt
    turnover = filled["amount"].sum() / 2 / eq.mean() / years if years > 0 and len(filled) else 0.0
    out.update({
        "trades": int(len(filled)), "buys": int((filled["side"] == "buy").sum()) if len(filled) else 0,
        "sells": int((filled["side"] == "sell").sum()) if len(filled) else 0,
        "blocked_orders": int((trades["status"] == "blocked").sum()) if not trades.empty else 0,
        "win_rate": _f(len(wins) / len(rt)) if len(rt) else None,
        "profit_factor": _f(wins["pnl"].sum() / -losses["pnl"].sum()) if len(losses) and losses["pnl"].sum() < 0 else None,
        "avg_win": _f(wins["pnl"].mean()) if len(wins) else None,
        "avg_loss": _f(losses["pnl"].mean()) if len(losses) else None,
        "total_fees": _f(filled["commission"].sum() + filled["tax"].sum()) if len(filled) else 0.0,
        "annual_turnover": _f(turnover),
    })
    return out


def monthly_returns(equity: pd.DataFrame, initial_cash: float) -> list[dict]:
    """[{year, month, ret}] plus month=0 rows for whole-year returns."""
    eq = equity.set_index(pd.to_datetime(equity["date"]))["equity"].astype(float)
    if eq.empty:
        return []
    start = pd.Series([float(initial_cash)], index=[eq.index[0] - pd.Timedelta(days=1)])
    s = pd.concat([start, eq])
    m = s.resample("ME").last().dropna()
    mret = m.pct_change().dropna()
    y = s.resample("YE").last().dropna()
    yret = y.pct_change().dropna()
    rows = [{"year": d.year, "month": d.month, "ret": _f(r)} for d, r in mret.items()]
    rows += [{"year": d.year, "month": 0, "ret": _f(r)} for d, r in yret.items()]
    return rows
