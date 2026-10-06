"""Offline unit tests for the data module (no network)."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from data import symbols as sym
from data.adjust import factors_from_events, normalize_factor_points
from data.query import MarketData
from data.schema import normalize_daily
from data.sources import SourceChain
from data.sources.base import DataSource, SourceError, SourceUnavailable, volume_to_shares
from data.store import Lake


# ---- symbols -----------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("600000", "600000.SH"), ("sh600000", "600000.SH"), ("sh.600000", "600000.SH"),
    ("600000.SS", "600000.SH"), ("000001", "000001.SZ"), ("300750.sz", "300750.SZ"),
    ("920002", "920002.BJ"), ("688981", "688981.SH"),
])
def test_normalize(raw, expected):
    assert sym.normalize(raw) == expected


def test_source_formats():
    assert sym.to_tencent("600000.SH") == "sh600000"
    assert sym.to_baostock("000001.SZ") == "sz.000001"
    assert sym.to_tdx("920002.BJ") == (2, "920002")
    assert sym.to_eastmoney_secid("600000.SH") == "1.600000"
    assert not sym.is_a_share("830799.BJ")  # legacy BSE code, migrated to 920xxx
    with pytest.raises(ValueError):
        sym.normalize("XYZ")


# ---- units -------------------------------------------------------------------
def test_volume_unit_inference():
    close = pd.Series([10.0, 10.0])
    # amount / (vol * close) ~ 100 -> volume was in 手
    assert list(volume_to_shares(pd.Series([100, 200]), pd.Series([1e5, 2e5]), close, 100)) == [10000, 20000]
    # ratio ~ 1 -> already shares (e.g. Tencent STAR market)
    assert list(volume_to_shares(pd.Series([100, 200]), pd.Series([1e3, 2e3]), close, 100)) == [100, 200]


def test_normalize_daily_drops_bad_rows():
    df = pd.DataFrame({"date": ["2024-01-02", "2024-01-03", "2024-01-03"],
                       "open": [1, 0, 2], "high": [1, 0, 2], "low": [1, 0, 2], "close": [1, 0, 2],
                       "volume": [1, 1, 1]})
    out = normalize_daily(df, "600000.SH", "test")
    assert list(out["date"]) == [date(2024, 1, 2), date(2024, 1, 3)]
    assert out["close"].iloc[-1] == 2


# ---- adjustment factors ----------------------------------------------------
def _bars(closes, start="2024-01-01"):
    d = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"date": d.date, "close": closes})


def test_factor_cash_dividend():
    bars = _bars([10.0, 10.0, 9.5, 9.6])
    ev = pd.DataFrame([{"date": bars["date"][2], "cash": 0.5, "bonus": 0, "rights": 0, "rights_price": 0}])
    f = factors_from_events(bars, ev)
    assert len(f) == 1 and f["date"][0] == bars["date"][2]
    assert f["adj_factor"][0] == pytest.approx(10.0 / 9.5)


def test_factor_bonus_and_nontrading_exdate():
    bars = _bars([20.0, 20.0, 10.0])
    # ex-date falls on a weekend -> applied to the next bar
    ev = pd.DataFrame([{"date": pd.Timestamp(bars["date"][2]) - pd.Timedelta(days=1),
                        "cash": 0, "bonus": 1.0, "rights": 0, "rights_price": 0}])
    f = factors_from_events(bars, ev)
    assert f["adj_factor"][0] == pytest.approx(2.0)


def test_normalize_provider_factor_rebases_to_one():
    bars = _bars([10.0] * 5, start="2024-01-01")
    pts = pd.DataFrame({"date": [date(2023, 6, 1), date(2024, 1, 3)], "adj_factor": [2.0, 2.2]})
    f = normalize_factor_points(pts, bars)
    assert list(f["date"]) == [date(2024, 1, 3)]
    assert f["adj_factor"][0] == pytest.approx(1.1)


# ---- fallback chain ----------------------------------------------------------
class _Fake(DataSource):
    def __init__(self, name, behavior):
        self.name, self.behavior, self.calls = name, behavior, 0

    def fetch_daily(self, symbol, start, end):
        self.calls += 1
        if self.behavior == "error":
            raise SourceError("boom")
        if self.behavior == "unavailable":
            raise SourceUnavailable("no token")
        if self.behavior == "empty":
            return pd.DataFrame()
        return pd.DataFrame({"date": [start], "close": [1.0]})


def test_chain_falls_back_and_disables():
    a, b, c = _Fake("tencent", "error"), _Fake("mootdx", "unavailable"), _Fake("eastmoney", "ok")
    chain = SourceChain(order=["tencent", "mootdx", "eastmoney"],
                        instances={"tencent": a, "mootdx": b, "eastmoney": c}, max_consecutive_errors=2)
    for _ in range(3):
        res = chain.daily("600000.SH", date(2024, 1, 2), date(2024, 1, 2))
        assert res.source == "eastmoney"
    assert a.calls == 2          # circuit breaker after 2 consecutive errors
    assert b.calls == 1          # unavailable -> disabled immediately
    assert set(chain.disabled()) == {"tencent", "mootdx"}


def test_chain_empty_answers_stop_early():
    a, b, c = _Fake("tencent", "empty"), _Fake("mootdx", "empty"), _Fake("eastmoney", "ok")
    chain = SourceChain(order=["tencent", "mootdx", "eastmoney"],
                        instances={"tencent": a, "mootdx": b, "eastmoney": c})
    res = chain.daily("600000.SH", date(2024, 1, 2), date(2024, 1, 2), max_empty_answers=2)
    assert res.data is None and res.empty_answers == 2 and c.calls == 0


# ---- storage + query -----------------------------------------------------------
def _daily(symbol, days, closes):
    return normalize_daily(pd.DataFrame({"date": days, "open": closes, "high": closes, "low": closes,
                                         "close": closes, "volume": [100] * len(days),
                                         "amount": [1000.0] * len(days)}), symbol, "test")


def test_upsert_and_adjusted_query(tmp_path):
    lake = Lake(tmp_path)
    lake.ensure_dirs()
    days = ["2023-12-29", "2024-01-02", "2024-01-03"]
    lake.upsert_daily(_daily("600000.SH", days, [10.0, 10.0, 9.0]))
    # overwrite one row and append another: new rows win, no duplicates
    lake.upsert_daily(_daily("600000.SH", ["2024-01-03", "2024-01-04"], [9.5, 9.6]))
    lake.write_adj_factors(pd.DataFrame({"symbol": ["600000.SH"], "date": [date(2024, 1, 3)],
                                         "adj_factor": [2.0], "source": ["test"]}))
    md = MarketData(lake)
    raw = md.daily("600000.SH")
    assert list(raw["close"]) == [10.0, 10.0, 9.5, 9.6]
    hfq = md.daily("600000.SH", adjust="hfq")
    assert list(hfq["close"]) == [10.0, 10.0, 19.0, 19.2]
    qfq = md.daily("600000.SH", adjust="qfq")
    assert list(qfq["close"]) == [5.0, 5.0, 9.5, 9.6]   # latest price unchanged
    assert sorted(p.parent.name for p in tmp_path.glob("daily/year=*/part.parquet")) == ["year=2023", "year=2024"]
