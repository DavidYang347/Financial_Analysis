"""Strategy registry, backtest engine rules and API (offline, temporary lake)."""
from __future__ import annotations

import textwrap
import time
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app import deps
from backend.app.main import app
from data.query import MarketData
from data.schema import normalize_daily
from data.store import Lake
from strategy import rules
from strategy.engine import BacktestSettings, Engine, rebalance_days
from strategy.registry import StrategyRegistry
from strategy.runner import BacktestStore, load_holdings, load_report, load_trades, run_backtest

DAYS = list(pd.bdate_range("2024-01-01", periods=30).date)


def _bars(sym, closes, opens=None, highs=None, lows=None, days=DAYS):
    opens = opens or closes
    return normalize_daily(pd.DataFrame({
        "date": days[:len(closes)], "open": opens, "high": highs or [max(o, c) for o, c in zip(opens, closes)],
        "low": lows or [min(o, c) for o, c in zip(opens, closes)], "close": closes,
        "volume": [1_000_000] * len(closes), "amount": [1e9] * len(closes)}), sym, "test")


def _lake(tmp_path, frames, stocks, factors=None) -> MarketData:
    lake = Lake(tmp_path / "lake")
    lake.ensure_dirs()
    lake.upsert_daily(pd.concat(frames))
    lake.write_stocks(pd.DataFrame([
        {"symbol": s, "code": s[:6], "exchange": s[-2:], "name": n, "board": rules.board_of(s),
         "list_date": "2000-01-01", "delist_date": dl, "status": "delisted" if dl else "listed", "sources": "test"}
        for s, n, dl in stocks]))
    if factors is not None:
        lake.write_adj_factors(factors)
    lake.write_calendar(DAYS)
    return MarketData(lake)


def _engine(md, fn, **kw):
    s = BacktestSettings(start=DAYS[0], end=DAYS[-1], initial_cash=100_000, lookback=0,
                         benchmark="none", **{"rebalance": "daily", "slippage": 0.0, **kw})
    return Engine(fn, {}, s, md).run()


def test_rules_lots_limits_fees():
    assert rules.round_buy("600000.SH", 1999) == 1900
    assert rules.round_buy("688001.SH", 199) == 0 and rules.round_buy("688001.SH", 257.5) == 257
    assert rules.round_sell("600000.SH", 150, 1000) == 100
    assert rules.round_sell("600000.SH", 950, 1000) == 900
    assert rules.round_sell("600000.SH", 1000, 1050) == 1050  # remainder below one lot -> sell all
    assert rules.limit_pct("300750.SZ", date(2020, 8, 21)) == 0.10
    assert rules.limit_pct("300750.SZ", date(2020, 8, 24)) == 0.20
    assert rules.limit_pct("600000.SH", date(2024, 1, 2), is_st=True) == 0.05
    assert rules.limit_prices(10.0, 0.10) == (11.0, 9.0)
    f = rules.Fees()
    assert f.commission_for(1000) == 5.0 and f.commission_for(100_000) == 25.0
    assert f.tax_for(100_000) == 50.0


def test_rebalance_days():
    days = [date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 29)]
    assert rebalance_days(days, "monthly") == {date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 29)}
    assert rebalance_days(days, "every_n", 2) == {date(2024, 1, 30), date(2024, 2, 1)}


def test_next_open_fill_lots_and_fees(tmp_path):
    md = _lake(tmp_path, [_bars("600000.SH", [10.0] * 30, opens=[10.5] * 30)], [("600000.SH", "浦发银行", None)])
    res = _engine(md, lambda ctx, p: {"600000.SH": 1.0})
    t = res.trades[res.trades["status"] == "filled"]
    first = t.iloc[0]
    # signal on day 0, filled at day 1's open
    assert first["date"] == DAYS[1] and first["price"] == 10.5 and first["side"] == "buy"
    assert first["shares"] == 9500  # 100k / 10.5 = 9523 -> rounded down to a board lot
    assert first["commission"] == round(9500 * 10.5 * 0.00025, 2)
    assert res.equity["equity"].iloc[0] == 100_000
    assert len(t) == 1  # later signals are within tolerance -> no trades


