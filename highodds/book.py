"""四级池子状态机 + 建仓 / 持仓 / 退出(文档第 4、12-17 节)。

策略脚本每个交易日收盘后调用一次 ``Book.step(ctx)``,返回目标权重:

    每日   持仓监控(证伪 / 价格止损 / 止盈 / 时间止损 / 事件节点)、回撤熔断、弹药池买入触发
    每周   (最后一个交易日)六通道扫描 → 雷达池 → 观察池 → 弹药池,并重算弹药池赔率
    每月   (最后一个交易日)持仓当前赔率审计、行业拐点通道

池子流转:  雷达 ──5 分钟快速淘汰──▶ 观察 ──深度研究(赔率卡)──▶ 弹药 ──价格进入买入区间──▶ 持仓

引擎只在有待执行的动作时才用目标权重覆盖持仓(``Pos.target_w``),平时沿用当前权重,
所以价格漂移不会引发无意义的小额调仓。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from fundamentals.pit import pit as get_pit
from highodds import channels as C
from highodds import features as F
from highodds import odds as O
from highodds import risk as R
from highodds import valuation as V
from highodds.pricer import Pricer

def _num(x, default: float = 0.0) -> float:
    """float(x),NaN / pd.NA / None 时取 default(不能写 ``x or default``:pd.NA 没有布尔值)。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if np.isfinite(v) else default


def _text(x) -> str:
    return "" if x is None or x is pd.NA or (isinstance(x, float) and np.isnan(x)) else str(x)


NEG_HARD = {"fraud_risk", "audit_bad", "delist_notice"}
NEG_ENTRY = NEG_HARD | {"st_imposed", "restructure_terminate", "ctrl_change_terminate", "delist_risk_prompt"}
GIVE_UP_DAYS = 5          # 下单后这么多个交易日仍没成交,放弃
MIN_WEIGHT = 0.003        # 低于这个仓位不值得开


@dataclass
class Pos:
    sym: str
    name: str
    signal_day: int
    signal_date: pd.Timestamp
    channel: str
    motif: str
    subs: str
    fam: str | None
    tag: str | None
    binary: bool
    bucket: str
    conviction: str
    score: float
    res: V.CardResult
    entry_px_h: float
    w_full: float
    industry: str = ""
    tranche: int = 0
    peak_h: float = 0.0
    stage: int = -1
    seen: bool = False
    tp1: bool = False
    tp2: bool = False
    time_stopped: bool = False
    no_add: bool = False
    odds_cut: bool = False
    falsified_day: int | None = None
    falsify_reason: str = ""
    exit_reason: str = ""
    target_w: float | None = None
    target_kind: str = ""
    target_day: int = 0
    last_fin_ann: pd.Timestamp | None = None
    open_date: pd.Timestamp | None = None
    sens_mult: float = 1.0


