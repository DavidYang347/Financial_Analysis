"""Observation tables exported after a backtest (saved as x_<name>.parquet in the run folder).

    pools     weekly pool sizes (radar / watch / ammo / holdings), signals, vetoes, breaker level
    signals   every channel signal seen in the weekly scans
    cards     every odds card written by deep research (with score, checklist, gates)
    journal   every decision with its reason (进观察池, 进弹药池, 建仓, 加仓, 证伪减仓, 止盈 ...)
    audits    monthly audit rows of holdings (current odds, risk contribution, action)
    ammo      the ammo pool at the end of the backtest (what would be bought next)
    trips     round trips: entry -> exit per symbol, with channel / theme / exit reason (§18 统计)
    stats     per channel and per theme: trades, win rate, avg win / loss, expectancy (§18.3)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ENTRY_ACTIONS = ("建仓（试错仓）",)


def _frame(rows, cols=None) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if cols is not None:
        for c in cols:
            if c not in df:
                df[c] = None
        df = df[cols]
    for c in df.columns:
        if df[c].dtype == object:
            sample = df[c].dropna().head(20)
            if len(sample) and all(isinstance(v, (set, list, dict)) for v in sample):
                df[c] = df[c].map(lambda v: "、".join(sorted(map(str, v))) if isinstance(v, (set, list)) else str(v))
    return df


def round_trips(trades: pd.DataFrame, journal: pd.DataFrame) -> pd.DataFrame:
    """Pair buys and sells per symbol into round trips (a trip ends when the position is flat)."""
    if trades is None or trades.empty:
        return pd.DataFrame()
    tr = trades[trades["status"] == "filled"].sort_values(["date"]).copy()
    tr["date"] = pd.to_datetime(tr["date"])
    j = journal.copy()
    rows = []
    for sym, g in tr.groupby("symbol"):
        pos, cost, proceeds, start, name = 0.0, 0.0, 0.0, None, g["name"].iloc[0]
        for r in g.itertuples():
            if r.side == "buy":
                if pos <= 1e-6:
                    start, cost, proceeds = r.date, 0.0, 0.0
                pos += r.shares
                cost += r.amount + r.commission
            else:
                pos -= r.shares
                proceeds += r.amount - r.commission - r.tax
            if pos <= 1e-6 and start is not None:
                rows.append(_trip(sym, name, start, r.date, cost, proceeds, j))
                start = None
        if start is not None and pos > 1e-6:
            rows.append(_trip(sym, name, start, None, cost, proceeds, j, open_shares=pos))
    return pd.DataFrame(rows)


def _trip(sym, name, start, end, cost, proceeds, j, open_shares=0.0) -> dict:
    jj = j[j["symbol"] == sym]
    entry = jj[(jj["action"].isin(ENTRY_ACTIONS)) & (jj["date"] < start)].tail(1)
    after = jj[(jj["date"] >= start) & ((jj["date"] <= end) if end is not None else True)]
    exits = after[~after["action"].isin(ENTRY_ACTIONS) & ~after["action"].str.startswith("加仓")]
    out = {"symbol": sym, "name": name, "entry_date": start, "exit_date": end, "cost": cost, "proceeds": proceeds,
           "pnl": proceeds - cost if end is not None else np.nan,
           "return": proceeds / cost - 1 if end is not None and cost > 0 else np.nan,
           "days": (end - start).days if end is not None else np.nan, "open": end is None,
           "theme": entry["theme"].iloc[0] if len(entry) else None,
           "channels": entry["channels"].iloc[0] if len(entry) else None,
           "score": entry["score"].iloc[0] if len(entry) else None,
           "odds_at_entry": entry["odds"].iloc[0] if len(entry) else None,
           "adds": int(after["action"].str.startswith("加仓").sum()),
           "exit_reason": "；".join(exits["action"].tolist()[-3:]) if len(exits) else ("持有中" if end is None else "")}
    return out


def channel_stats(trips: pd.DataFrame) -> pd.DataFrame:
    """§18.3 per channel letter and per theme (closed trips only)."""
    if trips.empty:
        return pd.DataFrame()
    t = trips[~trips["open"]].copy()
    if t.empty:
        return pd.DataFrame()
    rows = []

    def agg(label, kind, g):
        win = g[g["pnl"] > 0]
        loss = g[g["pnl"] <= 0]
        aw = win["return"].mean() if len(win) else 0.0
        al = -loss["return"].mean() if len(loss) else 0.0
        top = g.sort_values("pnl", ascending=False)
        n10 = max(1, int(round(len(g) * 0.1)))
        prof = g.loc[g["pnl"] > 0, "pnl"].sum()
        rows.append({"group": kind, "name": label, "trades": len(g), "win_rate": len(win) / len(g),
                     "avg_win": aw, "avg_loss": al, "payoff": aw / al if al > 0 else np.nan,
                     "expectancy": g["return"].mean(), "pnl": g["pnl"].sum(),
                     "right_tail": top["pnl"].head(n10).clip(lower=0).sum() / prof if prof > 0 else np.nan,
                     "days_win": win["days"].mean() if len(win) else np.nan,
                     "days_loss": loss["days"].mean() if len(loss) else np.nan})

    agg("全部", "all", t)
    for ch in "ABCDEF":
        g = t[t["channels"].fillna("").str.contains(ch)]
        if len(g):
            agg(ch, "channel", g)
    for th, g in t.groupby(t["theme"].fillna("?")):
        agg(th, "theme", g)
    return pd.DataFrame(rows)


def export(book, ctx) -> dict[str, pd.DataFrame]:
    journal = _frame(book.journal, ["date", "symbol", "action", "reason", "theme", "channels", "score", "odds",
                                    "target_h", "falsify_h", "buy_line_h", "weight"])
    names = ctx.stocks().set_index("symbol")["name"]
    journal.insert(2, "name", journal["symbol"].map(names))
    out = {
        "pools": _frame(book.pool_log),
        "signals": _frame(book.signals_log),
        "cards": _frame(book.cards),
        "journal": journal,
        "audits": _frame(book.audits),
        "ammo": _frame([{**{k: v for k, v in a.items() if not isinstance(v, (set, dict))}, "symbol": s}
                        for s, a in book.ammo.items()]),
    }
    return out