def test_t_plus_one_and_limit_up(tmp_path):
    # A: one-price limit-up on day 6 (after the new-listing window), so the buy
    # signalled on day 5 is blocked at day 6's open and retried on day 7.
    a = _bars("600000.SH", [10.0] * 6 + [11.0] * 24, opens=[10.0] * 6 + [11.0] * 24,
              highs=[10.0] * 6 + [11.0] + [11.2] * 23, lows=[10.0] * 6 + [11.0] + [10.8] * 23)
    b = _bars("000001.SZ", [10.0] * 30)
    md = _lake(tmp_path, [a, b], [("600000.SH", "浦发银行", None), ("000001.SZ", "平安银行", None)])

    def fn(ctx, p):
        return {"600000.SH": 0.5} if ctx.today == DAYS[5] else None

    res = _engine(md, fn)
    tr = res.trades
    blocked = tr[tr["status"] == "blocked"]
    assert len(blocked) == 1 and blocked.iloc[0]["date"] == DAYS[6] and "涨停" in blocked.iloc[0]["reason"]
    filled = tr[tr["status"] == "filled"]
    assert filled.iloc[0]["date"] == DAYS[7]

    # Same-day round trip is refused under T+1 when filling at the close.
    md2 = _lake(tmp_path / "x", [_bars("000001.SZ", [10.0] * 30)], [("000001.SZ", "平安银行", None)])
    calls = {"n": 0}

    def flip(ctx, p):
        calls["n"] += 1
        return {"000001.SZ": 1.0} if calls["n"] == 1 else {}

    res2 = _engine(md2, flip, fill="close", rebalance="daily")
    f2 = res2.trades[res2.trades["status"] == "filled"]
    assert list(f2["side"]) == ["buy", "sell"]
    assert f2.iloc[1]["date"] > f2.iloc[0]["date"]
    assert f2.iloc[1]["tax"] == pytest.approx(f2.iloc[1]["amount"] * 0.0005, abs=0.01)


def test_dividend_keeps_value_and_delisting(tmp_path):
    # 10 -> ex-dividend gap to 9 on day 5 with factor 1.1111: value must not drop.
    closes = [10.0] * 5 + [9.0] * 25
    f = pd.DataFrame({"symbol": ["600000.SH"], "date": [DAYS[5]], "adj_factor": [10 / 9], "source": ["test"]})
    dl = _bars("000002.SZ", [10.0] * 10)  # delisted after day 9
    md = _lake(tmp_path, [_bars("600000.SH", closes), dl],
               [("600000.SH", "浦发银行", None), ("000002.SZ", "万科A", "2024-01-15")], f)

    def fn(ctx, p):
        return {"600000.SH": 0.5, "000002.SZ": 0.4} if ctx.today == DAYS[0] else None

    res = _engine(md, fn)
    eq = res.equity.set_index("date")["equity"]
    assert eq[DAYS[5]] == pytest.approx(eq[DAYS[4]], rel=1e-9)
    tr = res.trades
    out = tr[(tr["symbol"] == "000002.SZ") & (tr["side"] == "sell")]
    assert len(out) == 1 and "退市" in out.iloc[0]["reason"]
    assert out.iloc[0]["date"] == DAYS[10]


SCRIPT = '''
from screening.params import Param
META = {"name": "测试策略", "description": "# 一直满仓\\n说明", "defaults": {"rebalance": "weekly"}}
PARAMS = [Param("w", "仓位", "float", 1.0, min=0, max=1)]
def rebalance(ctx, params):
    ctx.log("signal", len(ctx.universe()))
    return {"600000.SH": params["w"]}
'''


@pytest.fixture()
def reg(tmp_path):
    d = tmp_path / "strategies"
    d.mkdir()
    (d / "hold.py").write_text(SCRIPT, encoding="utf-8")
    (d / "bad.py").write_text('META = {"name": "x", "defaults": {"nope": 1}}\ndef rebalance(ctx, p): pass\n', encoding="utf-8")
    (d / "noentry.py").write_text('META = {"name": "y"}\n', encoding="utf-8")
    return StrategyRegistry(d)