class Book:
    def __init__(self, md, cfg) -> None:
        self.md, self.cfg = md, cfg
        self.pit = get_pit()
        self.panel = F.get_panel(md, self.pit)
        self.pricer = Pricer(md)
        n = self.pit.notices
        self.EV = C.EventIndex(n)
        self.chain_notices = n[n["event"].isin(F.CHAIN_TAGS)]
        self.by_date = {d: g.groupby("symbol")["event"].agg(set).to_dict()
                        for d, g in n[n["event"] != ""].groupby("ann_date")}
        self.radar: dict[str, dict] = {}
        self.watch: dict[str, dict] = {}
        self.ammo: dict[str, dict] = {}
        self.pos: dict[str, Pos] = {}
        self.expired: dict[str, pd.Timestamp] = {}
        self.carded: dict[str, pd.Timestamp] = {}
        self.brk = R.Breaker()
        self.S: pd.DataFrame | None = None
        self.n_calls = 0
        self._pending_adds: dict[str, float] = {}
        self.week_risk: dict[tuple, float] = {}
        self.L_pool: list[dict] = []
        self.L_cards: list[dict] = []
        self.L_signals: list[dict] = []
        self.L_closed: list[dict] = []
        self.L_week: list[dict] = []
        self.L_breaker: list[dict] = []

    # ================================================================ logging
    def _name(self, sym: str) -> str:
        if self.S is not None and sym in self.S.index:
            return str(self.S.at[sym, "name_then"])
        return ""

    def _log(self, day, sym, frm, to, reason, **kw) -> None:
        self.L_pool.append({"date": day, "symbol": sym, "name": self._name(sym), "from": frm, "to": to,
                            "reason": reason, **kw})

    def _log_card(self, day, sym, event, res: V.CardResult) -> None:
        self.L_cards.append({"date": day, "symbol": sym, "name": self._name(sym), "event": event, **res.summary()})

    # ================================================================ main loop
    def step(self, ctx) -> dict[str, float]:
        cfg = self.cfg
        day = pd.Timestamp(ctx.today)
        n = ctx.day_number()
        tb = ctx.today_bars()
        held = ctx.weights()
        self._reconcile(held, n, day)

        def pxh(s: str):
            if s in tb.index:
                return float(tb.at[s, "close"] * tb.at[s, "adj_factor"])
            return None

        lvl, fresh, dd = self.brk.update(ctx.equity, n, cfg)
        if fresh:
            self.L_breaker.append({"date": day, "level": lvl, "drawdown": round(dd, 4), "equity": ctx.equity})
        ev_today = self.by_date.get(day, {})

        cuts: dict[str, tuple[float, str]] = {}      # sym -> (保留比例, 原因)
        self._monitor(ctx, day, n, held, pxh, ev_today, fresh, cuts)

        if self.n_calls == 0 or ctx.is_period_end("week"):
            self._weekly(ctx, day, n, held, pxh, ctx.is_period_end("month"))
        adds: dict[str, float] = {}
        self._entries(ctx, day, n, held, pxh, ev_today, adds)

        targets = dict(held)
        acted = bool(cuts or adds)
        for sym, (keep, why) in cuts.items():
            p = self.pos.get(sym)
            if p is None or sym not in held:
                continue
            new_w = held[sym] * keep
            if new_w < MIN_WEIGHT * 0.5:
                new_w = 0.0
            self._set_target(p, new_w, "cut", n)
            if keep <= 0 and not p.exit_reason:
                p.exit_reason = why
            self._log(day, sym, "position", "position" if new_w > 0 else "exit", why, keep=keep)
        for sym, w in adds.items():
            p = self.pos[sym]
            self._set_target(p, w, "add", n)
        for sym, p in self.pos.items():
            if p.target_w is not None:
                targets[sym] = p.target_w
                acted = True
        self.n_calls += 1
        if ctx.is_period_end("week"):
            self._week_row(ctx, day, lvl, dd)
        if not acted:
            return None   # 没有任何动作:保持现有持仓,避免为价格漂移产生小额调仓
        return {s: w for s, w in targets.items() if w > 0}

    @staticmethod
    def _set_target(p: Pos, w: float, kind: str, n: int) -> None:
        p.target_w, p.target_kind, p.target_day = w, kind, n

    # ================================================================ reconcile with engine fills
    def _reconcile(self, held: dict, n: int, day) -> None:
        for sym, p in list(self.pos.items()):
            w = held.get(sym, 0.0)
            if w > 0 and not p.seen:
                p.seen, p.open_date = True, day
            if p.target_w is not None:
                tw = p.target_w
                done = (w <= tw * 1.05 + 1e-4) if p.target_kind == "cut" else (w >= tw * 0.9)
                if done or n - p.target_day > GIVE_UP_DAYS:
                    p.target_w = None
            if p.seen and w <= 0:
                self._close(sym, day, p)
            elif not p.seen and n - p.signal_day > GIVE_UP_DAYS:
                self._log(day, sym, "position", "ammo", "下单后未成交,放弃(涨停或停牌)")
                self.pos.pop(sym)

    def _close(self, sym: str, day, p: Pos) -> None:
        c = p.res.card
        self.L_closed.append({
            "symbol": sym, "name": p.name, "channel": p.channel, "motif": p.motif, "subs": p.subs, "family": p.fam or "",
            "tag": p.tag or "", "bucket": p.bucket, "conviction": p.conviction, "score": p.score,
            "signal_date": p.signal_date, "open_date": p.open_date, "close_date": day,
            "odds": round(c.odds(p.entry_px_h), 2), "expected": round(c.expected(p.entry_px_h), 3),
            "p_success": round(c.p_base + c.p_bull, 3), "downside": round(c.downside(p.entry_px_h), 3),
            "exit_reason": p.exit_reason or "其他", "tranche": p.tranche + 1, "industry": p.industry})
        self._log(day, sym, "position", "exit", p.exit_reason or "其他")
        self.pos.pop(sym, None)

    # ================================================================ daily: position monitoring
    def _monitor(self, ctx, day, n, held, pxh, ev_today, fresh, cuts) -> None:
        cfg = self.cfg
        month_end = ctx.is_period_end("month")
        cum = [cfg.tranche1, cfg.tranche1 + cfg.tranche2, 1.0]
        for sym, p in list(self.pos.items()):
            w = held.get(sym, 0.0)
            if w <= 0 or not p.seen:
                continue
            px = pxh(sym)
            if px is None:
                continue
            c = p.res.card
            p.peak_h = max(p.peak_h, px)
            evs = ev_today.get(sym, set())
            keep, why = 1.0, ""

            def cut(k: float, reason: str) -> None:
                nonlocal keep, why
                if k < keep:
                    keep, why = k, reason

            # -------- 1) 证伪:逻辑证伪(事件 / 一票否决) 与 价格止损
            fam_term = F.CHAINS[p.fam]["terminate"] if p.fam else None
            logic = (evs & NEG_HARD) or (fam_term and fam_term in evs)
            if logic and p.falsified_day is None:
                p.falsified_day, p.falsify_reason = n, "逻辑证伪:" + ",".join(sorted((evs & NEG_HARD) | ({fam_term} & evs)))
                cut(0.5, p.falsify_reason)
            elif px <= c.bear and p.falsified_day is None:
                p.falsified_day, p.falsify_reason = n, "跌破证伪价"
                cut(0.5, p.falsify_reason)
            elif p.falsified_day is not None and n - p.falsified_day >= cfg.falsify_wait_days:
                if p.falsify_reason.startswith("逻辑") or px <= c.bear:
                    cut(0.0, p.falsify_reason + ",确认失效清仓")
                else:
                    p.falsified_day = None     # 价格已回到证伪价之上,保留剩余半仓
            # -------- 2) 事件节点
            if p.fam and not keep < 1:
                live = self._live_chain(ctx, day, sym, p.fam)
                if live is not None:
                    if live.get("done"):
                        cut(0.0, "事件完成,兑现离场")
                    elif live["stage"] > p.stage:
                        p.stage = live["stage"]
                        p.tag = live["tag"]
                        newp = O.stage_prob(p.tag, cfg.event_prob_scale)
                        p.res.card = replace(c, p_base=newp, p_bear=1 - newp)
                        self._log(day, sym, "position", "position", f"事件推进到「{p.tag}」,成功概率 → {newp:.0%}")
                        if not p.no_add and p.tranche < 2 and self.brk.entries_allowed(n) and p.falsified_day is None:
                            p.tranche += 1
                            tw = min(cum[p.tranche] * p.w_full, cfg.max_single)
                            if tw > w * 1.05:
                                self._add_queue(p, tw)
            # -------- 3) 止盈
            T = c.base
            lv1, lv2 = max(cfg.tp1 * T, p.entry_px_h * 1.10), max(cfg.tp2 * T, p.entry_px_h * 1.20)
            if px >= lv2 and not p.tp2:
                p.tp2 = p.tp1 = p.no_add = True
                cut(1 - cfg.tp2_cut, f"止盈:达到基准价值 {cfg.tp2:.0%}")
            elif px >= lv1 and not p.tp1:
                p.tp1 = p.no_add = True
                cut(1 - cfg.tp1_cut, f"止盈:达到基准价值 {cfg.tp1:.0%}")
            if p.tp2 and p.peak_h > 0 and px <= p.peak_h * (1 - cfg.trail_dd):
                cut(0.0, "移动止盈:从最高点回撤")
            # -------- 4) 时间止损
            held_days = (day - p.open_date).days if p.open_date is not None else 0
            if held_days >= cfg.time_stop_days and not p.time_stopped and not p.tp1:
                p.time_stopped = p.no_add = True
                cut(0.5, "时间止损:催化剂过期仍未兑现")
            # -------- 5) 月度审计:当前赔率
            if month_end:
                cur = c.odds(px)
                if cur < cfg.odds_exit:
                    cut(0.0, f"当前赔率 {cur:.1f} < {cfg.odds_exit:g},清仓")
                elif cur < cfg.odds_cut and not p.odds_cut:
                    p.odds_cut = True
                    cut(0.5, f"当前赔率 {cur:.1f} < {cfg.odds_cut:g},减仓")
                elif cur >= 3:
                    p.odds_cut = False
            # -------- 6) 回撤熔断
            if fresh == 1 and c.odds(px) < cfg.odds_cut:
                cut(0.5, "回撤熔断一级:当前赔率不足,减仓")
            elif fresh == 2 and p.bucket != "core":
                cut(0.5, "回撤熔断二级:核心仓以外减半")
            elif fresh == 3 and not (p.bucket == "core" and p.conviction == "high"):
                cut(0.0, "回撤熔断三级:只保留高确信核心仓")
            if keep < 1:
                cuts[sym] = (keep, why)
                p.no_add = True

    def _add_queue(self, p: Pos, tw: float) -> None:
        """事件节点通过后的验证加仓:记下来,当天 step 的 _entries 里统一下单。"""
        self._pending_adds[p.sym] = tw

    def _live_chain(self, ctx, day, sym: str, fam: str) -> dict | None:
        cn = self.chain_notices
        rows = cn[(cn["symbol"] == sym) & (cn["ann_date"] <= day)]
        ch = F.build_chains(rows)
        c = ch.get((sym, fam))
        return c["live"] if c else None

    # ================================================================ weekly scan
    def _weekly(self, ctx, day, n, held, pxh, month_end: bool) -> None:
        cfg = self.cfg
        S = F.build_snapshot(ctx, self.pit, self.panel, cfg)
        if S.empty:
            return
        S = S.join(F.veto_table(S, self.pit.notices, day, cfg))
        self.S = S
        # 只需要最近一个有效期内的公告:更早的链已经过期(event_age_days),终止公告也不再影响今天
        cn = self.chain_notices
        lo = day - pd.Timedelta(days=self.cfg.event_age_days + self.cfg.event_lookback)
        chains = F.build_chains(cn[(cn["ann_date"] <= day) & (cn["ann_date"] > lo)])
        # 一次性批量查好所有活跃事件链的"事件前价格",避免后面逐只查库(每次一条 SQL)
        self.pricer.before({(s, c["live"]["first"]) for (s, _), c in chains.items() if c["live"]}
                           | {(s, c["closed"]["first"]) for (s, _), c in chains.items() if c["closed"]})
        sigs = C.scan(S, chains, self.EV, self.pit, cfg, day, self.pricer, month_end)
        by_sym: dict[str, list[dict]] = {}
        for s in sigs:
            by_sym.setdefault(s["symbol"], []).append(s)

        # ---- 新信号 → 雷达池
        for sym, ss in by_sym.items():
            if S.at[sym, "veto_hard"]:
                continue
            floor = self.expired.get(sym)
            ss = [s for s in ss if floor is None or s["date"] > floor]
            ss = [s for s in ss if (day - s["date"]).days <= max(cfg.event_lookback, 200)]
            if not ss:
                continue
            tgt = self.radar.get(sym) or self.watch.get(sym) or self.ammo.get(sym)
            if tgt is None and sym not in self.pos:
                tgt = self.radar[sym] = {"since": day, "last_sig": day, "sigs": {}}
                self._log(day, sym, "", "radar", "; ".join(sorted({s["text"] for s in ss}))[:120])
            if tgt is None:
                continue
            store = tgt.setdefault("sigs", {})
            for s in ss:
                k = (s["sub"], s["date"])
                if k not in store:
                    store[k] = s
                    self.L_signals.append({"scan_date": day, "symbol": sym, "name": S.at[sym, "name_then"],
                                           **{k2: v for k2, v in s.items() if k2 not in ("symbol", "hits", "fam", "tag",
                                                                                         "chain_first", "pre_px", "industry", "n_kinds", "kind")}})
                    tgt["last_sig"] = day
                    if s["sub"] in V.CHANGE_SUBS:
                        tgt["last_change"] = day

        # ---- 一票否决:所有池子清理;持仓触发逻辑证伪
        for pool, name in ((self.radar, "radar"), (self.watch, "watch"), (self.ammo, "ammo")):
            for sym in list(pool):
                if sym in S.index and S.at[sym, "veto_hard"]:
                    pool.pop(sym)
                    self._log(day, sym, name, "", "一票否决:" + S.at[sym, "veto_hard"].rstrip(";"))
        for sym, p in self.pos.items():
            if sym in S.index and S.at[sym, "veto_hard"] and p.falsified_day is None and sym in held:
                p.falsified_day, p.falsify_reason = n, "逻辑证伪:一票否决 " + S.at[sym, "veto_hard"].rstrip(";")
                self._set_target(p, held[sym] * 0.5, "cut", n)
                self._log(day, sym, "position", "position", p.falsify_reason, keep=0.5)

        # ---- 雷达池 → 观察池 (5 分钟快速淘汰)
        for sym in list(self.radar):
            e = self.radar[sym]
            if sym not in S.index:
                continue
            if (day - e["since"]).days > cfg.radar_expire_days:
                self.radar.pop(sym)
                self.expired[sym] = day
                self._log(day, sym, "radar", "", "8 周内没进观察池,自动删除")
                continue
            res = self._make_card(sym, S.loc[sym], e, chains, day)
            if res is None or not res.has_change:
                continue
            if res.card.odds() >= cfg.quick_odds_min:
                self.watch[sym] = {"since": day, "last_change": e.get("last_change", day), "last_sig": e["last_sig"],
                                   "sigs": e["sigs"], "quick": res.card.odds()}
                self.radar.pop(sym)
                self._log(day, sym, "radar", "watch", f"速算赔率 {res.card.odds():.1f} ≥ {cfg.quick_odds_min:g}",
                          odds=round(res.card.odds(), 2))

        # ---- 观察池 → 弹药池 (深度研究:完整赔率卡)
        self._refresh_ammo(day, S, chains)
        quota = cfg.deep_per_week * (cfg.warmup_mult if self.n_calls == 0 else 1)
        order = sorted((s for s in self.watch if s in S.index and s not in self.ammo),
                       key=lambda s: -self.watch[s].get("quick", 0))
        for sym in order:
            if quota <= 0:
                break
            last = self.carded.get(sym)
            if last is not None and (day - last).days < cfg.card_cooldown_days:
                continue
            quota -= 1
            self.carded[sym] = day
            e = self.watch[sym]
            res = self._make_card(sym, S.loc[sym], e, chains, day)
            if res is None:
                continue
            res = V.finish_card(res, S.loc[sym], cfg)
            e["quick"] = res.card.odds()
            ok, why = self._admit_ammo(res, S.loc[sym], day)
            if not ok:
                self._log(day, sym, "watch", "watch", "赔率卡未放行:" + why, odds=round(res.card.odds(), 2))
                continue
            self._to_ammo(sym, S.loc[sym], res, e, day)

        # ---- 过期 / 容量
        for sym in list(self.watch):
            if (day - self.watch[sym].get("last_change", self.watch[sym]["since"])).days > cfg.watch_expire_days:
                self.watch.pop(sym)
                self.expired[sym] = day
                self._log(day, sym, "watch", "", "6 个月没有新变化,删除")
        while len(self.watch) > cfg.watch_cap:
            w = min(self.watch, key=lambda s: self.watch[s].get("quick", 0))
            self.watch.pop(w)
            self._log(day, w, "watch", "", "观察池满,淘汰最弱")
        while len(self.radar) > cfg.radar_cap:
            w = min(self.radar, key=lambda s: self.radar[s]["last_sig"])
            self.radar.pop(w)
            self._log(day, w, "radar", "", "雷达池满,淘汰最旧")

    def _signals_for(self, e: dict, day, has_chain: bool) -> list[dict]:
        out = []
        for (sub, d), s in e.get("sigs", {}).items():
            if (day - d).days > 200:
                continue
            if sub == "A1" and not has_chain:
                continue
            out.append(s)
        return out

    def _make_card(self, sym: str, r: pd.Series, e: dict, chains: dict, day) -> V.CardResult | None:
        live, fam, pre = None, None, None
        best = -1
        for f in F.CHAINS:
            c = chains.get((sym, f))
            if c and c["live"] and not c["live"].get("done") and (day - c["live"]["last"]).days <= self.cfg.event_lookback:
                if c["live"]["stage"] > best:
                    live, fam, best = c["live"], f, c["live"]["stage"]
        if live is not None:
            key = (sym, live["first"])
            pre = self.pricer.before({key}).get(key)
        sigs = self._signals_for(e, day, live is not None)
        if not sigs and live is None:
            return None
        if not sigs:
            sigs = [{"channel": "A", "sub": "A1", "grade": "A", "text": f"{fam}:{live['tag']}", "date": live["last"]}]
        return V.build_card(sym, r, sigs, live, fam, pre, self.cfg, day)

    def _admit_ammo(self, res: V.CardResult, r: pd.Series, day) -> tuple[bool, str]:
        cfg = self.cfg
        soft = r.get("veto_soft", "")
        if soft and not cfg.allow_option:
            return False, "软否决:" + soft.rstrip(";")
        if res.evidence_ab < cfg.min_evidence:
            return False, f"独立 A/B 级证据 {res.evidence_ab} < {cfg.min_evidence}"
        if res.conviction == "none":
            return False, f"评分 {res.score['total']} < {cfg.score_option:g}"
        if res.sens.get("passed", 0) < 2:
            return False, f"敏感性三问只过 {res.sens.get('passed')} 问"
        if res.limit <= 0:
            return False, "买入线无效"
        if not res.pass_thresholds:
            return False, "买入线处仍不满足门槛:" + ";".join(res.fail_reasons)
        if res.card.price > res.limit * (1 + cfg.ammo_max_gap):
            return False, f"现价高于买入线 {res.card.price / res.limit - 1:.0%}"
        return True, ""

    def _to_ammo(self, sym, r, res: V.CardResult, e: dict, day) -> None:
        cfg = self.cfg
        if len(self.ammo) >= cfg.ammo_cap:
            worst = min(self.ammo, key=lambda s: self.ammo[s]["res"].score["total"])
            if self.ammo[worst]["res"].score["total"] >= res.score["total"]:
                self._log(day, sym, "watch", "watch", "弹药池满且评分不高于最弱者")
                return
            self.ammo.pop(worst)
            self._log(day, worst, "ammo", "watch", "弹药池满,被更高评分替换")
            self.watch[worst] = {"since": day, "last_change": day, "last_sig": day, "sigs": {}, "quick": 0}
        self.ammo[sym] = {"since": day, "res": res, "far_since": None, "trigger": None,
                          "soft": r.get("veto_soft", ""), "sigs": e.get("sigs", {}), "last_sig": e.get("last_sig", day),
                          "meta": {"board": r["board"], "is_st": bool(r["is_st"]), "mcap": _num(r["mcap_yi"], 0.0),
                                   "amount20": _num(r["amount20"], 0.0), "industry": _text(r.get("industry"))}}
        self.watch.pop(sym, None)
        self._log(day, sym, "watch", "ammo",
                  f"评分 {res.score['total']},赔率 {res.card.odds():.1f},买入线 {res.limit:.2f}(hfq)",
                  odds=round(res.card.odds(), 2), limit=round(res.limit, 3))
        self._log_card(day, sym, "ammo_in", res)

    def _refresh_ammo(self, day, S, chains) -> None:
        """文档 12.2:每周用最新价格重算弹药池里每只的赔率。"""
        cfg = self.cfg
        for sym in list(self.ammo):
            a = self.ammo[sym]
            if sym not in S.index:
                continue
            res = self._make_card(sym, S.loc[sym], a, chains, day)
            if res is None or not res.has_change:
                self.ammo.pop(sym)
                self.watch[sym] = {"since": day, "last_change": day, "last_sig": a["last_sig"], "sigs": a["sigs"], "quick": 0}
                self._log(day, sym, "ammo", "watch", "逻辑/估值条件不再成立,降回观察池")
                continue
            res = V.finish_card(res, S.loc[sym], cfg)
            if res.conviction == "none" or res.evidence_ab < cfg.min_evidence or res.limit <= 0:
                self.ammo.pop(sym)
                self.watch[sym] = {"since": day, "last_change": day, "last_sig": a["last_sig"], "sigs": a["sigs"], "quick": 0}
                self._log(day, sym, "ammo", "watch", "重算后评分 / 证据不足,降回观察池")
                continue
            a["res"] = res
            r = S.loc[sym]
            a["soft"] = r.get("veto_soft", "")
            a["meta"].update(mcap=float(r["mcap_yi"]), amount20=float(r["amount20"]))
            if res.card.price > res.limit * (1 + cfg.ammo_max_gap):
                a["far_since"] = a["far_since"] or day
                if (day - a["far_since"]).days > cfg.ammo_far_days:
                    self.ammo.pop(sym)
                    self.watch[sym] = {"since": day, "last_change": day, "last_sig": a["last_sig"], "sigs": a["sigs"], "quick": 0}
                    self._log(day, sym, "ammo", "watch", "价格远离买入线超过 3 个月,降回观察池")
            else:
                a["far_since"] = None

    # ================================================================ daily: entries
    def _entries(self, ctx, day, n, held, pxh, ev_today, adds) -> None:
        cfg = self.cfg
        # 已有待执行的加仓
        for sym, w in list(self._pending_adds.items()):
            p = self.pos.get(sym)
            if p is not None:
                adds[sym] = w
        self._pending_adds = {}
        if not self.ammo:
            return
        equity = ctx.equity
        iso = day.isocalendar()[:2]
        used_risk = self.week_risk.get(iso, 0.0)
        cur = {s: (self.pos[s], held.get(s, 0.0)) for s in self.pos}
        total_risk = 0.0
        for s, (p, w) in cur.items():
            px = pxh(s)
            if px:
                total_risk += w * max(1 - p.res.card.bear / px, 0.0)
        cap_risk = self.brk.risk_cap(cfg)
        cluster: dict[str, float] = {}
        bucket_w: dict[str, float] = {"core": 0.0, "event": 0.0, "option": 0.0}
        for s, (p, w) in cur.items():
            cluster[p.industry] = cluster.get(p.industry, 0.0) + w
            bucket_w[p.bucket] += w
        n_pos = len(cur)

        order = sorted(self.ammo, key=lambda s: -self.ammo[s]["res"].card.odds())
        for sym in order:
            a = self.ammo[sym]
            res: V.CardResult = a["res"]
            px = pxh(sym)
            if px is None or sym in self.pos:
                continue
            if px > res.limit:
                a["trigger"] = None
                continue
            if a["trigger"] is None:          # 第一次进入买入区间:记下,次日复核后再买(冷静期 ≥ 24 小时)
                a["trigger"] = n
                self._log(day, sym, "ammo", "ammo", f"价格进入买入区间 {px:.2f} ≤ {res.limit:.2f},次日复核",
                          px=round(px, 3))
                continue
            if n - a["trigger"] < 1:
                continue
            if not self.brk.entries_allowed(n):
                continue
            if ev_today.get(sym, set()) & NEG_ENTRY:
                self._log(day, sym, "ammo", "ammo", "复核发现新增利空公告,暂缓买入")
                a["trigger"] = None
                continue
            if n_pos >= cfg.max_positions:
                break
            nos = V.entry_checklist(res, px, "", cfg, True)
            if len(nos) >= 2:
                self._log(day, sym, "ammo", "ammo", "入场检查清单两项以上为否:" + "、".join(nos))
                continue
            meta = a["meta"]
            c = res.card
            soft = a.get("soft", "")
            conv = res.conviction
            bucket = R.bucket_of(res.binary, conv, c.odds(px), cfg)
            if soft:
                if not cfg.allow_option:
                    continue
                bucket, conv = "option", "low"
            down = c.downside(px)
            gap = R.gap_buffer(meta["board"], meta["is_st"], res.binary, cfg)
            budget = R.risk_budget(conv, res.binary, meta["is_st"], meta["mcap"], cfg) * res.sens.get("size_mult", 1.0)
            w_full = R.position_size(budget, down, gap)
            p_win = c.p_base + c.p_bull
            kc = R.kelly_cap(p_win, max(c.odds(px), 0.01))
            if kc > 0:
                w_full = min(w_full, kc)
            single = cfg.max_single_hard if (conv == "high" and res.floor_kind == "hard") else cfg.max_single
            w_full = min(w_full, single)
            if bucket == "option":
                w_full = min(w_full, cfg.max_option_single)
                w_full = min(w_full, max(cfg.max_option_total - bucket_w["option"], 0.0))
            elif bucket == "event":
                w_full = min(w_full, max(cfg.max_event_bucket - bucket_w["event"], 0.0))
            ind = meta["industry"]
            w_full = min(w_full, max(cfg.max_cluster - cluster.get(ind, 0.0), 0.0))
            adv = meta["amount20"] * 1e8 * cfg.adv_mult / equity if equity > 0 else 0.0
            w_full = min(w_full, adv)
            w1 = w_full * cfg.tranche1
            risk1 = w1 * down
            if iso in self.week_risk or used_risk:
                w1 = min(w1, max(cfg.week_new_risk - used_risk, 0.0) / down) if down > 0 else w1
                risk1 = w1 * down
            if total_risk + risk1 > cap_risk and down > 0:
                w1 = min(w1, max(cap_risk - total_risk, 0.0) / down)
                risk1 = w1 * down
            if w1 < MIN_WEIGHT:
                self._log(day, sym, "ammo", "ammo", f"仓位约束后不足 {MIN_WEIGHT:.1%},不建仓")
                continue
            p = Pos(sym=sym, name=self._name(sym), signal_day=n, signal_date=day, channel=res.channel, motif=res.motif,
                    subs=",".join(res.subs), fam=res.family, tag=res.tag, binary=res.binary, bucket=bucket,
                    conviction=conv, score=float(res.score["total"]), res=res, entry_px_h=px, w_full=w_full,
                    industry=ind, peak_h=px, sens_mult=res.sens.get("size_mult", 1.0))
            p.stage = F.CHAINS[res.family]["stages"].get(res.tag, -1) if res.family else -1
            self._set_target(p, w1, "open", n)
            self.pos[sym] = p
            self.ammo.pop(sym)
            n_pos += 1
            used_risk += risk1
            total_risk += risk1
            self.week_risk[iso] = used_risk
            cluster[ind] = cluster.get(ind, 0.0) + w1
            bucket_w[bucket] += w1
            self._log(day, sym, "ammo", "position", f"建仓(试错仓)目标 {w1:.2%},全仓 {w_full:.2%},{res.motif}/{bucket}",
                      px=round(px, 3), w=round(w1, 4))
            self._log_card(day, sym, "open", res)

    # ================================================================ reporting
    def _week_row(self, ctx, day, lvl, dd) -> None:
        self.L_week.append({"date": day, "radar": len(self.radar), "watch": len(self.watch), "ammo": len(self.ammo),
                            "positions": len(self.pos), "equity": ctx.equity, "cash_pct": ctx.cash / ctx.equity,
                            "breaker": lvl, "drawdown": round(dd, 4)})

    def finalize(self, ctx) -> dict[str, pd.DataFrame]:
        trades = getattr(ctx, "trades_df", pd.DataFrame())
        filled = trades[trades["status"] == "filled"] if len(trades) else trades
        closed = pd.DataFrame(self.L_closed)
        # 仍持有的也要进去,平仓日留空
        for sym, p in self.pos.items():
            c = p.res.card
            closed = pd.concat([closed, pd.DataFrame([{
                "symbol": sym, "name": p.name, "channel": p.channel, "motif": p.motif, "subs": p.subs,
                "family": p.fam or "", "tag": p.tag or "", "bucket": p.bucket, "conviction": p.conviction,
                "score": p.score, "signal_date": p.signal_date, "open_date": p.open_date, "close_date": pd.NaT,
                "odds": round(c.odds(p.entry_px_h), 2), "expected": round(c.expected(p.entry_px_h), 3),
                "p_success": round(c.p_base + c.p_bull, 3), "downside": round(c.downside(p.entry_px_h), 3),
                "exit_reason": "持有中", "tranche": p.tranche + 1, "industry": p.industry}])], ignore_index=True)
        if len(closed) and len(filled):
            pnl, cost, held_d = [], [], []
            pf = ctx._pf.positions
            for r in closed.itertuples():
                t = filled[(filled["symbol"] == r.symbol) & (pd.to_datetime(filled["date"]) >= r.signal_date)]
                if pd.notna(r.close_date):
                    t = t[pd.to_datetime(t["date"]) <= r.close_date]
                buys = t[t["side"] == "buy"]
                sells = t[t["side"] == "sell"]
                spent = float((buys["amount"] + buys["commission"]).sum())
                got = float((sells["amount"] - sells["commission"] - sells["tax"]).sum())
                if pd.isna(r.close_date) and r.symbol in pf:
                    got += pf[r.symbol].shares * pf[r.symbol].price
                pnl.append(got - spent)
                cost.append(spent)
                end = r.close_date if pd.notna(r.close_date) else pd.Timestamp(ctx.today)
                held_d.append((end - r.open_date).days if pd.notna(r.open_date) else None)
            closed["cost"] = cost
            closed["pnl"] = pnl
            closed["ret"] = closed["pnl"] / closed["cost"].replace(0, np.nan)
            closed["days"] = held_d
        out = {"ho_trades": closed, "ho_pool_events": pd.DataFrame(self.L_pool),
               "ho_cards": pd.DataFrame(self.L_cards), "ho_signals": pd.DataFrame(self.L_signals),
               "ho_weekly": pd.DataFrame(self.L_week), "ho_breaker": pd.DataFrame(self.L_breaker)}
        # 期末弹药池 / 观察池快照
        rows = []
        for sym, a in self.ammo.items():
            rows.append({"pool": "ammo", "symbol": sym, "name": self._name(sym), **a["res"].summary()})
        out["ho_pools_final"] = pd.DataFrame(rows)
        return {k: v for k, v in out.items() if isinstance(v, pd.DataFrame)}
