"""Historical names / ST status: segment building, lookups, and their use in backtests (offline)."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from data import names
from data.names import (StHistory, clean_name, is_st_name, segments_from_changes, segments_from_flags,
                        segments_from_intervals)
from data.query import MarketData
from data.schema import normalize_daily
from data.store import Lake
from strategy import rules
from strategy.engine import BacktestSettings, Engine


def test_name_helpers():
    assert clean_name("ST 国 农") == "ST国农" and clean_name("深安达Ａ") == "深安达A"
    assert is_st_name("*ST国华") and is_st_name("S*ST天龙") and is_st_name("PT水仙")
    assert not is_st_name("国华网安") and not is_st_name("")


def test_segments_from_changes():
    ch = pd.DataFrame({"date": [date(1999, 4, 27), date(2001, 3, 7)],
                       "before": ["深安达A", "ST深安达"], "after": ["ST深安达", "深安达A"]})
    seg = segments_from_changes("000004.SZ", ch, "国华退", date(1991, 1, 14), "szse")
    assert list(seg["name"]) == ["深安达A", "ST深安达", "深安达A"]
    assert list(seg["is_st"]) == [False, True, False]
    assert seg.iloc[1]["start"] == date(1999, 4, 27) and seg.iloc[1]["end"] == date(2001, 3, 6)
    assert seg.iloc[-1]["end"] is None
    empty = segments_from_changes("000001.SZ", ch.iloc[0:0], "平安银行", None, "szse")
    assert len(empty) == 1 and empty.iloc[0]["name"] == "平安银行"


def test_segments_from_flags_and_intervals():
    f = pd.DataFrame({"date": [date(2020, 1, d) for d in (2, 3, 6, 7, 8)], "is_st": [False, True, True, False, False]})
    seg = segments_from_flags("600234.SH", f)
    assert list(seg["is_st"]) == [False, True, False]
    assert seg.iloc[1]["start"] == date(2020, 1, 3) and seg.iloc[1]["end"] == date(2020, 1, 6)
    ts = pd.DataFrame({"name": ["天龙集团", "ST天龙"], "start_date": ["20000615", "20030417"],
                       "end_date": ["20030416", None]})
    seg2 = segments_from_intervals("600234.SH", ts)
    assert list(seg2["is_st"]) == [False, True] and seg2.iloc[1]["end"] is None


def _history() -> StHistory:
    seg = pd.DataFrame([
        {"symbol": "600001.SH", "start": date(2020, 1, 1), "end": date(2020, 6, 30), "name": "ST甲", "is_st": True, "source": "x"},
        {"symbol": "600001.SH", "start": date(2020, 7, 1), "end": None, "name": "甲", "is_st": False, "source": "x"},
    ])
    cov = pd.DataFrame([{"symbol": "600001.SH", "method": "baostock"}, {"symbol": "600002.SH", "method": "never_st"},
                        {"symbol": "600003.SH", "method": "undated"}])
    stocks = pd.DataFrame({"symbol": ["600001.SH", "600002.SH", "600003.SH"], "name": ["甲", "乙", "*ST丙"]})
    return StHistory(seg, cov, stocks)


def test_st_history_lookups():
    h = _history()
    assert h.is_st("600001.SH", date(2020, 3, 1)) and not h.is_st("600001.SH", date(2020, 7, 1))
    assert not h.is_st("600002.SH", date(2020, 3, 1))
    # no dated history -> current name decides
    assert h.is_st("600003.SH", date(2010, 1, 1))
    assert h.on(date(2020, 3, 1)) == {"600001.SH", "600003.SH"}
    m = h.mask(pd.Series(["600001.SH", "600001.SH", "600002.SH"]),
               pd.Series(pd.to_datetime(["2020-06-30", "2020-07-01", "2020-03-01"])))
    assert list(m) == [True, False, False]


def test_st_limit_rule_change():
    assert rules.limit_pct("600000.SH", date(2026, 7, 3), is_st=True) == 0.05
    assert rules.limit_pct("600000.SH", date(2026, 7, 6), is_st=True) == 0.10
    assert rules.limit_pct("300001.SZ", date(2026, 7, 6), is_st=True) == 0.20


def test_refresh_names_offline(tmp_path, monkeypatch):
    lake = Lake(tmp_path / "lake")
    lake.ensure_dirs()
    lake.write_stocks(pd.DataFrame([
        {"symbol": s, "code": s[:6], "exchange": s[-2:], "name": n, "board": "主板", "list_date": "2000-01-01",
         "delist_date": None, "status": "listed", "sources": "t"}
        for s, n in (("000004.SZ", "国华退"), ("600000.SH", "浦发银行"), ("600234.SH", "科新发展"))]))
    monkeypatch.setattr(names, "fetch_eastmoney_former_names", lambda: {
        "000004.SZ": ["深安达A", "ST深安达", "国华退"], "600000.SH": ["浦发银行", "G浦发", "浦发银行"],
        "600234.SH": ["天龙集团", "ST天龙", "科新发展"]})
    monkeypatch.setattr(names, "fetch_szse_name_changes", lambda: pd.DataFrame({
        "symbol": ["000004.SZ"], "date": [date(1999, 4, 27)], "before": ["深安达A"], "after": ["ST深安达"]}))
    calls = []

    def fake_flags(self, s):
        calls.append(s)
        return pd.DataFrame({"date": [date(2003, 4, 16), date(2003, 4, 17)], "is_st": [False, True]})

    monkeypatch.setattr(names._Baostock, "flags", fake_flags)
    rep = names.refresh_names(lake)
    assert rep["methods"] == {"szse": 1, "never_st": 1, "baostock": 1} and calls == ["600234.SH"]
    # second run: chain unchanged -> no per-symbol query
    calls.clear()
    names.refresh_names(lake)
    assert calls == []
    md = MarketData(lake)
    h = md.st_history()
    assert h.is_st("600234.SH", date(2010, 1, 1)) and not h.is_st("600234.SH", date(2000, 1, 1))
    assert h.is_st("000004.SZ", date(2000, 1, 1)) and not h.is_st("600000.SH", date(2000, 1, 1))
    assert list(md.name_history("000004.SZ")["name"]) == ["深安达A", "ST深安达"]
    assert set(md.st_stocks("2010-01-01")["symbol"]) == {"000004.SZ", "600234.SH"}


def test_backtest_uses_historical_st(tmp_path):
    """A stock that is ST in the past but not today: a +5% day must count as limit-up then."""
    days = list(pd.bdate_range("2024-01-01", periods=12).date)
    closes = [10.0] * 6 + [10.5] * 6
    bars = normalize_daily(pd.DataFrame({"date": days, "open": closes, "high": closes, "low": closes, "close": closes,
                                         "volume": [1_000_000] * 12, "amount": [1e9] * 12}), "600234.SH", "t")
    lake = Lake(tmp_path / "lake")
    lake.ensure_dirs()
    lake.upsert_daily(bars)
    lake.write_stocks(pd.DataFrame([{"symbol": "600234.SH", "code": "600234", "exchange": "SH", "name": "科新发展",
                                     "board": "主板", "list_date": "2000-01-01", "delist_date": None,
                                     "status": "listed", "sources": "t"}]))
    lake.write_calendar(days)

    def run():
        md = MarketData(lake)
        s = BacktestSettings(start=days[0], end=days[-1], initial_cash=100_000, lookback=0, benchmark="none",
                             rebalance="daily", slippage=0.0)
        fn = lambda ctx, p: {"600234.SH": 1.0} if ctx.today == days[5] else None  # noqa: E731
        return Engine(fn, {}, s, md).run().trades

    def history(method, is_st):
        names._write(lake, pd.DataFrame([{"symbol": "600234.SH", "start": date(2023, 1, 1), "end": None,
                                          "name": "*ST科新" if is_st else "科新发展", "is_st": is_st, "source": "t"}]),
                     names.SEGMENT_SCHEMA, "names.parquet", ["symbol"])
        names._write(lake, pd.DataFrame([{"symbol": "600234.SH", "method": method, "former_names": "",
                                          "chain_hash": "", "updated_at": "", "error": None}]),
                     names.COVERAGE_SCHEMA, "name_coverage.parquet", ["symbol"])

    def first_fill(t):
        return t[t["status"] == "filled"].iloc[0]["date"]

    # No history: the one-price +5% day might have been an ST limit, so the safety net blocks it.
    assert first_fill(run()) == days[7]
    # History says not ST then: +5% is below the 10% limit, the buy fills on day 6.
    history("baostock", False)
    assert first_fill(run()) == days[6]
    # History says ST then: day 6 is exactly the 5% limit-up, the buy waits one day.
    history("baostock", True)
    t = run()
    blocked = t[t["status"] == "blocked"]
    assert len(blocked) == 1 and "涨停" in blocked.iloc[0]["reason"] and blocked.iloc[0]["date"] == days[6]
    assert first_fill(t) == days[7]
