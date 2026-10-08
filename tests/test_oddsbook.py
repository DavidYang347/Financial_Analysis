"""High-odds playbook: odds formulas, event classification, point-in-time rules, finalize hook."""
from __future__ import annotations

import math
import textwrap

import pandas as pd
import pytest

from fundlab import events as ev
from fundlab.build import statutory_deadline
from fundlab.store import FundStore
from oddsbook import odds as od
from oddsbook.config import PARAMS
from screening.params import resolve_params

P = resolve_params(PARAMS, {"odds_min": 3.0})  # document default R = 3 for the worked example


def _row(**kw):
    base = dict(close_h=10.0, close=10.0, bvps_h=8.67, tbvps_h=8.67, pb_min=0.9, pb_med=1.5, ind_pb=1.5,
                ind_pe=15.0, shares=1e8, f_period=1.0, norm_np_hist=0.96e8, pos_years=3, np_ttm=0.5e8,
                eps_ttm_h=math.nan, net_cash=0.0, ocf_q_pos2=False, industry="化学制品", amount20=1e8,
                mcap=1e9, board="主板", is_st=False)
    base.update(kw)
    return pd.Series(base, name="600000.SH")


def _sig(**kw):
    base = dict(theme="distress", channels="B", codes="b_forecast_turn", n_ab=2, has_change=True,
                anchor_date=pd.NaT, detail="")
    base.update(kw)
    return pd.Series(base)


def test_buy_line_formula_matches_document_example():
    # §10.1: T = 14.40, D = 7.80, R = 3 -> buy line 9.45
    T, D, R = 14.4, 7.8, 3
    assert (T + R * D) / (1 + R) == pytest.approx(9.45)


def test_card_takes_most_conservative_anchor():
    # §8 step 4: tangible BVPS x 0.8 = 6.94 is below BVPS x min PB = 7.80, so it wins.
    card = od.build_card("600000.SH", _row(), _sig(), pd.DataFrame(), "2025-03-31", P)
    assert card["anchor"] == "净资产打折"
    assert card["falsify_h"] == pytest.approx(8.67 * 0.8)


def test_card_distress_example_uses_pb_floor_and_buy_line():
    # §10.1 with only the PB anchor available (no tangible book value).
    card = od.build_card("600000.SH", _row(tbvps_h=math.nan), _sig(), pd.DataFrame(), "2025-03-31", P)
    # Normalised EPS 0.96 x mid PE 15 = 14.40; floor = BVPS 8.67 x min PB 0.9 = 7.80
    assert card["target_h"] == pytest.approx(14.4, rel=1e-6)
    assert card["falsify_h"] == pytest.approx(7.803, rel=1e-3)
    assert card["anchor"] == "历史最低 PB"
    assert card["odds"] == pytest.approx((14.4 - 10) / (10 - 7.803), rel=1e-3)
    assert card["buy_line_h"] == pytest.approx((14.4 + 3 * 7.803) / 4, rel=1e-3)
    # Bought at the buy line, the odds equal the threshold R by construction.
    assert card["entry_h"] == pytest.approx(card["buy_line_h"])
    assert card["odds_entry"] == pytest.approx(3.0, rel=1e-6)


def test_broken_anchor_is_not_a_floor():
    # Price already below every anchor: downside falls back to broken_downside (fake-odds guard).
    card = od.build_card("600000.SH", _row(close_h=6.0, close=6.0), _sig(), pd.DataFrame(), "2025-03-31", P)
    assert card["anchor_broken"]
    assert card["downside"] == pytest.approx(P["broken_downside"])
    assert card["support"] == "none"


def test_upside_cap():
    card = od.build_card("600000.SH", _row(norm_np_hist=20e8, bvps_h=100.0, tbvps_h=100.0, pb_med=10, ind_pb=10),
                         _sig(), pd.DataFrame(), "2025-03-31", P)
    assert card["target_h"] <= 10.0 * (1 + P["max_upside"]) + 1e-9


def test_event_probability_is_product_of_nodes():
    p, _ = od.event_probability("ma", "a1_ma_draft", P)
    assert p == pytest.approx(0.9 * 0.95 * 0.85 * 0.95)


