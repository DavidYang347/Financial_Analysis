"""赔率与仓位公式:用策略文档第 10 节三个算例的数字校验。"""
import pytest

from highodds import config, odds, risk

CFG = config.resolve({})


def test_example1_distress_reversal():
    c = odds.scenario_card(10.0, 7.8, 14.4, 20.0, (0.3, 0.5, 0.2))
    assert c.odds() == pytest.approx(2.0, abs=1e-6)
    assert c.expected() == pytest.approx(0.354, abs=1e-6)
    assert c.buy_line(3) == pytest.approx(9.45, abs=1e-9)
    # 在买入线处赔率恰好 3:1,期望 +43.3%
    assert c.odds(9.45) == pytest.approx(3.0, abs=1e-9)
    assert c.expected(9.45) == pytest.approx(0.4328, abs=5e-4)
    ok, why = odds.passes(c, CFG)
    assert not ok and any("赔率" in w for w in why)
    assert odds.passes(odds.scenario_card(9.45, 7.8, 14.4, 20.0, (0.3, 0.5, 0.2)), CFG)[0]


def test_example1_sensitivity_and_size():
    c = odds.scenario_card(9.45, 7.8, 14.4, 20.0, (0.3, 0.5, 0.2))
    assert c.downside() == pytest.approx(0.1746, abs=1e-3)
    # 文档:仓位 = 1% ÷ (17.5% + 5%) ≈ 4.4%
    assert risk.position_size(0.01, 0.175, 0.05) == pytest.approx(0.0444, abs=1e-4)
    s = odds.sensitivity(c, CFG)
    assert s["passed"] >= 2
    # 下行锚压到 PB 0.7 倍(6.07 元)时在 9.45 买入,赔率约 1.46
    deep = odds.OddsCard(**{**c.__dict__, "bear": 6.07})
    assert deep.odds() == pytest.approx(1.46, abs=0.01)
    assert deep.expected() == pytest.approx(0.378, abs=0.01)


def test_example2_binary_event():
    c = odds.binary_card(7.0, 4.8, 10.5, 0.69)
    assert c.expected() == pytest.approx(0.2476, abs=1e-3)
    assert c.odds() == pytest.approx(1.59, abs=0.01)
    assert c.breakeven_p() == pytest.approx(0.386, abs=1e-3)
    assert c.buy_line(2.5) == pytest.approx(6.4286, abs=1e-3)
    c2 = odds.binary_card(6.43, 4.8, 10.5, 0.69)
    assert c2.odds() == pytest.approx(2.5, abs=0.02)
    assert c2.expected() == pytest.approx(0.359, abs=5e-3)
    # 二元事件风险预算减半:0.5% ÷ (25.3% + 5%) ≈ 1.7%(文档口径,缓冲取主板 5%)
    assert risk.position_size(0.005, 0.253, 0.05) == pytest.approx(0.0165, abs=5e-4)


def test_example3_sotp_scenarios():
    c = odds.scenario_card(10.0, 8.5, 14.75, 19.2, (0.3, 0.5, 0.2))
    assert c.odds() == pytest.approx(4.75 / 1.5, abs=1e-6)
    assert c.expected() == pytest.approx(0.377, abs=2e-3)


def test_quick_odds_anchor():
    # 文档 11.1:max(BVPS × 历史最低 PB, 事件前价格),再与 BVPS × 0.8 取更低
    a = odds.quick_anchor(floor_price=7.8, bvps=8.67, pre_event=None)
    assert a == pytest.approx(min(7.8, 8.67 * 0.8))
    a2 = odds.quick_anchor(floor_price=7.0, bvps=8.67, pre_event=8.0)
    assert a2 == pytest.approx(min(8.0, 6.936))
    up, down, q = odds.quick_odds(14.4, 10.0, a)
    assert q == pytest.approx(up / down)
    # 现价低于下行锚时,下行取下限而不是负数或 0
    _, down2, _ = odds.quick_odds(14.4, 6.0, 7.0, min_down=0.1)
    assert down2 == 0.1


def test_risk_budget_and_gap():
    assert risk.risk_budget("standard", False, False, 100, CFG) == pytest.approx(0.01)
    assert risk.risk_budget("high", True, False, 100, CFG) == pytest.approx(0.0075)
    assert risk.risk_budget("standard", False, True, 10, CFG) == pytest.approx(0.0025)
    assert risk.risk_budget("none", False, False, 100, CFG) == 0
    assert risk.gap_buffer("主板", False, False, CFG) == 0.05
    assert risk.gap_buffer("主板", True, False, CFG) == 0.10
    assert risk.gap_buffer("创业板", False, False, CFG) == 0.10
    assert risk.gap_buffer("主板", False, True, CFG) == 0.15
    assert risk.gap_buffer("北交所", False, False, CFG) == 0.15
    # 1/4 凯利:p=35%, R=4 → f*≈18.75%,四分之一约 4.7%
    assert risk.kelly_cap(0.35, 4) == pytest.approx(0.046875, abs=1e-6)


def test_breaker_levels():
    b = risk.Breaker()
    assert b.update(100, 0, CFG) == (0, 0, 0.0)
    assert b.update(91, 1, CFG)[:2] == (1, 1)      # -9% → 一级
    assert b.update(90, 2, CFG)[1] == 0             # 同级别不重复触发
    assert b.update(87, 3, CFG)[:2] == (2, 2)      # -13% → 二级
    assert b.update(80, 4, CFG)[:2] == (3, 3)      # -20% → 三级,进入冷静期
    assert not b.entries_allowed(5)
    b.update(101, 6, CFG)                           # 创新高重置
    assert b.level == 0 and b.entries_allowed(6 + CFG.freeze_days + 1)


def test_score_card_tiers():
    hi = odds.score_card(odds.ScoreInputs(edge=0.5, odds=4.5, expected=0.6, floor_kind="hard",
                                          catalyst_months=6, falsify_count=2, amount20_yi=2, ret60=0.05,
                                          evidence_ab=2))
    assert hi["total"] == 100
    lo = odds.score_card(odds.ScoreInputs(edge=0.05, odds=1.5, expected=0.1, floor_kind="none",
                                          catalyst_months=None, falsify_count=0, amount20_yi=0.1, ret60=0.8,
                                          evidence_ab=0))
    assert lo["total"] == 5 + 0 + 3
    assert odds.conviction(85, CFG) == "high" and odds.conviction(70, CFG) == "standard"
    assert odds.conviction(65, CFG) == "low" and odds.conviction(50, CFG) == "none"


def test_stage_probability():
    assert odds.stage_prob("restructure_approved") > odds.stage_prob("restructure_draft") > odds.stage_prob("restructure_plan")
    assert odds.stage_prob("restructure_draft", 2.0) == 0.95
    assert odds.family_of("ctrl_change") == "ctrl" and odds.family_of("holder_inc") is None
