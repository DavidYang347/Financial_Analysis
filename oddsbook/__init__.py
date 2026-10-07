"""oddsbook: the 高赔率 · 低频 · 基本面 v3 playbook as code.

    config.py    every tunable number of the playbook (Param list, defaults = 附录 G)
    features.py  point-in-time cross-section: price, liquidity, fundamentals, valuation history
    veto.py      一票否决 (hard -> excluded, soft -> option position only)
    channels.py  the six screening channels A-F -> radar signals with evidence grades
    odds.py      8-step odds card: three scenarios, anchors, probabilities, buy line, sensitivity
    score.py     附录 E scorecard + 入场检查清单
    book.py      four pools, sizing, tranches, exits, portfolio limits, drawdown breaker
    journal.py   what gets exported for observation (pools, cards, journal, channel stats)

The strategy script ``strategy/strategies/high_odds_v3.py`` wires these into
the backtest engine; the "高赔率观察" page shows the exported tables.
"""
