"""Fill data placeholders in a report draft with tables built from the statistics.

    python -m review render monthly 2026-06

Draft:  review/reports_src/{monthly,weekly}/<label>.md   (narrative written per the SOP, with placeholders)
Output: materials/复盘报告/{月度复盘,周度复盘}/<label>.md

Placeholders: ``{{t:<name>}}`` inserts a table (see TABLES), ``{{v:<path>}}`` a single value from the
statistics JSON (dotted path, e.g. ``{{v:breadth.median|pct}}``). Tables are generated so every number
in them comes straight from review.stats; the narrative around them is written by hand.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from review import store

SRC = Path(__file__).resolve().parent / "reports_src"


def pct(x, nd=1, sign=True) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "–"
    return f"{x * 100:+.{nd}f}%" if sign else f"{x * 100:.{nd}f}%"


def yi(x, nd=0) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "–"
    return f"{x / 1e8:,.{nd}f}"


def num(x, nd=2) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "–"
    return f"{x:,.{nd}f}"


def esc(s) -> str:
    return str(s or "").replace("|", "／").replace("\n", " ").strip()


def short(s, n=60) -> str:
    s = esc(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _src(web: dict | None) -> str:
    if not web:
        return ""
    for x in web.get("sources") or []:
        if x.get("url"):
            title = short(x.get("title") or "来源", 18)
            return f"[{title}]({x['url']})"
    return ""


def catalyst_cell(r: dict, n=70) -> str:
    w = r.get("web")
    if w and w.get("catalyst"):
        d = w.get("catalyst_date") or ""
        return f"{short(w['catalyst'], n)}{('（' + d + '）') if d else ''} {_src(w)}".strip()
    c = r.get("catalyst") or {}
    ev = c.get("events") or []
    if ev:
        e = ev[0]
        return f"公告：{short(e['title'], 40)}（{e['date']}，巨潮）；未联网核实"
    fc = c.get("forecast")
    if fc:
        return f"业绩预告：{fc['kind']}（{fc['date']}）；未联网核实"
    return "未逐股核实（公告库无相关公告）"


def driver_cell(r: dict) -> str:
    w = r.get("web") or {}
    return esc(w.get("driver") or r.get("driver_hint") or "未核实")


def theme_cell(r: dict) -> str:
    w = r.get("web") or {}
    if w.get("theme"):
        return short(w["theme"], 16)
    cs = r.get("concepts") or []
    return short("、".join(cs[:2]), 16) + "（概念板块）" if cs else "–"


# ---- tables ---------------------------------------------------------------------
def t_indices(s):
    rows = ["| 指数 | 区间涨跌 | 年初至今 | 收盘 | 区间振幅 | 近250日位置 | 收盘 vs MA60 / MA120 | 年内新高 |",
            "|---|---|---|---|---|---|---|---|"]
    for i in s["indices"]:
        pos = "–" if i.get("pos_250") is None else f"{i['pos_250'] * 100:.0f}%"
        ma = ("上" if i["close"] > i["ma60"] else "下") + " / " + ("上" if i["close"] > i["ma120"] else "下")
        rows.append(f"| {i['index']} | {pct(i['ret'])} | {pct(i['ytd'])} | {num(i['close'])} | {pct(i['amplitude'], sign=False)} "
                    f"| {pos} | {ma} | {'是' if i['new_year_high'] else '否'} |")
    return "\n".join(rows)


def t_valuation(s):
    rows = ["| 指数 | 滚动 PE | 近 10 年分位 | 10 年区间 | 股债性价比（1/PE − 10Y 国债） |", "|---|---|---|---|---|"]
    for v in sorted(s["valuation"], key=lambda x: ["沪深300", "中证500", "中证1000", "中证2000"].index(x["index"])
                    if x["index"] in ["沪深300", "中证500", "中证1000", "中证2000"] else 9):
        erp = "–" if v.get("erp") is None else f"{v['erp']:.2f}%（10Y {v['cgb10']:.3f}%）"
        rows.append(f"| {v['index']} | {v['pe']:.2f} | {v['pe_pct_10y'] * 100:.0f}% | {v['pe_min_10y']:.1f}–{v['pe_max_10y']:.1f} | {erp} |")
    return "\n".join(rows)


def t_breadth(s):
    b, v, st = s["breadth"], s["volume"], s["style"]
    bm = st["board_median"]
    cm = st["cap_median"]
    lines = [
        "| 指标 | 数值 |", "|---|---|",
        f"| 个股区间涨跌幅中位数 / 均值 | {pct(b['median'])} / {pct(b['mean'])} |",
        f"| 上涨家数占比 | {pct(b['up_share'], sign=False)}（样本 {b['n']} 只，剔除 ST 和新股前的全部有效个股） |",
        f"| 涨幅 > 20% / 跌幅 > 20% | {b['gt20']} / {b['lt_m20']} |",
        f"| 涨幅 > 50% | {b['gt50']} |",
        f"| 区间收盘翻倍 / 区间低点到高点翻倍 | {b['doubled_cc']} / {b['doubled_l2h']}（另有新股 {b.get('doubled_l2h_new', 0)}、退市整理股 {b.get('doubled_l2h_delisting', 0)} 只单列） |",
        f"| 期末创 60 日新高个股 | {b['new_high_60']} |",
        f"| 涨停 / 跌停（个股·日，含 ST） | {b['limit_up_total']} / {b['limit_down_total']} |",
        f"| 日均成交额 | {yi(v['avg_daily'])} 亿元（上期 {yi(v['prev_avg_daily'])} 亿，环比 {pct(v['chg'])}；处近一年 {v['pct_1y'] * 100:.0f}% 分位） |",
        f"| 单日最高成交 | {yi(v['max_amount'])} 亿元（{v['max_day']}） |",
        f"| 板块涨跌幅中位数 | 主板 {pct(bm.get('主板'))}，创业板 {pct(bm.get('创业板'))}，科创板 {pct(bm.get('科创板'))}，北交所 {pct(bm.get('北交所'))} |",
        f"| 流通市值分组中位数 | <30亿 {pct(cm.get('<30亿'))}，30–100亿 {pct(cm.get('30–100亿'))}，100–500亿 {pct(cm.get('100–500亿'))}，>500亿 {pct(cm.get('>500亿'))} |",
    ]
    m = s.get("margin") or {}
    if m:
        lines.append(f"| 两融余额（沪深） | 期末 {yi(m['end'])} 亿元，较上期末 {yi(m['change'])} 亿元 |")
    return "\n".join(lines)


def t_macro(s):
    m = s.get("macro") or {}
    out = ["| 指标 | 截至月末已公布的最近几期 |", "|---|---|"]

    def ser(key, f):
        return "；".join(f(x) for x in m.get(key, []))
    out.append("| 制造业 / 非制造业 PMI | " + ser("pmi", lambda x: f"{x['month'][5:]}月 {x['mfg']:.1f} / {x['non_mfg']:.1f}") + " |")
    out.append(f"| CPI 同比 | {ser('cpi_yoy', lambda x: x['month'][5:] + '月 ' + format(x['yoy'], '.1f') + '%')} |")
    out.append(f"| PPI 同比 | {ser('ppi_yoy', lambda x: x['month'][5:] + '月 ' + format(x['yoy'], '.1f') + '%')} |")
    out.append(f"| M2 / M1 同比 | {ser('money', lambda x: x['month'][5:] + '月 ' + format(x['m2_yoy'], '.1f') + '% / ' + format(x['m1_yoy'], '.1f') + '%')} |")
    if m.get("cgb10"):
        c = m["cgb10"][-1]
        out.append(f"| 10 年国债收益率 | {c['date']} {c['y10']:.3f}%（月内变动 {m.get('cgb10_chg_bp', 0):+.1f}bp） |")
    return "\n".join(out)


def t_emotion_weeks(s):
    rows = ["| 周 | 交易日 | 日均涨停（不含ST） | 日均跌停（不含ST） | 最高连板 | 个股周涨幅中位数 | 情绪阶段（多数日） | 周涨幅前 3 |",
            "|---|---|---|---|---|---|---|---|"]
    for w in s["weeks"]:
        top = "、".join(f"{x['name']} {pct(x['wret'], 0)}" for x in w["top"][:3])
        rows.append(f"| {w['week']} | {w['days']} | {w['limit_up_avg']:.0f} | {w['limit_down_avg']:.0f} | {w['max_streak']} "
                    f"| {pct(w['median_ret'])} | {w['stage']} | {top} |")
    return "\n".join(rows)


def t_emotion_days(s):
    rows = ["| 日期 | 涨停（不含 ST） | 跌停（不含 ST） | 最高连板 | 炸板 | 昨日涨停今日均涨 | 上涨占比 | 成交额（亿） | 阶段 |",
            "|---|---|---|---|---|---|---|---|---|"]
    for e in s["emotion"]:
        zb = "–" if e.get("broken") is None else str(e["broken"])
        rows.append(f"| {e['date'][5:]} | {e['limit_up']}（{e['limit_up_ex_st']}） | {e['limit_down']}（{e['limit_down_ex_st']}） "
                    f"| {e['max_streak']} | {zb} | {pct(e.get('premium'))} | {pct(e['up_ratio'], 0, False)} | {yi(e['amount'])} | {e['stage']} |")
    return "\n".join(rows)


def t_gainers(s, n=50):
    rows = ["| 排名 | 代码 | 名称 | 月涨幅 | 涨停数 | 月初流通市值(亿) | 申万一级 | 所属题材 | 驱动类型 | 核心催化（日期 / 来源） | 走势形态 |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in s["gainers"][:n]:
        rows.append(f"| {r['rank']} | {r['symbol']} | {esc(r['name'])} | {pct(r['ret'])} | {r['limit_up_days']} | {yi(r['float_cap0'])} "
                    f"| {esc(r.get('sw') or '–')} | {theme_cell(r)} | {driver_cell(r)} | {catalyst_cell(r)} | {esc(r.get('shape') or '–')} |")
    return "\n".join(rows)


def t_doubled(s):
    rows = ["| 代码 | 名称 | 低点→高点 | 月涨幅 | 起涨（月内低点） | 高点 | 月初流通市值(亿) | 起涨前位置 | 量能放大 | 题材 | 驱动 | 催化 | 监管关注 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in s["doubled_l2h"]:
        w = r.get("web") or {}
        pos = "–" if r.get("pos_250_before") is None else f"{r['pos_250_before'] * 100:.0f}%"
        vr = "–" if r.get("vol_ratio") is None else f"{r['vol_ratio']:.1f}×"
        rows.append(f"| {r['symbol']} | {esc(r['name'])} | {pct(r['l2h'])} | {pct(r['ret'])} | {r['low_date'][5:]} | {r['high_date'][5:]} "
                    f"| {yi(r['float_cap0'])} | {pos} | {vr} | {theme_cell(r)} | {driver_cell(r)} | {catalyst_cell(r, 55)} "
                    f"| {short(w.get('regulatory') or '–', 30)} |")
    return "\n".join(rows)


def t_doubled_side(s):
    parts = []
    if s.get("doubled_new"):
        parts.append("区间内上市的新股（首日不设涨跌幅，单列不计入翻倍统计）：" +
                     "、".join(f"{r['name']}（{pct(r['l2h'], 0)}）" for r in s["doubled_new"]))
    if s.get("doubled_delist"):
        parts.append("退市整理期个股（首日先暴跌再日内反弹，非上涨行情）：" +
                     "、".join(f"{r['name']}（{pct(r['l2h'], 0)}）" for r in s["doubled_delist"]))
    return "\n\n".join(parts) if parts else "无。"


def t_ytd(s):
    y = s.get("ytd_doubled") or {}
    if not y:
        return "数据缺失。"
    new = [x for x in y["items"] if x.get("new")]
    lines = [f"年初至今翻倍（{s['end']} 收盘相对 {s['end'][:4]} 年初）：**{y['count']} 只**，本月新增 🆕 {y['new']} 只，本月跌出 {len(y['dropped'])} 只。",
             "",
             "年内涨幅前 20：" + "、".join(f"{x['name']} {pct(x['ytd'], 0)}{' 🆕' if x.get('new') else ''}" for x in y["items"][:20]),
             "",
             "本月新增（前 20）：" + ("、".join(f"{x['name']} {pct(x['ytd'], 0)}" for x in new[:20]) or "无"),
             "",
             "本月跌出（前 20）：" + ("、".join(f"{x['name']}（{pct(x['ytd'], 0)}）" for x in y["dropped"][:20]) or "无")]
    return "\n".join(lines)


def t_profile(s):
    p = s["profile"]

    def kv(d, n=8):
        return "，".join(f"{k} {v}" for k, v in list(d.items())[:n])
    g = s.get("genes") or {}
    rows = ["| 维度 | 涨幅前 50 分布 | 月内翻倍股分布 |", "|---|---|---|",
            f"| 驱动类型 | {kv(p['driver'])} | {kv(g.get('driver', {}))} |",
            f"| 流通市值 | {kv(p['cap'])} | {kv(g.get('cap', {}))} |",
            f"| 上市板块 | {kv(p['board'])} | {kv(g.get('board', {}))} |",
            f"| 申万一级 | {kv(p['sw'], 6)} | {kv(g.get('sw', {}), 6)} |",
            f"| 走势形态 | {kv(p['shape'])} | {kv(g.get('shape', {}))} |"]
    return "\n".join(rows)


def t_genes(s):
    g = s.get("genes") or {}
    if not g:
        return "本月无翻倍股。"
    h = g.get("holders") or {}
    rows = ["| 指标 | 本月翻倍股 |", "|---|---|",
            f"| 数量 | {g['n']} |",
            f"| 月初流通市值中位数 | {yi(g['median_cap0'])} 亿元 |",
            f"| 起涨前股价在近 250 日区间的位置（中位数） | {g['median_pos_250_before'] * 100:.0f}%（低于 30% 的占 {g['low_pos_share'] * 100:.0f}%） |",
            f"| 月内日均成交额 / 此前 60 日均值（中位数） | {g['median_vol_ratio']:.1f} 倍 |",
            f"| ST 占比 | {g['st_share'] * 100:.0f}% |"]
    if h:
        rows.append(f"| 股东户数变化（{h['note'].split('（')[0].replace('股东户数 ', '')}） | 中位数 {h['median_chg']:+.1f}%，下降的占 {h['fell_share'] * 100:.0f}%（{h['n']} 只有数据） |")
    return "\n".join(rows)


def t_losers(s, n=30):
    rows = ["| 名称 | 代码 | 月涨幅 | 跌停数 | 月初流通市值(亿) | 申万一级 | 公告线索（本地公告库） |", "|---|---|---|---|---|---|---|"]
    for r in s["losers"][:n]:
        c = r.get("catalyst") or {}
        ev = c.get("events") or []
        hint = "；".join(f"{e['date'][5:]} {short(e['title'], 30)}" for e in ev[:2])
        if c.get("forecast"):
            hint += ("；" if hint else "") + f"预告{c['forecast']['kind']}"
        rows.append(f"| {esc(r['name'])} | {r['symbol']} | {pct(r['ret'])} | {r['limit_down_days']} | {yi(r['float_cap0'])} "
                    f"| {esc(r.get('sw') or '–')} | {hint or '无相关公告'} |")
    return "\n".join(rows)


def t_industries(s):
    rows = ["| 排名 | 申万一级 | 本月 | 上月 | 成交额占比 | 占比变化 | 收盘在 60 日线上 |", "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(s["industries"], 1):
        sc = "–" if r.get("share_chg") is None else f"{r['share_chg'] * 100:+.2f}pp"
        rows.append(f"| {i} | {r['industry']} | {pct(r['ret'])} | {pct(r.get('prev_ret'))} | {pct(r.get('amount_share'), 1, False)} | {sc} "
                    f"| {'是' if r['above_ma60'] else '否'} |")
    return "\n".join(rows)


def t_concepts(s, n=12):
    rows = ["| 概念板块 | 成分 | 涨幅中位数 | 上涨占比 | 涨停次数 | 活跃周数 | 龙头（最大涨幅） | 中军（上涨股中流通市值最大） | 周度中位数 |",
            "|---|---|---|---|---|---|---|---|---|"]
    for c in s["concepts"][:n]:
        wk = " → ".join(pct(x, 0) for x in c["weekly_median"])
        rows.append(f"| {c['concept']} | {c['n']} | {pct(c['median_ret'])} | {pct(c['up_share'], 0, False)} | {c['limit_up_days']} "
                    f"| {c['active_weeks']} | {c['leader_name']} {pct(c['leader_ret'], 0)} | {c['core_name'] or '–'} {pct(c['core_ret'], 0)} | {wk} |")
    rows.append("")
    rows.append("跌幅最大的概念：" + "、".join(f"{c['concept']}（{pct(c['median_ret'], 0)}）" for c in s["concepts"][-6:]))
    return "\n".join(rows)


def t_modes(s):
    rows = ["| 模式 | 本月量化指标 | 口径 | 代表个股 |", "|---|---|---|---|"]
    for m in s["modes"]:
        v = m["value"]
        vs = "–" if v is None else (pct(v) if abs(v) < 5 else str(v))
        rows.append(f"| {m['mode']} | {vs} | {m['evidence']} | {'、'.join(m['examples']) or '–'} |")
    return "\n".join(rows)


def t_ma(s):
    rows = ["| 公告类型 | 样本 | 次日平均涨幅 | 5 日平均涨幅 | 次日上涨占比 |", "|---|---|---|---|---|"]
    for k, v in s["ma_reaction"].items():
        rows.append(f"| {k} | {v['n']} | {pct(v['next_day'])} | {pct(v['day5'])} | {pct(v['up_share'], 0, False)} |")
    return "\n".join(rows)


def t_events(s):
    names = {"ma_plan": "筹划重组/预案", "ma_draft": "重组草案", "ma_progress": "重组进展", "ma_approved": "重组获批/注册",
             "ma_closed": "重组完成/过户", "ma_terminated": "重组终止", "control_change": "控制权变更", "control_terminated": "控制权变更终止",
             "stake_transfer": "协议转让", "placement": "定增预案", "buyback_plan": "回购方案", "buyback_cancel": "回购注销",
             "insider_buy": "增持", "insider_sell": "减持预披露", "incentive": "股权激励/员工持股", "investigation": "立案调查/处罚",
             "delist_risk": "退市风险提示", "delist_final": "终止上市", "st_imposed": "新实施风险警示", "st_removed": "撤销风险警示",
             "bankruptcy": "破产重整", "frozen": "控股股东冻结/拍卖", "impairment": "计提减值", "audit_adverse": "非标审计意见"}
    keep = [e for e in s["events"] if e["event"] in names]
    rows = ["| 公告类型 | 公告数 | 涉及公司 |", "|---|---|---|"]
    for e in keep:
        rows.append(f"| {names[e['event']]} | {e['n']} | {e['stocks']} |")
    return "\n".join(rows)


def t_risk(s, n=30):
    names = {"investigation": "立案调查/处罚", "delist_risk": "退市风险", "delist_final": "终止上市", "st_imposed": "新实施风险警示",
             "audit_adverse": "非标审计意见", "funds_occupied": "资金占用"}
    rows = ["| 日期 | 公司 | 类型 | 公告标题 |", "|---|---|---|---|"]
    for r in s["risk"][:n]:
        rows.append(f"| {r['date'][5:]} | {esc(r['name'])} | {names.get(r['event'], r['event'])} | {short(r['title'], 50)} |")
    return "\n".join(rows)


def t_side(s):
    st = "、".join(f"{r['name']} {pct(r['ret'], 0)}" for r in s["side_st"])
    nw = "、".join(f"{r['name']} {pct(r['ret'], 0)}" for r in s["side_new"])
    return f"- ST 涨幅前 10：{st or '无'}\n- 次新（上市不足 60 个交易日）涨幅前 10：{nw or '无'}"


def t_supply(s):
    x = s.get("supply") or {}
    ins, ul, ipo = x.get("insider") or {}, x.get("unlock") or {}, x.get("ipo") or {}
    rows = ["| 项目 | 本月 |", "|---|---|"]
    if ins:
        rows.append(f"| 产业资本增持（不含员工持股 / 回购专户） | {yi(ins['buy'], 1)} 亿元，{ins['buy_n']} 家 |")
        rows.append(f"| 产业资本减持 | {yi(ins['sell'], 1)} 亿元，{ins['sell_n']} 家 |")
        rows.append(f"| 增减持净额 | {yi(ins['net'], 1)} 亿元 |")
    if ul:
        top = "、".join(f"{t['name']}（{t['date'][5:]}，{yi(t['value'])} 亿）" for t in ul["top"][:3])
        rows.append(f"| 限售股解禁市值 | {yi(ul['value'])} 亿元，{ul['n']} 家；最大：{top} |")
    if ipo:
        rows.append(f"| 新股上市 | {ipo['n']} 只 |")
    return "\n".join(rows) + "\n\n口径：增减持来自东方财富股东增减持明细（公告日在本月），金额 = 变动股数 × 公告日收盘价，为估算；解禁市值为东方财富口径。"


TABLES = {"supply": t_supply, "indices": t_indices, "valuation": t_valuation, "breadth": t_breadth, "macro": t_macro,
          "emotion_weeks": t_emotion_weeks, "emotion_days": t_emotion_days, "gainers": t_gainers, "doubled": t_doubled,
          "doubled_side": t_doubled_side, "ytd": t_ytd, "profile": t_profile, "genes": t_genes, "losers": t_losers,
          "industries": t_industries, "concepts": t_concepts, "modes": t_modes, "ma": t_ma, "events": t_events,
          "risk": t_risk, "side": t_side}


def _value(s: dict, path: str) -> str:
    fmt = None
    if "|" in path:
        path, fmt = path.split("|", 1)
    v = s
    for k in path.split("."):
        v = v[int(k)] if isinstance(v, list) else v.get(k)
    if fmt == "pct":
        return pct(v)
    if fmt == "pct0":
        return pct(v, 0, False)
    if fmt == "yi":
        return yi(v)
    return str(v)


def render(kind: str, label: str) -> Path:
    src = SRC / kind / f"{label}.md"
    stats = store.load_stats(kind, label)
    if stats is None:
        raise SystemExit(f"先运行 python -m review {kind} {label} 生成统计")
    text = src.read_text(encoding="utf-8")
    text = re.sub(r"\{\{t:([a-z_]+)\}\}", lambda m: TABLES[m.group(1)](stats), text)
    text = re.sub(r"\{\{v:([^}]+)\}\}", lambda m: _value(stats, m.group(1)), text)
    out = store.report_path(kind, label)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out
