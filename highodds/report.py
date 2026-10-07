"""回测后的观察报告:把 ho_*.parquet 汇总成一个自包含的 HTML。

    python -m highodds.report              # 最近一次高赔率策略回测
    python -m highodds.report <run_id>     # 指定回测
输出:data/lake/backtests/<run_id>/observe.html

内容对应文档第 18 节的统计:漏斗转化、按通道 / 母题 / 仓位层 / 退出原因的归因、概率校准、
赔率卡、回撤熔断、期末弹药池。样本只有几十笔时这些数字只能看方向,不能据此改规则(文档 19 节)。
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from data import config

BT = config.LAKE_DIR / "backtests"
CSS = """body{font:14px/1.6 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif;margin:28px auto;max-width:1180px;
color:#1f2a37;background:#eef1f5;padding:0 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px;
border-left:4px solid #2b4c7e;padding-left:8px}table{border-collapse:collapse;background:#fff;width:100%;margin:6px 0 12px;
font-size:12.5px}th{background:#1f3557;color:#fff;text-align:left;padding:5px 8px;font-weight:500}
td{padding:4px 8px;border-bottom:1px solid #e3e8ef}tr:nth-child(even) td{background:#f8fafc}.up{color:#d9363e}.dn{color:#1e9e5a}
.note{background:#fff8e1;border:1px solid #f0d58c;padding:8px 12px;margin:8px 0;font-size:13px}.kv span{display:inline-block;
margin:0 22px 6px 0}.kv b{font-size:17px}small{color:#6b7684}"""


def _f(df: pd.DataFrame, pct=(), num=(), cur=()) -> str:
    d = df.copy()
    for c in pct:
        if c in d:
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else f"{v:.1%}")
    for c in num:
        if c in d:
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else f"{v:,.2f}")
    for c in cur:
        if c in d:
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else f"{v:,.0f}")
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].dt.strftime("%Y-%m-%d").fillna("")
    return d.to_html(index=False, escape=True, na_rep="", border=0)


def _attr(t: pd.DataFrame, key: str) -> pd.DataFrame:
    g = t.groupby(key, dropna=False)
    out = g.apply(lambda x: pd.Series({
        "笔数": len(x), "胜率": (x["ret"] > 0).mean(), "平均收益": x["ret"].mean(),
        "盈亏比": (x.loc[x.ret > 0, "ret"].mean() / -x.loc[x.ret < 0, "ret"].mean())
        if (x.ret > 0).any() and (x.ret < 0).any() else np.nan,
        "合计盈亏": x["pnl"].sum(), "平均持有(天)": x["days"].mean()}), include_groups=False).reset_index()
    return out.sort_values("合计盈亏")


def latest_run() -> str:
    runs = sorted(p.name for p in BT.glob("*-high_odds_low_freq*") if (p / "ho_trades.parquet").exists())
    if not runs:
        raise SystemExit("没有找到高赔率策略的回测结果,先跑一次回测")
    return runs[-1]