@pytest.fixture()
def md(tmp_path):
    closes = [10 + 0.1 * i for i in range(30)]
    return _lake(tmp_path, [_bars("600000.SH", closes), _bars("000001.SZ", [10.0] * 30)],
                 [("600000.SH", "浦发银行", None), ("000001.SZ", "平安银行", None)])


def test_registry(reg):
    items = {s.id: s for s in reg.scan()}
    assert items["hold"].ok and items["hold"].defaults == {"rebalance": "weekly"}
    assert not items["bad"].ok and "nope" in items["bad"].error
    assert not items["noentry"].ok and "rebalance" in items["noentry"].error


def test_run_and_store(md, reg):
    rec = run_backtest(reg.get("hold"), md, {"w": 0.8}, {"start": DAYS[0].isoformat(), "benchmark": "000001.SZ"})
    assert rec["status"] == "ok" and rec["settings"]["rebalance"] == "weekly"
    store = BacktestStore(md)
    rep = load_report(store, rec["run_id"])
    m = rep["metrics"]
    assert m["total_return"] > 0 and m["benchmark_return"] == pytest.approx(0, abs=1e-9)
    assert rep["equity"][0]["nav"] == pytest.approx(1.0) and "benchmark_nav" in rep["equity"][0]
    assert any("signal" in x for x in rep["logs"])
    assert load_trades(store, rec["run_id"])["total"] >= 1
    h = load_holdings(store, rec["run_id"])
    assert h["items"][0]["symbol"] == "600000.SH" and 0.7 < h["items"][0]["weight"] < 0.85
    assert [r["run_id"] for r in store.history("hold")] == [rec["run_id"]]


def test_failure_recorded(md, reg):
    (reg.directory / "boom.py").write_text(textwrap.dedent('''
        META = {"name": "坏策略", "description": "x"}
        def rebalance(ctx, params):
            raise ValueError("算错了")
    '''), encoding="utf-8")
    rec = run_backtest(reg.get("boom"), md, {}, {"start": DAYS[0].isoformat()})
    assert rec["status"] == "failed" and "算错了" in rec["error"] and "trace" in rec


def test_api(md, reg, monkeypatch):
    monkeypatch.setattr("backend.app.routers.strategies.market_data", lambda: md)
    monkeypatch.setattr("backend.app.routers.strategies.registry", lambda: reg)
    c = TestClient(app)
    j = c.get("/api/v1/strategies").json()
    assert {x["id"] for x in j["items"]} == {"hold", "bad", "noentry"}
    assert c.get("/api/v1/strategies/hold").json()["defaults"] == {"rebalance": "weekly"}
    assert "def rebalance" in c.get("/api/v1/strategies/hold/source").json()["source"]
    assert c.get("/api/v1/strategies/zzz").status_code == 404
    assert c.post("/api/v1/backtests", json={"strategy": "hold", "params": {"w": 2}}).status_code == 422
    assert c.post("/api/v1/backtests", json={"strategy": "hold", "settings": {"fill": "x"}}).status_code == 422
    assert c.post("/api/v1/backtests", json={"strategy": "bad"}).status_code == 422
    r = c.post("/api/v1/backtests", json={"strategy": "hold", "settings": {"start": DAYS[0].isoformat()}})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    for _ in range(100):
        job = c.get("/api/v1/backtests/job").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "ok", job
    rep = c.get(f"/api/v1/backtests/{run_id}").json()
    assert rep["metrics"]["trades"] >= 1 and rep["equity"]
    assert c.get(f"/api/v1/backtests/{run_id}/trades", params={"status": "filled"}).json()["total"] >= 1
    assert c.get(f"/api/v1/backtests/{run_id}/holdings").json()["items"]
    csv = c.get(f"/api/v1/backtests/{run_id}/trades.csv")
    assert csv.status_code == 200 and "symbol" in csv.text
    assert c.get("/api/v1/backtests", params={"strategy": "hold"}).json()["items"][0]["run_id"] == run_id
    assert c.get("/api/v1/backtests/../../etc").status_code == 404
    assert c.get("/api/v1/backtests/20990101-000000-nope").status_code == 404