def test_current_odds():
    card = {"target_h": 14.4, "falsify_h": 7.8}
    assert od.current_odds(card, 9.45) == pytest.approx(3.0, rel=1e-3)
    assert od.current_odds(card, 7.0) == -math.inf


@pytest.mark.parametrize("title,event", [
    ("某某:关于筹划重大资产重组的停牌公告", "ma_plan"),
    ("某某:发行股份及支付现金购买资产暨关联交易报告书(草案)", "ma_draft"),
    ("某某:关于终止筹划重大资产重组事项的公告", "ma_terminated"),
    ("某某:关于控股股东签署股份转让协议暨控制权拟发生变更的提示性公告", "control_change"),
    ("某某:关于收到中国证券监督管理委员会立案告知书的公告", "investigation"),
    ("某某:董事会关于2024年度保留意见审计报告涉及事项的专项说明", "audit_adverse"),
    ("某某:关于公司股票被实施其他风险警示相关事项的进展公告", "st_progress"),
    ("某某:关于持股5%以上股东减持股份的预披露公告", "insider_sell"),
    ("某某:关于持股5%以上股东减持计划期限届满暨实施结果的公告", None),
    ("某某:2024年度非经营性资金占用及其他关联资金往来情况汇总表", None),
    ("某某:中信证券关于某某重大资产重组之独立财务顾问核查意见", None),
    ("某某:关于面向专业投资者非公开发行公司债券预案的公告", None),
])
def test_event_rules(title, event):
    got = ev.classify(pd.DataFrame({"title": [title], "category": [""]})).iloc[0]
    assert (None if pd.isna(got) else got) == event


def test_statutory_deadline():
    s = statutory_deadline(pd.Series(pd.to_datetime(["2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30"])))
    assert [str(x)[:10] for x in s] == ["2025-04-30", "2025-04-30", "2025-08-31", "2025-10-31"]


def test_fin_asof_is_point_in_time(tmp_path):
    d = tmp_path / "fundlab"
    d.mkdir()
    pd.DataFrame({"symbol": ["A", "A"], "period": pd.to_datetime(["2024-12-31", "2025-03-31"]),
                  "ann_date": pd.to_datetime(["2025-04-20", "2025-04-28"]), "np": [1.0, 2.0]}
                 ).to_parquet(d / "fin.parquet")
    fs = FundStore(d)
    assert fs.fin_asof("2025-04-19").empty
    assert fs.fin_asof("2025-04-20")["np"].tolist() == [1.0]       # published that day -> usable after the close
    assert fs.fin_asof("2025-05-01")["np"].tolist() == [2.0]


def test_finalize_tables_are_saved(tmp_path):
    """Strategies may export extra tables from finalize(); the runner saves them as x_<name>.parquet."""
    from tests.test_backtest import DAYS, _bars, _lake
    from strategy.registry import StrategyRegistry
    from strategy.runner import BacktestStore, list_extras, load_extra, run_backtest

    md = _lake(tmp_path, [_bars("600000.SH", [10.0] * 30)], [("600000.SH", "浦发银行", None)])
    sdir = tmp_path / "strategies"
    sdir.mkdir()
    (sdir / "with_extras.py").write_text(textwrap.dedent('''
        import pandas as pd
        META = {"name": "x", "description": "x"}
        def rebalance(ctx, params):
            ctx.state.setdefault("days", []).append(ctx.today)
            return {"600000.SH": 0.5}
        def finalize(ctx, params):
            return {"days": pd.DataFrame({"date": ctx.state["days"]}), "bad name!": pd.DataFrame()}
    '''), encoding="utf-8")
    s = StrategyRegistry(sdir).get("with_extras")
    rec = run_backtest(s, md, {}, {"start": DAYS[0], "end": DAYS[-1], "rebalance": "weekly", "lookback": 0})
    assert rec["status"] == "ok", rec.get("error")
    store = BacktestStore(md)
    names = [x["name"] for x in list_extras(store, rec["run_id"])]
    assert names == ["x_days"]
    assert load_extra(store, rec["run_id"], "x_days")["total"] >= 5
