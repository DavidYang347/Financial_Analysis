"""Run parameter variants of the high-odds strategy and log their metrics.

    python -m oddsbook.experiment --name base
    python -m oddsbook.experiment --name gates --set max_checklist_fail=2 score_min=65
    python -m oddsbook.experiment --list             # results so far, best first

Each run goes through the normal backtest engine (same rules as the web page)
but is not added to the backtest history. Results are appended to
``data/lake/oddsbook_experiments.jsonl`` with metrics for the whole period and
for each sub-period (default split: 2025 = tuning, 2026 = holdout), so a
change can be judged on the part of the data it was not tuned on.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, datetime

import numpy as np
import pandas as pd

from data import config

LOG = config.LAKE_DIR / "oddsbook_experiments.jsonl"
RUNS = config.LAKE_DIR / "oddsbook_experiments"
SPLITS = [("2025", "2025-01-01", "2025-12-31"), ("2026", "2026-01-01", "2026-12-31")]


def _coerce(v: str):
    for f in (int, float):
        try:
            return f(v)
        except ValueError:
            pass
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    return v


def segment(eq: pd.DataFrame, a: str, b: str) -> dict:
    e = eq[(eq["date"] >= pd.Timestamp(a)) & (eq["date"] <= pd.Timestamp(b))]
    if len(e) < 5:
        return {}
    prev = eq[eq["date"] < pd.Timestamp(a)]
    base = prev["equity"].iloc[-1] if len(prev) else e["equity"].iloc[0]
    nav = e["equity"] / base
    r = nav.pct_change().fillna(nav.iloc[0] - 1)
    out = {"ret": float(nav.iloc[-1] - 1), "mdd": float((nav / nav.cummax() - 1).min()),
           "sharpe": float(r.mean() / r.std() * math.sqrt(244)) if r.std() > 0 else None,
           "expo": float((e["market_value"] / e["equity"]).mean())}
    if "benchmark" in e:
        pb = prev["benchmark"].iloc[-1] if len(prev) else e["benchmark"].iloc[0]
        out["bench"] = float(e["benchmark"].iloc[-1] / pb - 1)
    return out


def run(overrides: dict, start: str = "2025-01-02", end: str | None = None, name: str = "") -> dict:
    from data.query import MarketData
    from screening.params import resolve_params
    from strategy import metrics
    from strategy.registry import registry
    from strategy.runner import build_settings
    from strategy.engine import Engine
    from oddsbook import journal

    md = _SHARED.get("md") or _SHARED.setdefault("md", MarketData())
    s = registry().get("high_odds_v3")
    if not s.ok:
        raise RuntimeError(s.error)
    params = resolve_params(s.params, overrides)
    settings = build_settings(s, {"start": start, "end": end}, md)
    t0 = time.time()
    res = Engine(s.module.rebalance, params, settings, md, finalize=s.module.finalize).run()
    m = metrics.compute(res.equity, res.trades, settings.initial_cash, settings.risk_free)
    eq = res.equity.copy()
    eq["date"] = pd.to_datetime(eq["date"])
    stats = res.extras.get("stats", pd.DataFrame())
    allrow = stats[stats["group"] == "all"].iloc[0].to_dict() if len(stats) else {}
    trips = res.extras.get("trips", pd.DataFrame())
    rec = {
        "time": datetime.now().isoformat(timespec="seconds"), "name": name, "overrides": overrides,
        "start": str(settings.start), "end": str(settings.end), "elapsed": round(time.time() - t0, 1),
        "ret": m.get("total_return"), "cagr": m.get("annual_return"), "mdd": m.get("max_drawdown"),
        "sharpe": m.get("sharpe"), "calmar": m.get("calmar"), "bench": m.get("benchmark_return"),
        "expo": float((eq["market_value"] / eq["equity"]).mean()),
        "entries": int(len(trips)), "closed": allrow.get("trades"), "win_rate": allrow.get("win_rate"),
        "payoff": allrow.get("payoff"), "expectancy": allrow.get("expectancy"),
        "segments": {k: segment(eq, a, b) for k, a, b in SPLITS},
    }
    if len(stats):
        rec["by_theme"] = {r["name"]: [int(r["trades"]), round(r["expectancy"], 3), round(r["pnl"])]
                           for r in stats[stats["group"] == "theme"].to_dict("records")}
    if name:  # keep the decision tables of each variant for diagnosis
        d = RUNS / name
        d.mkdir(parents=True, exist_ok=True)
        for k in ("trips", "journal", "cards", "pools", "audits"):
            if k in res.extras:
                res.extras[k].to_parquet(d / f"{k}.parquet", index=False)
        res.equity.to_parquet(d / "equity.parquet", index=False)
        res.trades.to_parquet(d / "trades.parquet", index=False)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    rec["_res"] = res
    return rec


_SHARED: dict = {}


def batch(variants: list[tuple[str, dict]], budget: float = 150, **kw) -> list[dict]:
    """Run variants not yet in the log (by name) until ``budget`` seconds are used. Resumable."""
    done = set()
    if LOG.exists():
        done = {json.loads(x)["name"] for x in LOG.read_text(encoding="utf-8").splitlines() if x.strip()}
    t0 = time.time()
    out = []
    for name, ov in variants:
        if name in done:
            continue
        if time.time() - t0 > budget:
            break
        rec = run(ov, name=name, **kw)
        rec.pop("_res")
        out.append(rec)
        seg = rec["segments"]
        print(f"{name:28s} ret {rec['ret']:+.3f} mdd {rec['mdd']:+.3f} sh {rec['sharpe']:.2f} expo {rec['expo']:.2f} "
              f"n {rec['entries']:3d} | 25: {seg['2025'].get('ret', 0):+.3f} sh {seg['2025'].get('sharpe') or 0:.2f} "
              f"| 26: {seg['2026'].get('ret', 0):+.3f} sh {seg['2026'].get('sharpe') or 0:.2f} "
              f"dd {seg['2026'].get('mdd', 0):+.3f}  ({rec['elapsed']:.0f}s)", flush=True)
    left = [n for n, _ in variants if n not in done and n not in {r["name"] for r in out}]
    print(f"-- {len(out)} run, {len(left)} left")
    return out


def show(limit: int = 40, sort: str = "sharpe") -> None:
    if not LOG.exists():
        print("no experiments yet")
        return
    rows = [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if x.strip()]
    df = pd.DataFrame([{
        "name": r["name"], "ret": r["ret"], "mdd": r["mdd"], "sharpe": r["sharpe"], "expo": r["expo"],
        "entries": r["entries"], "win": r.get("win_rate"), "payoff": r.get("payoff"),
        "r25": r["segments"].get("2025", {}).get("ret"), "s25": r["segments"].get("2025", {}).get("sharpe"),
        "r26": r["segments"].get("2026", {}).get("ret"), "s26": r["segments"].get("2026", {}).get("sharpe"),
        "dd26": r["segments"].get("2026", {}).get("mdd"),
        "set": " ".join(f"{k}={v}" for k, v in r["overrides"].items())} for r in rows])
    df = df.sort_values(sort, ascending=False).head(limit)
    with pd.option_context("display.width", 250, "display.max_colwidth", 90, "display.max_rows", 200):
        print(df.round(3).to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m oddsbook.experiment")
    ap.add_argument("--name", default="")
    ap.add_argument("--set", nargs="*", default=[], help="参数覆盖，如 score_min=65 max_total_risk=0.12")
    ap.add_argument("--start", default="2025-01-02")
    ap.add_argument("--end", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sort", default="sharpe")
    a = ap.parse_args()
    if a.list:
        show(sort=a.sort)
        return
    ov = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        ov[k] = _coerce(v)
    rec = run(ov, a.start, a.end, a.name)
    rec.pop("_res")
    seg = rec["segments"]
    print(json.dumps({"name": a.name, "ret": rec["ret"], "mdd": rec["mdd"], "sharpe": rec["sharpe"],
                      "expo": round(rec["expo"], 3), "entries": rec["entries"], "win": rec["win_rate"],
                      "payoff": rec["payoff"], "2025": seg.get("2025"), "2026": seg.get("2026"),
                      "elapsed": rec["elapsed"]}, ensure_ascii=False, default=lambda x: round(x, 4)))


if __name__ == "__main__":
    main()
