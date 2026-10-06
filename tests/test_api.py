"""API tests against a tiny temporary lake (no network)."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app import deps
from backend.app.main import app
from data.query import MarketData
from data.schema import normalize_daily
from data.store import Lake


@pytest.fixture()
def client(tmp_path, monkeypatch):
    lake = Lake(tmp_path)
    lake.ensure_dirs()
    days = ["2024-01-02", "2024-01-03", "2024-01-04"]
    bars = normalize_daily(pd.DataFrame({"date": days, "open": [10, 10, 9], "high": [10, 10, 9],
                                         "low": [10, 10, 9], "close": [10.0, 10.0, 9.0],
                                         "volume": [100] * 3, "amount": [1e3] * 3}), "600000.SH", "test")
    lake.upsert_daily(bars)
    lake.write_adj_factors(pd.DataFrame({"symbol": ["600000.SH"], "date": [date(2024, 1, 4)],
                                         "adj_factor": [1.1], "source": ["test"]}))
    lake.write_stocks(pd.DataFrame([{"symbol": "600000.SH", "code": "600000", "exchange": "SH", "name": "浦发银行",
                                     "board": "主板", "list_date": "1999-11-10", "delist_date": None,
                                     "status": "listed", "sources": "test"}]))
    lake.write_calendar([date.fromisoformat(d) for d in days])
    md = MarketData(lake)
    app.dependency_overrides[deps.market_data] = lambda: md
    monkeypatch.setattr("backend.app.routers.data_admin.market_data", lambda: md)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_daily_endpoints(client):
    r = client.get("/api/v1/daily/sh600000", params={"adjust": "qfq"})
    assert r.status_code == 200
    j = r.json()
    assert j["symbol"] == "600000.SH" and j["count"] == 3
    assert [x["close"] for x in j["items"]] == [pytest.approx(10 / 1.1, abs=1e-4)] * 2 + [9.0]
    assert client.get("/api/v1/daily", params={"symbols": "600000.SH"}).json()["count"] == 3


def test_meta_endpoints(client):
    assert client.get("/api/v1/stocks", params={"keyword": "浦发"}).json()["count"] == 1
    assert client.get("/api/v1/stocks/600000").json()["name"] == "浦发银行"
    assert client.get("/api/v1/stocks/600001").status_code == 404
    assert client.get("/api/v1/calendar").json()["count"] == 3
    assert client.get("/api/v1/cross-section/2024-01-03").json()["count"] == 1
    assert client.get("/api/v1/data/status").json()["rows"] == 3


def test_validation(client):
    assert client.get("/api/v1/daily/XYZ").status_code == 400
    assert client.get("/api/v1/daily/600000.SH", params={"adjust": "bad"}).status_code == 422
