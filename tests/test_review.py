"""Market review statistics: period returns, low-to-high doubling, limit-up counting, report rendering, API."""
from __future__ import annotations

import pandas as pd
import pytest

from review import render, stats, store


def _md(tmp_path, closes: dict[str, list[float]], days, highs=None, lows=None):
    from tests.test_backtest import _bars, _lake
    frames = []
    for s, c in closes.items():
        h = (highs or {}).get(s)
        lo = (lows or {}).get(s)
        frames.append(_bars(s, c, highs=h, lows=lo, days=days))
    return _lake(tmp_path, frames, [(s, s[:6], None) for s in closes])


@pytest.fixture
def lake(tmp_path, monkeypatch):
    # 70 trading days before the month so stocks count as listed > 60 days, then one month.
    days = list(pd.bdate_range("2026-03-02", "2026-06-30").date)
    n_before = sum(1 for d in days if d < pd.Timestamp("2026-06-01").date())
    flat = [10.0] * len(days)
    # A: closes at the month's end x2.5; dips to 8 then hits 20 intra-month (low-to-high 150%).
    a = [10.0] * n_before + [10.0] * (len(days) - n_before)
    a[-1] = 25.0
    a_low = list(a)
    a_low[n_before + 2] = 8.0
    a_high = list(a)
    a_high[n_before + 5] = 20.0
    # B: +10% limit-up for 3 days in June, then flat.
    b = [10.0] * n_before
    v = 10.0
    for i in range(len(days) - n_before):
        if i < 3:
            v = round(v * 1.1, 2)
        b.append(v)
    md = _md(tmp_path, {"600001.SH": a, "600002.SH": b, "600003.SH": flat}, days,
             highs={"600001.SH": [max(x, y) for x, y in zip(a, a_high)]},
             lows={"600001.SH": [min(x, y) for x, y in zip(a, a_low)]})
    monkeypatch.setattr(store, "STATS", tmp_path / "reviews")
    monkeypatch.setattr(store, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(stats, "RAW", tmp_path / "raw")  # no external data in tests
    return md


def test_monthly_returns_doublers_and_limits(lake):
    r = stats.monthly("2026-06", md=lake)
    g = {x["symbol"]: x for x in r["gainers"]}
    assert g["600001.SH"]["ret"] == pytest.approx(1.5)
    # low-to-high: lowest low 8 before the high 25 at month end -> 25 / 8 - 1
    assert g["600001.SH"]["l2h"] == pytest.approx(25 / 8 - 1)
    assert r["breadth"]["doubled_cc"] == 1
    assert {x["symbol"] for x in r["doubled_l2h"]} == {"600001.SH"}
    assert g["600002.SH"]["limit_up_days"] == 3 and g["600002.SH"]["max_streak"] == 3
    assert r["gainers"][0]["symbol"] == "600001.SH"
    assert store.load_stats("monthly", "2026-06")["label"] == "2026-06"


def test_render_fills_placeholders(lake, tmp_path, monkeypatch):
    stats.monthly("2026-06", md=lake)
    src = tmp_path / "src" / "monthly"
    src.mkdir(parents=True)
    (src / "2026-06.md").write_text("# t\n中位数 {{v:breadth.median|pct}}\n\n{{t:doubled}}\n{{t:breadth}}\n", encoding="utf-8")
    monkeypatch.setattr(render, "SRC", tmp_path / "src")
    out = render.render("monthly", "2026-06").read_text(encoding="utf-8")
    assert "{{" not in out and "600001.SH" in out and "| 指标 | 数值 |" in out


def test_store_rejects_bad_labels():
    for kind, label in (("monthly", "../x"), ("monthly", "2026-6"), ("weekly", "2026-09"), ("daily", "2026-09")):
        with pytest.raises(ValueError):
            store.check(kind, label)
    store.check("weekly", "2026-W40")


def test_emotion_stage_rules():
    base = {"limit_up": 50, "limit_down": 5, "max_streak": 4, "premium": 0.01, "up_ratio": 0.5}
    assert stats.classify_emotion({**base, "limit_down_ex_st": 150, "limit_up_ex_st": 20}) == "冰点"
    assert stats.classify_emotion({**base, "limit_down_ex_st": 40, "limit_up_ex_st": 60}) == "分歧/退潮"
    assert stats.classify_emotion({**base, "limit_down_ex_st": 3, "limit_up_ex_st": 130, "up_ratio": 0.7}) == "高潮"
    assert stats.classify_emotion({**base, "limit_down_ex_st": 3, "limit_up_ex_st": 70, "max_streak": 6}) == "发酵/主升"
    assert stats.classify_emotion({**base, "limit_down_ex_st": 3, "limit_up_ex_st": 50}) == "修复"