def build(run_id: str | None = None) -> Path:
    run_id = run_id or latest_run()
    d = BT / run_id
    rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
    m = rep["metrics"]
    rd = lambda n: pd.read_parquet(d / f"{n}.parquet") if (d / f"{n}.parquet").exists() else pd.DataFrame()
    trades, pool, cards, sigs, week, brk, final = (rd(n) for n in (
        "ho_trades", "ho_pool_events", "ho_cards", "ho_signals", "ho_weekly", "ho_breaker", "ho_pools_final"))
    eq = rd("equity")
    parts = [f"<h1>高赔率低频基本面策略:回测观察报告</h1><small>{html.escape(run_id)} · "
             f"{rep['settings']['start']} ~ {rep['settings']['end']}</small>"]

    k = [("总收益", m.get("total_return")), ("年化", m.get("annual_return")), ("最大回撤", m.get("max_drawdown")),
         ("夏普", m.get("sharpe")), ("相对基准超额", m.get("excess_return"))]
    kv = "".join(f"<span>{n}<br><b>{'' if v is None else (f'{v:.2f}' if n == '夏普' else f'{v:.1%}')}</b></span>" for n, v in k)
    if len(eq):
        kv += (f"<span>平均持仓数<br><b>{eq['positions'].mean():.1f}</b></span>"
               f"<span>平均仓位<br><b>{(eq['market_value'] / eq['equity']).mean():.0%}</b></span>")
        if "benchmark" in eq:
            kv += f"<span>基准(全市场等权)<br><b>{eq['benchmark'].iloc[-1] / eq['benchmark'].iloc[0] - 1:.1%}</b></span>"
    parts.append(f'<div class="kv">{kv}</div>')
    parts.append('<div class="note">这是框架的第一版默认参数结果,不是优化后的结果。赔率卡的估值全部由程序按固定规则生成'
                 '(PB 法为主),事件成功价用固定涨幅代替,没有人工深研。样本只有几十笔,数字只能看方向。</div>')

    if len(pool):
        parts.append("<h2>1. 池子漏斗(雷达 → 观察 → 弹药 → 持仓)</h2>")
        f = pool.assign(**{"from": pool["from"].replace("", "新信号"), "to": pool["to"].replace("", "删除")})
        flow = f.groupby(["from", "to"]).size().rename("次数").reset_index().sort_values("次数", ascending=False)
        parts.append(_f(flow))
        why = pool[(pool["to"] == "") | (pool["from"] == pool["to"])]
        why = why.assign(原因=why["reason"].astype(str).str.replace(r"[\d\.]+%?", "#", regex=True).str[:46])
        parts.append("<p><small>被拦下或删除的主要原因</small></p>" +
                     _f(why.groupby(["from", "原因"]).size().rename("次数").reset_index()
                        .sort_values("次数", ascending=False).head(14)))
    if len(week):
        parts.append("<h2>2. 池子规模(每周)</h2>")
        w = week.copy()
        w["cash_pct"], w["drawdown"] = w["cash_pct"], w["drawdown"]
        parts.append(_f(w[["date", "radar", "watch", "ammo", "positions", "cash_pct", "breaker", "drawdown"]]
                        .iloc[::max(1, len(w) // 20)], pct=["cash_pct", "drawdown"]))
    if len(trades):
        done = trades[trades["close_date"].notna()]
        parts.append(f"<h2>3. 交易归因(共 {len(trades)} 笔,已平仓 {len(done)} 笔,持有中 {len(trades) - len(done)} 笔)</h2>")
        for key, label in (("channel", "按筛选通道"), ("motif", "按母题"), ("bucket", "按仓位层"),
                           ("family", "按事件类型"), ("exit_reason", "按退出原因")):
            t = trades.assign(**{key: trades[key].replace("", "无事件")})
            if key == "exit_reason":
                t[key] = t[key].str.replace(r"[\d\.]+", "#", regex=True)
            parts.append(f"<p><small>{label}</small></p>" + _f(_attr(t, key), pct=["胜率", "平均收益"],
                                                                  num=["盈亏比", "平均持有(天)"], cur=["合计盈亏"]))
        ev = trades[trades["bucket"].isin(["event", "option"]) | (trades["family"] != "")]
        if len(ev) >= 3:
            parts.append("<h2>4. 概率校准(事件类)</h2>")
            cal = pd.DataFrame({"笔数": [len(ev)], "预测成功概率(均值)": [ev["p_success"].mean()],
                                "实际盈利比例": [(ev["ret"] > 0).mean()]})
            parts.append(_f(cal, pct=["预测成功概率(均值)", "实际盈利比例"]) +
                         "<small>“盈利”不等于“事件成功”,只是最接近的可观察代理。文档要求偏差 &lt; 10 个百分点,样本 &lt; 30 笔时只看方向。</small>")
        parts.append("<h2>5. 全部交易</h2>")
        cols = ["symbol", "name", "channel", "motif", "bucket", "signal_date", "open_date", "close_date", "score",
                "odds", "expected", "downside", "exit_reason", "ret", "pnl", "days"]
        parts.append(_f(trades[[c for c in cols if c in trades]].sort_values("signal_date"),
                        pct=["expected", "downside", "ret"], cur=["pnl"], num=["odds"]))
    if len(cards):
        o = cards[cards["event"] == "open"]
        parts.append(f"<h2>6. 建仓时的赔率卡({len(o)} 张)</h2>")
        if len(o):
            stat = pd.DataFrame({"下行恰好等于下限的比例": [(o["downside"] <= 0.1001).mean()],
                                 "平均结构赔率": [o["odds"].mean()], "平均期望收益": [o["expected"].mean()],
                                 "平均下行": [o["downside"].mean()]})
            parts.append(_f(stat, pct=["下行恰好等于下限的比例", "平均期望收益", "平均下行"], num=["平均结构赔率"]) +
                         "<small>下行等于下限,说明现价已经低于下行锚,证伪价是被 min_downside 托住的,而不是估出来的,"
                         "这类“假赔率”更容易被正常波动打掉(文档 1.3)。</small>")
        parts.append(_f(o[["date", "symbol", "name", "channel", "kind", "tag", "price", "bear", "base", "limit", "odds",
                           "expected", "downside", "score", "conviction", "thesis"]],
                        pct=["expected", "downside"], num=["price", "bear", "base", "limit", "odds"]))
    if len(brk):
        parts.append("<h2>7. 回撤熔断记录</h2>" + _f(brk, pct=["drawdown"], cur=["equity"]))
    if len(final):
        parts.append("<h2>8. 期末弹药池</h2>" + _f(final[["symbol", "name", "channel", "motif", "price", "limit", "odds",
                                                           "expected", "score", "thesis"]],
                                                   pct=["expected"], num=["price", "limit", "odds"]))
    if len(sigs):
        s = sigs.groupby(["channel", "sub"]).agg(信号数=("symbol", "size"), 股票数=("symbol", "nunique")).reset_index()
        parts.append("<h2>9. 各通道触发的信号(进入雷达池的)</h2>" + _f(s))
    out = d / "observe.html"
    out.write_text(f"<!doctype html><meta charset='utf-8'><title>{html.escape(run_id)}</title>"
                   f"<style>{CSS}</style>" + "\n".join(parts), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else None))
