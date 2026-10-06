"""Adjustment-factor computation.

We store unadjusted prices plus a cumulative backward-adjustment (后复权, hfq)
factor. The factor is 1.0 before the first corporate action and steps up at
each ex-date, so historical values never change when new events occur.

    hfq_price(t) = raw_price(t) * F(t)
    qfq_price(t) = raw_price(t) * F(t) / F(latest)

For an ex-date with previous close P, per-share cash dividend D, bonus/
capitalization shares S, rights shares R at price K:

    ex_ref = (P - D + R*K) / (1 + S + R)
    step   = P / ex_ref
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

EVENT_COLUMNS = ["date", "cash", "bonus", "rights", "rights_price"]


def factors_from_events(bars: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Build hfq factor change points from unadjusted bars and corporate actions.

    ``bars`` needs columns date, close (unadjusted, any order). Returns a frame
    with columns date, adj_factor where date is the first trading day on or
    after each ex-date.
    """
    out_cols = ["date", "adj_factor"]
    if bars is None or bars.empty or events is None or events.empty:
        return pd.DataFrame(columns=out_cols)

    b = bars[["date", "close"]].copy()
    b["date"] = pd.to_datetime(b["date"])
    b = b.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)
    dates = b["date"].values
    closes = b["close"].to_numpy(dtype=float)

    ev = events.copy()
    ev["date"] = pd.to_datetime(ev["date"])
    for c in ("cash", "bonus", "rights", "rights_price"):
        ev[c] = pd.to_numeric(ev.get(c, 0.0), errors="coerce").fillna(0.0)
    # Merge events that fall on the same effective trading day.
    idx = np.searchsorted(dates, ev["date"].values, side="left")
    ev["bar_idx"] = idx
    ev = ev[(ev["bar_idx"] > 0) & (ev["bar_idx"] < len(dates))]
    if ev.empty:
        return pd.DataFrame(columns=out_cols)

    steps: list[tuple[pd.Timestamp, float]] = []
    for bar_idx, g in ev.groupby("bar_idx", sort=True):
        prev_close = closes[bar_idx - 1]
        cash = g["cash"].sum()
        bonus = g["bonus"].sum()
        rights = g["rights"].sum()
        # Weighted rights price if several rights issues share one day (rare).
        k = (g["rights"] * g["rights_price"]).sum() / rights if rights > 0 else 0.0
        if cash == 0 and bonus == 0 and rights == 0:
            continue
        ex_ref = (prev_close - cash + rights * k) / (1.0 + bonus + rights)
        if not np.isfinite(ex_ref) or ex_ref <= 0 or prev_close <= 0:
            continue
        step = prev_close / ex_ref
        if not np.isfinite(step) or step <= 0 or abs(step - 1.0) < 1e-9:
            continue
        steps.append((pd.Timestamp(dates[bar_idx]), step))

    if not steps:
        return pd.DataFrame(columns=out_cols)
    df = pd.DataFrame(steps, columns=["date", "step"])
    df["adj_factor"] = df["step"].cumprod()
    df["date"] = df["date"].dt.date
    return df[out_cols].reset_index(drop=True)


def normalize_factor_points(df: pd.DataFrame, bars: pd.DataFrame | None = None) -> pd.DataFrame:
    """Normalize a provider factor series (e.g. baostock/tushare) to change points with F=1 at the start."""
    out_cols = ["date", "adj_factor"]
    if df is None or df.empty:
        return pd.DataFrame(columns=out_cols)
    f = df[["date", "adj_factor"]].copy()
    f["date"] = pd.to_datetime(f["date"]).dt.date
    f = f.dropna().sort_values("date").drop_duplicates("date", keep="last")
    f = f[f["adj_factor"] > 0]
    if f.empty:
        return pd.DataFrame(columns=out_cols)
    # Rebase so the factor in effect on the first stored bar is 1.0, and drop
    # points on/before that bar (they are absorbed into the base).
    base = 1.0
    if bars is not None and not bars.empty:
        first_bar = pd.to_datetime(bars["date"]).min().date()
        before = f[f["date"] <= first_bar]
        if len(before):
            base = float(before["adj_factor"].iloc[-1])
            f = f[f["date"] > first_bar]
    f = f.assign(adj_factor=f["adj_factor"] / base)
    # Keep change points only (the implicit value before the first point is 1.0).
    prev = f["adj_factor"].shift(1).fillna(1.0)
    f = f[(f["adj_factor"] - prev).abs() > 1e-9]
    return f[out_cols].reset_index(drop=True)


def factor_on(points: pd.DataFrame, when: date) -> float:
    if points is None or points.empty:
        return 1.0
    p = points[pd.to_datetime(points["date"]).dt.date <= when]
    return float(p["adj_factor"].iloc[-1]) if len(p) else 1.0
