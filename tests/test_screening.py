"""Screening framework and run bookkeeping (offline, temporary lake)."""
from __future__ import annotations

import logging
import textwrap
from datetime import date

import pandas as pd
import pytest

from data.maintenance import runs
from data.query import MarketData
from data.schema import normalize_daily
from data.store import Lake
from screening.params import COMMON_FILTERS, Param, ParamError, opt, resolve_params
from screening.registry import Registry
from screening.runner import ScreenStore, run_screener


@pytest.fixture()
def md(tmp_path):
    lake = Lake(tmp_path / "lake")
    lake.ensure_dirs()
    days = pd.bdate_range("2024-01-01", periods=30).date
    frames = []
    for sym, slope in (("600000.SH", 0.1), ("000001.SZ", -0.1), ("300750.SZ", 0.2)):
        closes = [10 + slope * i for i in range(len(days))]
        frames.append(normalize_daily(pd.DataFrame({
            "date": days, "open": closes, "high": closes, "low": closes, "close": closes,
            "volume": [1000] * len(days), "amount": [1e9] * len(days)}), sym, "test"))
    lake.upsert_daily(pd.concat(frames))
    lake.write_stocks(pd.DataFrame([
        {"symbol": s, "code": s[:6], "exchange": s[-2:], "name": n, "board": b, "list_date": "2000-01-01",
         "delist_date": None, "status": "listed", "sources": "test"}
        for s, n, b in (("600000.SH", "浦发银行", "主板"), ("000001.SZ", "ST平安", "主板"),
                        ("300750.SZ", "宁德时代", "创业板"))]))
    lake.write_calendar(list(days))
    return MarketData(lake)


SCRIPT = '''
import pandas as pd
from screening.context import last_rows
from screening.params import COMMON_FILTERS, Param

META = {"name": "上涨股", "description": "# 选出上涨的股票\\n说明", "tags": ["测试"],
        "columns": {"ret": "涨幅%"}}
PARAMS = [Param("n", "周期", "int", 10, min=2, max=20), *COMMON_FILTERS]

def screen(ctx, params):
    pool = ctx.universe(params)
    bars = ctx.bars(params["n"], pool)
    first = bars.groupby("symbol").head(1).set_index("symbol")["close"]
    last = last_rows(bars).set_index("symbol")["close"]
    ret = (last / first - 1) * 100
    return ret[ret > 0].rename("ret").reset_index()
'''


@pytest.fixture()
def reg(tmp_path):
    d = tmp_path / "screeners"
    d.mkdir()
    (d / "up.py").write_text(SCRIPT, encoding="utf-8")
    (d / "broken.py").write_text("META = {}\n", encoding="utf-8")
    (d / "_helper.py").write_text("X = 1\n", encoding="utf-8")
    return Registry(d)


def test_params_validation():
    ps = [Param("n", "周期", "int", 5, min=1, max=10),
          Param("mode", "模式", "select", "a", options=[opt("a"), opt("b")])]
    assert resolve_params(ps, {}) == {"n": 5, "mode": "a"}
    assert resolve_params(ps, {"n": "7"}) == {"n": 7, "mode": "a"}
    for bad in ({"n": 11}, {"n": 1.5}, {"mode": "c"}, {"zzz": 1}):
        with pytest.raises(ParamError):
            resolve_params(ps, bad)


def test_registry_discovers_and_reports_errors(reg):
    items = {s.id: s for s in reg.scan()}
    assert set(items) == {"up", "broken"}  # _helper.py ignored
    assert items["up"].ok and items["up"].name == "上涨股"
    assert items["up"].summary()["brief"] == "选出上涨的股票"
    assert not items["broken"].ok and "name" in items["broken"].error


def test_registry_picks_up_new_and_deleted_files(reg):
    d = reg.directory
    (d / "second.py").write_text(SCRIPT.replace("上涨股", "第二个"), encoding="utf-8")
    assert {s.id for s in reg.scan()} == {"up", "broken", "second"}
    (d / "second.py").unlink()
    assert {s.id for s in reg.scan()} == {"up", "broken"}


def test_run_and_history(md, reg):
    s = reg.get("up")
    # fixture has only 30 trading days, so turn off the default "listed >= 120 days" filter
    r = run_screener(s, md, {"n": 10, "exclude_st": True, "min_amount": 0, "min_list_days": 0})
    assert r["status"] == "ok"
    assert {x["symbol"] for x in r["items"]} == {"600000.SH", "300750.SZ"}
    assert r["items"][0]["name"] in ("浦发银行", "宁德时代")
    assert {"key": "ret", "label": "涨幅%"} in r["columns"]
    # board filter drops ChiNext
    r2 = run_screener(s, md, {"boards": ["主板"], "min_amount": 0, "min_list_days": 0})
    assert [x["symbol"] for x in r2["items"]] == ["600000.SH"]
    store = ScreenStore(md)
    hist = store.history("up")
    assert [h["run_id"] for h in hist] == [r2["run_id"], r["run_id"]]
    rec, df = store.get(r["run_id"])
    assert rec["count"] == 2 and len(df) == 2


def test_run_failure_is_recorded(md, reg, tmp_path):
    (reg.directory / "boom.py").write_text(textwrap.dedent('''
        META = {"name": "坏脚本", "description": "x"}
        def screen(ctx, params):
            raise ValueError("算错了")
    '''), encoding="utf-8")
    r = run_screener(reg.get("boom"), md, {})
    assert r["status"] == "failed" and "算错了" in r["error"]
    assert ScreenStore(md).history("boom")[0]["status"] == "failed"


def test_common_filters_shape():
    assert {p.key for p in COMMON_FILTERS} == {"exclude_st", "exclude_bj", "boards", "min_list_days", "min_amount"}


def test_lake_lock_and_log_capture(tmp_path):
    lake = Lake(tmp_path / "lake")
    rid = runs.new_run_id("incremental")
    with runs.lake_lock(lake):
        assert runs.is_locked(lake)
        with pytest.raises(runs.LakeBusy):
            with runs.lake_lock(lake):
                pass
        with runs.capture_log(lake, rid):
            logging.getLogger("data.test").info("hello from the run")
    assert not runs.is_locked(lake)
    text, nxt = runs.read_log(lake, rid)
    assert "hello from the run" in text and nxt == len(text.encode())
    lake.append_log({"run_id": rid, "mode": "incremental", "started_at": "x"})
    total, items = runs.list_runs(lake)
    assert total == 1 and items[0]["run_id"] == rid and items[0]["has_log"]
    with pytest.raises(ValueError):
        runs.log_path(lake, "../../etc/passwd")
