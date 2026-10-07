"""时点(point-in-time)正确性与事件分类。不依赖网络和真实数据湖:用手工构造的小表。"""
from pathlib import Path

import pandas as pd
import pytest

from fundamentals import events
from fundamentals.pit import PIT


def _pit(tmp_path: Path, **tables) -> PIT:
    for name, df in tables.items():
        df.to_parquet(tmp_path / f"{name}.parquet", index=False)
    return PIT(tmp_path)


def _fin() -> pd.DataFrame:
    def row(sym, period, ann, ocf, npp, rev=100.0):
        return {"symbol": sym, "period": pd.Timestamp(period), "ann_date": pd.Timestamp(ann), "ocf": ocf,
                "np_parent": npp, "revenue": rev, "eps_h": 1.0, "bvps_h": 5.0}
    return pd.DataFrame([
        row("A.SZ", "2024-12-31", "2025-03-28", 40, 40),
        row("A.SZ", "2025-03-31", "2025-04-25", 10, 8),
        row("A.SZ", "2025-06-30", "2025-08-20", 25, 20),
        row("A.SZ", "2025-09-30", "2025-10-28", 45, 50),
    ])


def test_financials_only_visible_after_announcement(tmp_path):
    p = _pit(tmp_path, fin=_fin())
    assert p.latest_fin("2025-04-24").at["A.SZ", "period"] == pd.Timestamp("2024-12-31")
    assert p.latest_fin("2025-04-25").at["A.SZ", "period"] == pd.Timestamp("2025-03-31")   # 公告当天收盘后可见
    assert p.latest_fin("2025-08-19").at["A.SZ", "period"] == pd.Timestamp("2025-03-31")
    assert p.latest_fin("2025-08-20").at["A.SZ", "period"] == pd.Timestamp("2025-06-30")


def test_single_quarter_values_are_differenced_within_year(tmp_path):
    p = _pit(tmp_path, fin=_fin())
    f = p.fin_visible("2025-12-31").set_index("period")
    assert f.at[pd.Timestamp("2025-03-31"), "ocf_q"] == 10           # Q1 = 累计
    assert f.at[pd.Timestamp("2025-06-30"), "ocf_q"] == 15           # H1 - Q1
    assert f.at[pd.Timestamp("2025-09-30"), "np_q"] == 30            # 9M - H1
    # 跨年不相减:年报的 Q4 需要前三季度,缺一个季度就不是单季值
    assert pd.isna(f.at[pd.Timestamp("2024-12-31"), "ocf_q"])


def test_visible_set_cache_does_not_leak_between_days(tmp_path):
    p = _pit(tmp_path, fin=_fin())
    late = p.fin_visible("2025-12-31")
    early = p.fin_visible("2025-04-01")      # 先看晚的,再看早的,不能拿到缓存里的晚数据
    assert len(late) == 4 and len(early) == 1


def test_events_and_holder_windows_respect_day(tmp_path):
    n = pd.DataFrame({"symbol": ["A.SZ"] * 3, "ann_date": pd.to_datetime(["2025-01-10", "2025-02-10", "2025-03-10"]),
                      "title": "x", "category": "y", "src": "s", "event": ["restructure_plan", "restructure_draft", "st_removed"]})
    h = pd.DataFrame({"symbol": ["A.SZ"] * 2, "ann_date": pd.to_datetime(["2025-02-01", "2025-05-01"]),
                      "direction": ["增持", "增持"], "pct_total": [0.6, 0.9], "start": pd.NaT, "end": pd.NaT,
                      "holder": "h", "pct_after": 1.0})
    p = _pit(tmp_path, notices=n, holder=h)
    assert set(p.events("2025-02-15", 90)["event"]) == {"restructure_plan", "restructure_draft"}
    assert p.events("2025-02-15", 90, ["st_removed"]).empty
    assert len(p.holder_moves("2025-03-01", 90, "增持")) == 1
    assert len(p.holder_moves("2025-05-01", 90, "增持")) == 2       # 02-01 还在 90 天窗口内
    assert len(p.holder_moves("2025-06-01", 90, "增持")) == 1       # 02-01 已经超出窗口


def test_pledge_is_lagged(tmp_path):
    pl = pd.DataFrame({"symbol": ["A.SZ", "A.SZ"], "date": pd.to_datetime(["2025-03-07", "2025-03-14"]),
                       "ratio": [40.0, 80.0]})
    p = _pit(tmp_path, pledge=pl)
    # 3-14 的数据在 3-14 当天还看不到,3-21 才可见
    assert p.pledge_latest("2025-03-14").at["A.SZ"] == 40.0
    assert p.pledge_latest("2025-03-20").at["A.SZ"] == 40.0
    assert p.pledge_latest("2025-03-21").at["A.SZ"] == 80.0


@pytest.mark.parametrize("title,category,expected", [
    ("万通发展:关于终止重大资产重组事项的公告", "重组进展公告", "restructure_terminate"),
    ("英集芯:关于终止筹划重大资产重组事项暨公司股票复牌公告", "", "restructure_terminate"),
    ("亚振家居:关于终止筹划控制权变更事项暨复牌的公告", "", "restructure_terminate"),
    ("东土科技:关于披露发行股份及支付现金购买资产并募集配套资金预案后的进展公告", "", "restructure_plan"),
    ("某某:重大资产购买之标的资产完成过户的公告", "", "restructure_done"),
    ("某某:关于控股股东协议转让股份暨控制权拟发生变更的公告", "", "ctrl_change"),
    ("苏奥传感:关于股份协议转让完成过户登记暨公司控制权拟发生变更的进展公告", "", "ctrl_change"),
    ("动力源:关于协议转让股份完成过户登记并解除质押暨控制权发生变更的公告", "", "ctrl_change_done"),
    ("*ST盛屯:关于撤销其他风险警示暨停牌的公告", "", "st_removed"),
    ("ST任子行:关于申请撤销其他风险警示的公告", "", "st_apply_remove"),
    ("某某:关于公司股票被实施退市风险警示暨停牌的公告", "", "st_imposed"),
    ("某某:关于被债权人申请重整及预重整的进展公告", "", "bankruptcy_pre"),
    ("某某:关于公司收到立案告知书的公告(立案调查)", "", "fraud_risk"),
    ("某某:关于股东增持公司股份的公告", "股东/实际控制人股份增持", "holder_inc"),
    ("某某:关于召开2025年第一次临时股东大会的通知", "", ""),
])
def test_event_classification(title, category, expected):
    assert events.classify(title, category) == expected


def test_termination_beats_other_restructure_tags():
    # 终止类必须排在重组类前面,否则"终止重大资产重组"会被当成重组推进
    tags = [t for t, _, _ in events.RULES]
    assert tags.index("restructure_terminate") < tags.index("restructure_plan")
    assert tags.index("restructure_terminate") < tags.index("restructure_misc")
    assert tags.index("st_removed") < tags.index("st_imposed")
