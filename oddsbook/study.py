"""Card study: write an odds card for every signalled stock each week and measure what happened next.

    python -m oddsbook.study build --budget 150     # resumable, one parquet per week
    python -m oddsbook.study report                  # forward returns by theme / channel / gate

The backtest only researches a few stocks a week, so its cards are a small,
selected sample. This study removes the capacity limit to measure which card
features (theme, anchor, odds, score, checklist, veto ...) predict forward
returns, which is what the gates should be calibrated on. Forward returns are
labels only: they are never fed back into a card.

Output: data/lake/oddsbook_study/<yyyymmdd>.parquet, then study.parquet with
forward hfq returns over 20 / 60 / 120 / 250 trading days, the maximum
drawdown inside 120 days, and whether the target or the falsify price was hit
first within 250 days.
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from data import config

OUT = config.LAKE_DIR / "oddsbook_study"


def build(budget: float | None = None, start="2025-01-03", end="2026-09-30", params: dict | None = None) -> dict:
    from data.query import MarketData
    from fundlab.store import FundStore
    from oddsbook import channels as chn
    from oddsbook import features as feat
    from oddsbook import odds as od
    from oddsbook import score as sc
    from oddsbook import veto as vt
    from oddsbook.config import PARAMS
    from screening.params import resolve_params

    t0 = time.time()
    p = resolve_params(PARAMS, params or {})
    md = MarketData()
    fs = FundStore()
    OUT.mkdir(parents=True, exist_ok=True)
    pb = feat.PriceBook.load(md, pd.Timestamp(start) - pd.Timedelta(days=400), md.latest_date())
    days = pb.dates[(pb.dates >= pd.Timestamp(start)) & (pb.dates <= pd.Timestamp(end))]
    weeks = pd.Series(days, index=days).groupby([days.isocalendar().year, days.isocalendar().week]).max()
    stocks = md.stocks()
    sth = md.st_history()
    events = fs.table("events").sort_values("ann_date")
    done = 0
    for t in weeks:
        f = OUT / f"{t:%Y%m%d}.parquet"
        if f.exists():
            continue
        if budget is not None and time.time() - t0 > budget:
            break
        snap = feat.snapshot(t, stocks, sth.on(t.date()), fs, pb)
        ev = events[(events["ann_date"] <= t) & (events["ann_date"] >= t - pd.DateOffset(years=3))]
        veto = vt.veto(snap, ev, t, p)
        turns = feat.industry_turns(fs, t)
        i = int(pb.dates.searchsorted(t, side="right")) - 1
        w0 = pb.dates[max(0, i - p["radar_event_days"] + 1)]
        sig = chn.scan(snap, fs, ev, t, w0, turns, p)
        m = (snap["bars"] >= 240) & snap["fin_period"].notna() & (snap["exchange"] != "BJ") & ~snap["is_financial"]
        sig = sig[sig["symbol"].isin(m[m].index)]
        summ = chn.summarize(sig)
        close_h = pb.upto(t, 400)["close_h"]
        rows = []
        for sym, s in summ.iterrows():
            row = snap.loc[sym]
            card = od.build_card(sym, row, s, close_h, t, p)
            card = sc.grade(card, row, veto.loc[sym], p)
            q = od.quick_odds(row, s, close_h, p)
            card.update(q)
            card["hard_veto"] = bool(veto.at[sym, "hard"])
            card["amount20"] = row["amount20"]
            card["ret20_before"] = row["ret20"]
            card["ret60_before"] = row["ret60"]
            card["pb_pct"] = row["pb_pct"]
            card["loss_years"] = row["loss_years"]
            rows.append(card)
        df = pd.DataFrame(rows)
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].map(lambda v: None if v is None else str(v) if not isinstance(v, (bool, np.bool_)) else bool(v))
        df.to_parquet(f, index=False)
        done += 1
    left = sum(1 for t in weeks if not (OUT / f"{t:%Y%m%d}.parquet").exists())
    return {"weeks_done": done, "weeks_left": left, "elapsed": round(time.time() - t0, 1)}


def label() -> pd.DataFrame:
    """Join all weekly card files with forward returns (labels)."""
    from data.query import MarketData
    from oddsbook import features as feat
    md = MarketData()
    files = sorted(OUT.glob("2*.parquet"))
    cards = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    cards["date"] = pd.to_datetime(cards["date"])
    pb = feat.PriceBook.load(md, cards["date"].min() - pd.Timedelta(days=5), md.latest_date())
    ch = pb.close_h.ffill()
    d = pb.dates
    pos = {s: i for i, s in enumerate(ch.columns)}
    arr = ch.to_numpy()
    out = {k: [] for k in ("f20", "f60", "f120", "f250", "mdd120", "hit", "hit_days", "entry_next")}
    for r in cards.itertuples():
        j = pos.get(r.symbol)
        i = int(d.searchsorted(r.date, side="right"))  # next trading day = entry (next open ~ next close here)
        if j is None or i >= len(d):
            for k in out:
                out[k].append(np.nan)
            continue
        path = arr[i:, j]
        p0 = arr[i - 1, j]
        out["entry_next"].append(path[0] / p0 - 1 if len(path) else np.nan)
        for n in (20, 60, 120, 250):
            out[f"f{n}"].append(path[n - 1] / p0 - 1 if len(path) >= n else np.nan)
        w = path[:120]
        out["mdd120"].append(np.nanmin(w / np.fmax.accumulate(np.r_[p0, w])[1:] - 1) if len(w) else np.nan)
        w = path[:250]
        up = np.where(w >= r.target_h)[0] if np.isfinite(r.target_h) else np.array([])
        dn = np.where(w <= r.falsify_h)[0] if np.isfinite(r.falsify_h) else np.array([])
        a = up[0] if len(up) else 10 ** 6
        b = dn[0] if len(dn) else 10 ** 6
        out["hit"].append("target" if a < b else "falsify" if b < a else "none")
        out["hit_days"].append(min(a, b) if min(a, b) < 10 ** 6 else np.nan)
    for k, v in out.items():
        cards[k] = v
    # Market (equal-weight) forward returns for excess return.
    mkt = ch.pct_change(fill_method=None).clip(-0.5, 1.0).mean(axis=1).fillna(0)
    mnav = (1 + mkt).cumprod()
    for n in (20, 60, 120, 250):
        fwd = mnav.shift(-n) / mnav - 1
        cards[f"m{n}"] = cards["date"].map(fwd)
        cards[f"x{n}"] = cards[f"f{n}"] - cards[f"m{n}"]
    cards.to_parquet(OUT / "study.parquet", index=False)
    return cards


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m oddsbook.study")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--budget", type=float, default=None)
    sub.add_parser("label")
    a = ap.parse_args()
    if a.cmd == "build":
        print(build(a.budget))
    else:
        df = label()
        print(len(df), "cards labelled")


if __name__ == "__main__":
    main()
