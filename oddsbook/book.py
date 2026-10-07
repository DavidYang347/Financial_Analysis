"""The playbook as a daily state machine: four pools, odds cards, sizing, exits and the breaker.

Rhythm (v3 Part D, mapped to a backtest that wakes up after every close):

    weekly  (last trading day of the week)   scan the six channels -> radar pool,
            5-minute elimination -> watch pool, deep research (full odds cards)
            for the best few -> ammo pool, recompute the odds of ammo cards,
            refresh the cards of holdings when new reports / events arrived
    monthly (last trading day of the month)  audit: current odds of every holding
            (< 2 reduce, < 1 exit), time stops, pool expiry
    daily                                    announcements of holdings (falsify / verify),
            price alerts of ammo cards (buy line), take-profit / trailing stops,
            breaker, then build the next day's target weights

Decisions on day t are filled by the engine at the next open (T+1, lots,
limits). A card written on day t can be acted on from day t+1 on (冷静期 ≥ 24h).

Everything that is decided is written to ``self.journal`` with the reason, and
pool sizes / cards / audits are kept for the observation tables (journal.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from fundlab.store import FundStore
from oddsbook import channels as chn
from oddsbook import features as feat
from oddsbook import odds as od
from oddsbook import score as sc
from oddsbook import veto as vt
from oddsbook.config import floats

# Events that make a holding's thesis worse. "confirmed" ones end it outright.
CONFIRMED_FALSIFY = {"ma": {"ma_terminated"}, "control": {"control_terminated"}, "bankruptcy": {"delist_final"}}
ALWAYS_CONFIRMED = {"investigation", "audit_adverse", "delist_final"}
SOFT_FALSIFY = {"frozen", "debt_default", "funds_occupied", "delist_risk", "st_imposed"}
# Events that count as new supporting evidence (verification add, time-stop reset).
POSITIVE = {"ma_approved", "ma_draft", "ma_progress", "st_removed", "audit_cleared", "insider_buy", "buyback_cancel",
            "placement_major", "bankruptcy", "debt_restructure"}
BAD_FORECAST = {"首亏", "增亏", "续亏", "预减"}
GOOD_FORECAST = {"扭亏", "减亏", "预增", "略增", "续盈"}


@dataclass
class Holding:
    symbol: str
    card: dict
    weight_full: float          # full target weight (all tranches) from the sizing formula at entry
    w_target: float = 0.0       # weight the book wants now (0 = exit)
    tranche: int = 1            # tranches bought so far
    opened: pd.Timestamp | None = None
    last_add: pd.Timestamp | None = None
    status: str = "pending"     # pending (order sent) | open
    pending_since: pd.Timestamp | None = None
    dirty: bool = True          # target changed since the last order
    flags: set = field(default_factory=set)
    peak_h: float = 0.0
    reassess_on: pd.Timestamp | None = None
    last_evidence: pd.Timestamp | None = None
    entry_price_h: float = math.nan
    tier: str = "standard"



class Book:
    def __init__(self, params: dict, md, fs: FundStore | None = None) -> None:
        self.p = params
        self.md = md
        self.fs = fs or FundStore()
        self.tranches = floats(params["tranches"]) or [0.4, 0.3, 0.3]
        self.radar: dict[str, dict] = {}
        self.watch: dict[str, dict] = {}
        self.ammo: dict[str, dict] = {}
        self.hold: dict[str, Holding] = {}
        self.research_log: dict[str, pd.Timestamp] = {}
        self.state_seen: dict[tuple, tuple] = {}
        self.journal: list[dict] = []
        self.cards: list[dict] = []
        self.pool_log: list[dict] = []
        self.audits: list[dict] = []
        self.signals_log: list[dict] = []
        self.peak = None
        self.breaker = 0
        self.cooldown_until: pd.Timestamp | None = None
        self.week_new_risk = 0.0
        self.week_key = None
        self.panic = False
        self.pb: feat.PriceBook | None = None
        self.turns = pd.DataFrame()
        self._events = None
        self._breaker_actions = 0
        self._weights: dict[str, float] = {}

    # ---- setup -------------------------------------------------------------------
    def ensure_prices(self, today, lookback_days: int = 400) -> None:
        if self.pb is None:
            start = pd.Timestamp(today) - pd.Timedelta(days=lookback_days)
            self.pb = feat.PriceBook.load(self.md, start, self.md.latest_date())
            ev = self.fs.table("events")
            self._events = ev.sort_values("ann_date").reset_index(drop=True)

    def events_upto(self, t) -> pd.DataFrame:
        e = self._events
        return e.iloc[: int(e["ann_date"].searchsorted(pd.Timestamp(t), side="right"))]

    def log(self, t, sym, action, reason, **kw) -> None:
        card = kw.pop("card", None) or {}
        self.journal.append({"date": pd.Timestamp(t), "symbol": sym, "action": action, "reason": reason,
                             "theme": card.get("theme_name"), "channels": card.get("channels"),
                             "score": card.get("score"), "odds": card.get("odds_entry"),
                             "target_h": card.get("target_h"), "falsify_h": card.get("falsify_h"),
                             "buy_line_h": card.get("buy_line_h"), **kw})

    # ---- calendar ------------------------------------------------------------------
    def _next_day(self, t):
        d = self.pb.dates
        i = int(d.searchsorted(pd.Timestamp(t), side="right"))
        return d[i] if i < len(d) else None

    def is_week_end(self, t) -> bool:
        n = self._next_day(t)
        return n is None or n.isocalendar()[:2] != pd.Timestamp(t).isocalendar()[:2]

    def is_month_end(self, t) -> bool:
        n = self._next_day(t)
        return n is None or (n.year, n.month) != (pd.Timestamp(t).year, pd.Timestamp(t).month)

    def trading_days_back(self, t, n: int) -> pd.Timestamp:
        d = self.pb.dates
        i = int(d.searchsorted(pd.Timestamp(t), side="right")) - 1
        return d[max(0, i - n + 1)]

    # ---- main entry ------------------------------------------------------------------
    def step(self, ctx) -> dict[str, float] | None:
        t = pd.Timestamp(ctx.today)
        self.ensure_prices(t)
        p = self.p
        eq = ctx.equity
        weights = ctx.weights()
        self._weights = weights
        self._reconcile(t, weights)
        self._update_breaker(t, eq)
        wk = t.isocalendar()[:2]
        if wk != self.week_key:
            self.week_key, self.week_new_risk = wk, 0.0
            self.panic = self._panic(t)
            if self.panic:
                self.log(t, "", "恐慌剧本", f"全市场近 5 日跌幅超过 {p['panic_week_drop']:.0%}，弹药池按赔率从高到低建试错仓")

        st_set = ctx.st_symbols()
        stocks = ctx.stocks()
        if self.is_week_end(t) or not self.pool_log:
            self.weekly(t, stocks, st_set, eq)
        changes = self.daily_holdings(t, stocks, st_set, weights)
        if self.is_month_end(t):
            changes |= self.monthly(t, stocks, st_set, weights)
        changes |= self.entries(t, eq, weights, stocks, st_set)
        changes |= self._limits(t, eq, weights)
        if not changes:
            return None
        return self._targets(weights)

    # ---- weight changes -----------------------------------------------------------------
    def _cur(self, sym: str) -> float:
        return self._weights.get(sym, 0.0)

    def _reduce(self, h: Holding, frac: float) -> None:
        """Sell ``frac`` of what is held now."""
        base = self._cur(h.symbol) or h.w_target
        h.w_target = base * (1 - frac)
        h.dirty = True

    # ---- reconciliation with actual fills ------------------------------------------------
    def _reconcile(self, t, weights: dict[str, float]) -> None:
        for sym, h in list(self.hold.items()):
            held = weights.get(sym, 0.0) > 0
            if h.w_target <= 0 and not held:
                del self.hold[sym]
                continue
            if h.status == "pending":
                if held:
                    h.status, h.opened = "open", h.opened or t
                elif (t - h.pending_since).days > self.p["pending_days"] * 1.6:
                    self.log(t, sym, "撤单", "买单多日未成交（涨停或停牌），不追，退回弹药池", card=h.card)
                    del self.hold[sym]
                    self.ammo[sym] = {**h.card, "since": t}
            elif not held:
                del self.hold[sym]  # sold out (exit filled, or delisted)

    # ---- breaker ------------------------------------------------------------------------
    def _update_breaker(self, t, eq: float) -> None:
        p = self.p
        if self.cooldown_until is not None and t >= self.cooldown_until:
            self.cooldown_until = None
            self.breaker = 0
            self.peak = eq
            self.log(t, "", "熔断解除", "冷静期结束，以当前净值为新高点")
        self.peak = eq if self.peak is None else max(self.peak, eq)
        dd = 1 - eq / self.peak if self.peak else 0.0
        level = 3 if dd >= p["dd3"] else 2 if dd >= p["dd2"] else 1 if dd >= p["dd1"] else 0
        if level > self.breaker:
            self.breaker = level
            self._breaker_actions = level
            msg = {1: "暂停新开仓；持仓重测赔率，减掉赔率 < 2 的",
                   2: f"总风险预算降到 {p['dd2_risk']:.0%}；核心仓以外减半",
                   3: "只保留高确信核心仓；冷静期后才恢复交易"}[level]
            self.log(t, "", f"回撤熔断 {level}", f"组合从高点回撤 {dd:.1%}：{msg}")
            if level == 3:
                self.cooldown_until = t + pd.Timedelta(days=int(p["cooldown_days"] * 1.4))
        elif level == 0 and self.breaker and self.cooldown_until is None:
            self.breaker = 0

    def _panic(self, t) -> bool:
        w = self.pb.upto(t, 6)["close_h"]
        if len(w) < 6:
            return False
        r = (w.iloc[-1] / w.iloc[0] - 1).replace([np.inf, -np.inf], np.nan).dropna()
        r = r[r.abs() < 1]
        return bool(len(r) and r.mean() <= -self.p["panic_week_drop"])

    # ---- weekly research ---------------------------------------------------------------
    def weekly(self, t, stocks, st_set, eq) -> None:
        p = self.p
        snap = feat.snapshot(t, stocks, st_set, self.fs, self.pb)
        if snap.empty:
            return
        ev = self.events_upto(t)
        ev = ev[ev["ann_date"] >= t - pd.DateOffset(years=3)]
        veto = vt.veto(snap, ev, t, p)
        if p.get("f_enabled", True) and (self.turns.empty or self.is_month_end(t) or t.month in (5, 9, 11)):
            self.turns = feat.industry_turns(self.fs, t)
        w0 = self.trading_days_back(t, p["radar_event_days"])
        sig = chn.scan(snap, self.fs, ev, t, w0, self.turns, p)
        allowed = self._universe_mask(snap)
        sig = sig[sig["symbol"].isin(allowed[allowed].index)].copy()
        # State signals keep the date they were first seen (continuous presence; gaps > 5 weeks reset).
        if len(sig):
            dates = []
            for r in sig.itertuples():
                if r.code in chn.STATE_CODES:
                    k = (r.symbol, r.code)
                    first, last = self.state_seen.get(k, (t, t))
                    if (t - last).days > 35:
                        first = t
                    self.state_seen[k] = (first, t)
                    dates.append(first)
                else:
                    dates.append(r.date)
            sig["date"] = pd.to_datetime(dates)
        for r in sig.itertuples():
            self.signals_log.append({"date": t, "symbol": r.symbol, "channel": r.channel, "code": r.code,
                                     "grade": r.grade, "detail": r.detail})
        summ = chn.summarize(sig)
        close_h = self.pb.upto(t, 400)["close_h"]

        # ① radar
        for sym, s in summ.iterrows():
            if veto.at[sym, "hard"]:
                continue
            r = self.radar.get(sym)
            if r is None:
                self.radar[sym] = {"since": t, **s.to_dict(), "last": t}
            else:
                r.update({**s.to_dict(), "last": t})
        for sym in [s for s, r in self.radar.items() if (t - r["last"]).days > 7 * p["radar_weeks"]]:
            del self.radar[sym]
        # Radar cap: keep stocks with a real change and more independent evidence first.
        if len(self.radar) > p["radar_cap"]:
            for r in self.radar.values():
                r["_rank"] = (1e6 if r.get("has_change") else 0) + 1e3 * (r.get("n_ab") or 0) + \
                    (r["last"] - pd.Timestamp("2000-01-01")).days / 1e4
            keep = sorted(self.radar, key=lambda s: -self.radar[s]["_rank"])[: p["radar_cap"]]
            self.radar = {s: self.radar[s] for s in keep}
        # 5-minute elimination -> ② watch
        planned = eq * min(p["max_weight"], 0.05)
        for sym, r in list(self.radar.items()):
            if sym not in snap.index or sym in self.watch or sym in self.ammo or sym in self.hold:
                continue
            row = snap.loc[sym]
            sig_row = pd.Series(r)
            q = od.quick_odds(row, sig_row, close_h, p)
            r.update(q)
            why = None
            if veto.at[sym, "hard"]:
                why = "一票否决：" + veto.at[sym, "reasons"]
            elif row["amount20"] < max(p["min_amount"] * 1e8, p["adv_multiple"] * planned):
                why = "成交额不足"
            elif not r.get("has_change"):
                why = "只是便宜，看不到具体变化"
            elif not (q["quick_odds"] >= p["quick_odds_min"]):
                why = f"速算赔率 {q['quick_odds']:.1f} < {p['quick_odds_min']}"
            if why is None:
                self.watch[sym] = {**r, "since": t, "last_change": r.get("last_change", t)}
                self.log(t, sym, "进观察池", f"速算赔率 {q['quick_odds']:.1f}；{r['detail'][:80]}",
                         theme=od.THEMES.get(r["theme"]), channels=r["channels"])
        for sym, w in list(self.watch.items()):
            if sym in summ.index:
                w.update({**summ.loc[sym].to_dict()})
                if pd.notna(summ.at[sym, "last_change"]):
                    w["last_change"] = max(w.get("last_change", t), summ.at[sym, "last_change"])
            gone = sym not in snap.index
            if gone or veto.at[sym, "hard"] or (t - w["last_change"]).days > 30.4 * p["watch_months"]:
                reason = "停牌/退市" if gone else ("一票否决：" + veto.at[sym, "reasons"]) if veto.at[sym, "hard"] \
                    else f"{p['watch_months']} 个月没有新变化"
                self.log(t, sym, "移出观察池", reason)
                del self.watch[sym]
        self._cap(self.watch, p["watch_cap"], "quick_odds", t, "观察池")

        # ③ deep research for the best few -> ammo
        todo = [s for s in self.watch if s in snap.index and
                (s not in self.research_log or (t - self.research_log[s]).days >= 7 * p["research_retry_weeks"]
                 or self.watch[s].get("last_change", t) > self.research_log[s])]
        todo.sort(key=lambda s: -(self.watch[s].get("quick_odds") or 0))
        for sym in todo[: p["research_per_week"]]:
            card = self._research(t, sym, snap.loc[sym], pd.Series(self.watch[sym]), close_h, veto.loc[sym])
            self.research_log[sym] = t
            ok, why = self._ammo_ok(card)
            if ok:
                self.ammo[sym] = {**card, "since": t, "far_since": None}
                del self.watch[sym]
                self.log(t, sym, "进弹药池", f"评分 {card['score']}，买入线 {card['buy_line_h']:.2f}"
                         f"（现价 {card['price_h']:.2f}）", card=card)
            else:
                self.log(t, sym, "深研未通过", why, card=card)

        # recompute ammo cards with the latest price and reports
        for sym, a in list(self.ammo.items()):
            if sym not in snap.index or veto.at[sym, "hard"]:
                self.log(t, sym, "移出弹药池", "停牌/退市" if sym not in snap.index else "一票否决：" + veto.at[sym, "reasons"])
                del self.ammo[sym]
                continue
            s = summ.loc[sym] if sym in summ.index else pd.Series({k: a.get(k) for k in (
                "theme", "channels", "codes", "n_ab", "has_change", "anchor_date", "detail")})
            card = self._research(t, sym, snap.loc[sym], s, close_h, veto.loc[sym], theme=a["theme"], record=False)
            ok, why = self._ammo_ok(card)
            if not ok:
                self.log(t, sym, "降回观察池", f"重算后不达标：{why}", card=card)
                del self.ammo[sym]
                self.watch[sym] = {**s.to_dict(), "since": t, "last_change": a["since"], "quick_odds": card["odds"]}
                continue
            far = card["price_h"] > card["buy_line_h"] * 1.2
            far_since = a.get("far_since") or (t if far else None)
            if not far:
                far_since = None
            fresh = sym in summ.index and summ.at[sym, "has_change"]
            if far_since is not None and (t - far_since).days > 30.4 * p["ammo_stale_months"] and not fresh:
                self.log(t, sym, "降回观察池", f"价格远离买入线超过 {p['ammo_stale_months']} 个月且没有新催化", card=card)
                del self.ammo[sym]
                self.watch[sym] = {**s.to_dict(), "since": t, "last_change": t, "quick_odds": card["odds"]}
                continue
            self.ammo[sym] = {**card, "since": a["since"], "far_since": far_since}
        self._cap(self.ammo, p["ammo_cap"], "expected_entry", t, "弹药池")

        # holdings: refresh cards when there is new information
        for sym, h in self.hold.items():
            if sym not in snap.index:
                continue
            row = snap.loc[sym]
            new_report = pd.notna(row["fin_ann"]) and row["fin_ann"] > h.card["date"]
            if new_report or sym in summ.index:
                s = summ.loc[sym] if sym in summ.index else pd.Series({k: h.card.get(k) for k in (
                    "theme", "channels", "codes", "n_ab", "has_change", "anchor_date", "detail")})
                card = self._research(t, sym, row, s, close_h, veto.loc[sym], theme=h.card["theme"], record=False)
                card["entry_h"] = h.entry_price_h
                h.card = {**card, "catalyst_deadline": h.card["catalyst_deadline"]}
                h.flags.add("card_refreshed")

        self.pool_log.append({"date": t, "radar": len(self.radar), "watch": len(self.watch), "ammo": len(self.ammo),
                              "holdings": len(self.hold), "signals": int(len(sig)), "vetoed": int(veto["hard"].sum()),
                              "soft_vetoed": int(veto["soft"].sum()), "breaker": self.breaker,
                              "turning_industries": int(self.turns["turning"].sum()) if len(self.turns) else 0})

    def _universe_mask(self, snap: pd.DataFrame) -> pd.Series:
        p = self.p
        m = (snap["bars"] >= min(p["min_list_days"], 240)) & snap["fin_period"].notna()
        if p.get("exclude_bj", True):
            m &= snap["exchange"] != "BJ"
        if p.get("exclude_financials", True):
            m &= ~snap["is_financial"]
        return m

    def _research(self, t, sym, row, sig, close_h, veto_row, theme=None, record=True) -> dict:
        card = od.build_card(sym, row, sig, close_h, t, self.p, theme=theme)
        card = sc.grade(card, row, veto_row, self.p)
        card["name"] = row.get("name")
        if record:
            self.cards.append(dict(card))
        return card

    def _ammo_ok(self, card: dict) -> tuple[bool, str]:
        p = self.p
        why = []
        if card["veto"] == "hard":
            why.append("一票否决")
        if card["veto"] == "soft" and not (card["odds_entry"] >= 8):
            why.append("软否决只允许赔率 ≥ 8 的期权仓")
        if not card["qualified"]:
            why.append(f"赔率 {card['odds_entry']:.1f} / 期望 {card['expected_entry']:.0%} / 下行 {card['downside_entry']:.0%} 未达门槛")
        if card["score"] < p["score_min"]:
            why.append(f"评分 {card['score']} < {p['score_min']}")
        if card["n_evidence"] < p["min_ab_evidence"]:
            why.append(f"独立 A/B 证据 {card['n_evidence']} 条")
        if card["sens_passes"] < 2:
            why.append(f"敏感性只过 {card['sens_passes']} 问")
        if card["checklist_fails"] > p["max_checklist_fail"]:
            why.append(f"检查清单 {card['checklist_fails']} 项否")
        return (not why), "；".join(why)

    def _cap(self, pool: dict, cap: int, key: str, t, label: str) -> None:
        if len(pool) <= cap:
            return
        ranked = sorted(pool, key=lambda s: -(pool[s].get(key) if pool[s].get(key) is not None
                                                and np.isfinite(pool[s].get(key)) else -1e9))
        for sym in ranked[cap:]:
            self.log(t, sym, f"挤出{label}", f"超过 {label}上限 {cap}，淘汰最弱的")
            del pool[sym]

    # ---- daily: holdings -------------------------------------------------------------
    def daily_holdings(self, t, stocks, st_set, weights) -> bool:
        p = self.p
        changed = False
        if not self.hold:
            return False
        px = self.pb.last(t)
        ev = self.events_upto(t)
        today_ev = ev[ev["ann_date"] > t - pd.Timedelta(days=4)]
        fc = self.fs.table("forecast")
        fc = fc[(fc["ann_date"] <= t) & (fc["ann_date"] > t - pd.Timedelta(days=4)) & (fc["metric"] == "np")]
        for sym, h in list(self.hold.items()):
            if h.status != "open" or h.w_target <= 0:
                continue
            P = px.get(sym, math.nan)
            if not np.isfinite(P):
                continue  # suspended: nothing can be done today
            c = h.card
            h.peak_h = max(h.peak_h, P)
            mine = today_ev[(today_ev["symbol"] == sym) & (today_ev["ann_date"] > (h.last_evidence or h.opened))]
            kinds = set(mine["event"])
            # -- logic falsified --
            confirmed = (kinds & ALWAYS_CONFIRMED) | (kinds & CONFIRMED_FALSIFY.get(c["theme"], set()))
            soft = kinds & SOFT_FALSIFY
            bad_fc = fc[(fc["symbol"] == sym) & fc["kind"].isin(BAD_FORECAST)]
            if c["theme"] in ("distress", "other", "cycle") and len(bad_fc):
                soft = soft | {"业绩预告 " + bad_fc["kind"].iloc[-1]}
            if c["theme"] == "cycle" and len(self.turns) and c.get("industry") in set(
                    self.turns.loc[self.turns["rolling_over"], "industry"]) and "cycle_roll" not in h.flags:
                soft = soft | {"行业毛利率连续两季回落"}
                h.flags.add("cycle_roll")
            if confirmed:
                titles = "；".join(mine[mine["event"].isin(confirmed)]["title"].str[:40])
                self._exit(t, h, "逻辑证伪（确认）", titles)
                changed = True
                continue
            if soft and "falsified" not in h.flags:
                self._reduce(h, p["falsify_cut"])
                h.flags.add("falsified")
                h.reassess_on = t + pd.Timedelta(days=max(1, int(p["reassess_days"] * 1.4)))
                self.log(t, sym, "证伪减仓", f"{'、'.join(sorted(soft))}：先减 {p['falsify_cut']:.0%}，"
                         f"{p['reassess_days']} 个交易日内重估", card=c)
                changed = True
                continue
            if h.reassess_on is not None and t >= h.reassess_on:
                h.reassess_on = None
                card = self._refresh(t, sym, stocks, st_set, h)
                cur = od.current_odds(card, P) if card else -math.inf
                if card is None or card["veto"] == "hard" or cur < p["odds_reduce"]:
                    self._exit(t, h, "证伪确认，清仓", f"重估后当前赔率 {cur:.1f}")
                else:
                    self.log(t, sym, "证伪重估：保留", f"重估后当前赔率 {cur:.1f}，保留剩余仓位", card=card)
                changed = True
                continue
            # -- positive evidence --
            good = kinds & POSITIVE
            good_fc = fc[(fc["symbol"] == sym) & fc["kind"].isin(GOOD_FORECAST)]
            if len(good_fc):
                good = good | {"业绩预告 " + good_fc["kind"].iloc[-1]}
            if good:
                h.last_evidence = t
                if c["is_event"] and "ma_closed" in kinds:
                    pass
                changed |= self._verify_add(t, h, P, "验证加仓：" + "、".join(sorted(good)))
            if c["is_event"] and "ma_closed" in kinds and "event_done" not in h.flags:
                h.flags.add("event_done")
                card = self._refresh(t, sym, stocks, st_set, h, theme="other")
                if card is None or not card["qualified_now"]:
                    self._exit(t, h, "事件完成，兑现", "过户 / 实施完毕：事件仓使命结束，转核心仓不达标")
                else:
                    h.card, h.tier = card, card["tier"]
                    self.log(t, sym, "事件完成，转核心仓", "按新的赔率卡继续持有", card=card)
                changed = True
                continue
            # -- price stop (backstop) --
            D = c["falsify_h"]
            if p["price_stop"] != "off" and P < D:
                if p["price_stop"] == "all" or "price_stop" in h.flags and P < D * 0.9:
                    self._exit(t, h, "价格止损", f"收盘 {P:.2f} 跌破证伪价 {D:.2f}")
                    changed = True
                    continue
                if "price_stop" not in h.flags:
                    h.flags.add("price_stop")
                    self._reduce(h, 0.5)
                    self.log(t, sym, "价格止损：减半", f"收盘 {P:.2f} 跌破证伪价 {D:.2f}", card=c)
                    changed = True
                    continue
            # -- take profit --
            T = c["target_h"]
            if np.isfinite(T):
                if P >= T and "tp2" not in h.flags:
                    h.flags |= {"tp1", "tp2"}
                    self._reduce(h, p["tp2_cut"])
                    self.log(t, sym, "止盈：达到基准价值", f"收盘 {P:.2f} ≥ 基准价值 {T:.2f}，减 {p['tp2_cut']:.0%}，剩余转利润仓", card=c)
                    changed = True
                elif P >= p["tp1"] * T and "tp1" not in h.flags:
                    h.flags.add("tp1")
                    self._reduce(h, p["tp1_cut"])
                    self.log(t, sym, "止盈：接近基准价值", f"收盘 {P:.2f} ≥ {p['tp1']:.0%} × 基准价值，减 {p['tp1_cut']:.0%}", card=c)
                    changed = True
                elif "tp2" in h.flags and P <= h.peak_h * (1 - p["trail"]):
                    self._exit(t, h, "移动止盈", f"从高点 {h.peak_h:.2f} 回撤 {1 - P / h.peak_h:.0%}")
                    changed = True
                    continue
        return changed

    def _refresh(self, t, sym, stocks, st_set, h: Holding, theme=None) -> dict | None:
        snap = feat.snapshot(t, stocks, st_set, self.fs, self.pb, only={sym})
        if sym not in snap.index:
            return None
        ev = self.events_upto(t)
        ev = ev[(ev["symbol"] == sym) & (ev["ann_date"] >= t - pd.DateOffset(years=3))]
        veto = vt.veto(snap, ev, t, self.p)
        s = pd.Series({k: h.card.get(k) for k in ("theme", "channels", "codes", "n_ab", "has_change", "anchor_date",
                                                   "detail")})
        card = self._research(t, sym, snap.loc[sym], s, self.pb.upto(t, 400)["close_h"], veto.loc[sym],
                              theme=theme or h.card["theme"], record=False)
        return card

    def _verify_add(self, t, h: Holding, P: float, why: str) -> bool:
        p = self.p
        if h.tranche >= len(self.tranches) or self.breaker or "falsified" in h.flags or "tp1" in h.flags:
            return False
        if h.last_add is not None and (t - h.last_add).days < 5:
            return False
        cur = od.current_odds(h.card, P)
        if not cur >= 2:  # §16.2: 2–3 hold without adding
            return False
        add = h.weight_full * self.tranches[h.tranche]
        h.tranche += 1
        h.last_add = t
        h.w_target = (self._cur(h.symbol) or h.w_target) + add
        h.dirty = True
        self.log(t, h.symbol, f"加仓（第 {h.tranche} 批）", f"{why}；当前赔率 {cur:.1f}", card=h.card)
        return True

    # ---- monthly audit ----------------------------------------------------------------
    def monthly(self, t, stocks, st_set, weights) -> bool:
        p = self.p
        changed = False
        px = self.pb.last(t)
        for sym, h in list(self.hold.items()):
            if h.status != "open" or h.w_target <= 0 or not np.isfinite(px.get(sym, math.nan)):
                continue
            P = px[sym]
            c = h.card
            cur = od.current_odds(c, P)
            row = {"date": t, "symbol": sym, "name": c.get("name"), "tier": h.tier, "weight": weights.get(sym, 0.0),
                   "price_h": P, "target_h": c["target_h"], "falsify_h": c["falsify_h"], "current_odds": cur,
                   "risk": weights.get(sym, 0.0) * max(0.0, 1 - c["falsify_h"] / P),
                   "catalyst_deadline": c["catalyst_deadline"], "action": ""}
            # time stop
            deadline = c["catalyst_deadline"] + pd.DateOffset(months=3 * (p["time_stop_quarters"] - 1))
            fresh = h.last_evidence is not None and h.last_evidence > c["catalyst_deadline"] - pd.DateOffset(months=6)
            if t > deadline and not fresh and "time_stop" not in h.flags:
                h.flags.add("time_stop")
                card = self._refresh(t, sym, stocks, st_set, h)
                cur2 = od.current_odds(card, P) if card else -math.inf
                if cur2 < c["R"]:
                    self._exit(t, h, "时间止损：清仓", f"催化剂过期且无新证据，重测赔率 {cur2:.1f} < {c['R']}")
                else:
                    self._reduce(h, 0.5)
                    h.card = {**card, "catalyst_deadline": t + pd.DateOffset(months=6)}
                    self.log(t, sym, "时间止损：减半", f"催化剂过期且无新证据，重测赔率 {cur2:.1f}", card=card)
                row["action"] = "时间止损"
                changed = True
            elif "tp2" in h.flags:
                pass  # profit position after reaching the base value: managed by the trailing stop (§16.3)
            elif cur < p["odds_exit"]:
                self._exit(t, h, "当前赔率 < 1，清仓", f"当前赔率 {cur:.2f}")
                row["action"] = "清仓"
                changed = True
            elif cur < p["odds_reduce"] and "odds_cut" not in h.flags and "tp2" not in h.flags:
                h.flags.add("odds_cut")
                self._reduce(h, 0.5)
                self.log(t, sym, "当前赔率 < 2，减仓", f"当前赔率 {cur:.2f}，减半", card=c)
                row["action"] = "减仓"
                changed = True
            self.audits.append(row)
        return changed

    # ---- entries ------------------------------------------------------------------------
    def total_risk(self, weights: dict[str, float], px: pd.Series) -> float:
        r = 0.0
        for sym, w in weights.items():
            h = self.hold.get(sym)
            P = px.get(sym, math.nan)
            if h is None or not np.isfinite(P) or P <= 0:
                continue
            r += w * max(0.0, 1 - h.card["falsify_h"] / P)
        return r

    def size(self, card: dict, P: float, eq: float) -> float:
        p = self.p
        down = 1 - card["falsify_h"] / P
        if down <= 0:
            return 0.0
        board = card.get("board")
        if card["is_event"] or board == "北交所":
            gap = p["gap_binary"]
        elif card.get("is_st") or board in ("创业板", "科创板"):
            gap = p["gap_growth"]
        else:
            gap = p["gap_main"]
        if card["score"] >= p["score_high"]:
            budget = p["risk_high"]
        elif card["score"] >= p["score_min"]:
            budget = p["risk_std"]
        else:
            budget = p["risk_low"]
        if card["is_event"] or card.get("is_st") or (card.get("mcap") or 0) < p["small_cap"] * 1e8:
            budget *= 0.5
        w = budget / (down + gap)
        R = (card["target_h"] - P) / (P - card["falsify_h"])
        kelly = card["p_up"] - (1 - card["p_up"]) / R if R > 0 else -1
        if kelly <= 0:
            return 0.0
        w = min(w, p["kelly_frac"] * kelly)
        cap = p["max_weight_high"] if card["score"] >= p["score_high"] and card["support"] == "hard" else p["max_weight"]
        if card["tier"] == "option":
            cap = min(cap, p["option_weight"])
        w = min(w, cap)
        adv = card.get("amount20") or 0
        if eq > 0 and adv > 0:
            w = min(w, p["adv_cap"] * adv / eq)
        return max(0.0, w)

    def entries(self, t, eq, weights, stocks, st_set) -> bool:
        p = self.p
        if self.breaker >= 1 or self.cooldown_until is not None or not self.ammo:
            return False
        px = self.pb.last(t)
        risk_cap = p["dd2_risk"] if self.breaker >= 2 else p["max_total_risk"]
        cur_risk = self.total_risk(weights, px)
        ev = self.events_upto(t)
        ev = ev[ev["ann_date"] > t - pd.Timedelta(days=30)]
        cands = []
        for sym, a in self.ammo.items():
            P = px.get(sym, math.nan)
            if sym in self.hold or not np.isfinite(P) or a["date"] >= t:
                continue  # cooling period: decided on a card written before today
            if P > a["buy_line_h"]:
                continue
            bad = ev[(ev["symbol"] == sym) & (ev["ann_date"] > a["date"]) &
                     ev["event"].isin(SOFT_FALSIFY | ALWAYS_CONFIRMED | {"ma_terminated", "control_terminated"})]
            if len(bad):
                continue  # 次日复核：有新的利空就不买，等下周重做赔率卡
            odds_now = (a["target_h"] - P) / (P - a["falsify_h"]) if P > a["falsify_h"] else -1
            cands.append((odds_now, sym, P))
        cands.sort(reverse=True)
        changed = False
        live = {s: h for s, h in self.hold.items() if h.w_target > 0}
        n_open = len(live)
        opt_total = sum(max(weights.get(s, 0), h.w_target) for s, h in live.items() if h.tier == "option")
        ind_w: dict[str, float] = {}
        for s, h in live.items():
            k = h.card.get("industry")
            ind_w[k] = ind_w.get(k, 0) + max(weights.get(s, 0), h.w_target)
        for odds_now, sym, P in cands:
            if n_open >= p["max_positions"]:
                break
            a = self.ammo[sym]
            W = self.size(a, P, eq)
            first = self.tranches[0]
            if P <= a["buy_line_h"] * (1 - p["deep_discount"]):
                first = max(first, p["deep_first"])
            w1 = W * first
            if w1 < 0.004:
                continue
            ind = a.get("industry")
            if ind_w.get(ind, 0) + w1 > p["max_industry"]:
                continue
            if a["tier"] == "option" and opt_total + w1 > p["option_total"]:
                continue
            add_risk = w1 * (1 - a["falsify_h"] / P)
            if cur_risk + add_risk > risk_cap:
                continue
            if self.panic and self.week_new_risk + add_risk > p["panic_week_risk"]:
                continue
            h = Holding(sym, dict(a), weight_full=W, w_target=w1, tranche=1, status="pending", pending_since=t,
                        entry_price_h=P, peak_h=P, tier=a["tier"], last_evidence=t)
            self.hold[sym] = h
            del self.ammo[sym]
            cur_risk += add_risk
            self.week_new_risk += add_risk
            ind_w[ind] = ind_w.get(ind, 0) + w1
            if a["tier"] == "option":
                opt_total += w1
            n_open += 1
            self.log(t, sym, "建仓（试错仓）", f"收盘 {P:.2f} ≤ 买入线 {a['buy_line_h']:.2f}；当前赔率 {odds_now:.1f}；"
                     f"目标仓位 {W:.1%}，首批 {w1:.1%}", card=a, weight=w1)
            changed = True
        # odds add (§15.3 赔率加仓): price fell, logic unchanged, refreshed odds ≥ add_odds
        if self.is_week_end(t):
            for sym, h in self.hold.items():
                P = px.get(sym, math.nan)
                if h.status != "open" or h.w_target <= 0 or not np.isfinite(P) or "card_refreshed" not in h.flags:
                    continue
                h.flags.discard("card_refreshed")
                if P < h.entry_price_h and od.current_odds(h.card, P) >= p["add_odds"] and h.card["veto"] == "":
                    changed |= self._verify_add(t, h, P, f"赔率加仓：价格低于建仓价且重做赔率卡后赔率 ≥ {p['add_odds']}")
        return changed

    # ---- portfolio limits ----------------------------------------------------------------
    def _limits(self, t, eq, weights) -> bool:
        p = self.p
        changed = False
        level = getattr(self, "_breaker_actions", 0)
        if level:
            self._breaker_actions = 0
            px = self.pb.last(t)
            for sym, h in list(self.hold.items()):
                P = px.get(sym, math.nan)
                if not np.isfinite(P) or h.w_target <= 0:
                    continue
                cur = od.current_odds(h.card, P)
                if level >= 3 and not (h.tier == "core" and h.card["score"] >= p["score_high"]):
                    self._exit(t, h, "熔断 3：只留高确信核心仓", "")
                elif level >= 1 and cur < 2 and "tp2" not in h.flags:
                    self._exit(t, h, f"熔断 {level}：赔率 < 2", f"当前赔率 {cur:.1f}")
                elif level == 2 and h.tier != "core":
                    self._reduce(h, 0.5)
                    self.log(t, sym, "熔断 2：非核心仓减半", "", card=h.card)
            changed = True
        for sym, h in self.hold.items():
            w = weights.get(sym, 0.0)
            if w > p["trim_weight"] and h.w_target > 0:
                h.w_target, h.dirty = p["max_weight"], True
                self.log(t, sym, "单票超限减仓", f"市值占比 {w:.1%} > {p['trim_weight']:.0%}，减到 {p['max_weight']:.0%}",
                         card=h.card)
                changed = True
        return changed

    def _exit(self, t, h: Holding, action: str, why: str) -> None:
        h.w_target, h.dirty = 0.0, True
        self.log(t, h.symbol, action, why, card=h.card)

    def _targets(self, weights: dict[str, float]) -> dict[str, float]:
        """Target weights for the engine. Untouched positions keep their drifted weight (no rebalancing)."""
        out = {}
        for sym, h in self.hold.items():
            if h.w_target <= 0:
                continue
            cur = weights.get(sym, 0.0)
            out[sym] = h.w_target if (h.dirty or h.status == "pending" or cur <= 0) else cur
            if h.status == "open":
                h.dirty = False
        return out
