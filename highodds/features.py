"""行情 + 基本面的时点快照、事件链、一票否决。

所有价格和每股指标统一放在后复权(hfq)口径下:
    px_hfq = 原始价格 × 复权因子 F
    bvps_h = 每股净资产 × F(公告日)
除权除息(送转、分红)发生后,hfq 价格和 hfq 每股指标同比变化,比值不变,
所以赔率卡里存的 D / T / 买入线不需要在除权时调整。换算回今天的真实价格:hfq ÷ F(今天)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fundamentals.pit import PIT

# ---- 事件链 --------------------------------------------------------------------------
CHAINS = {
    "restructure": {
        "stages": {"restructure_plan": 0, "restructure_progress": 0, "restructure_misc": 0,
                   "restructure_draft": 1, "restructure_inquiry": 2, "restructure_accepted": 3,
                   "restructure_approved": 4, "restructure_done": 5},
        "terminate": "restructure_terminate", "done": "restructure_done",
    },
    "ctrl": {
        "stages": {"ctrl_change": 0, "ctrl_change_done": 1},
        "terminate": "ctrl_change_terminate", "done": "ctrl_change_done",
    },
    "bankruptcy": {
        "stages": {"bankruptcy_pre": 0, "bankruptcy_investor": 1, "bankruptcy_plan": 2},
        "terminate": None, "done": None,
    },
}
CHAIN_TAGS = sorted({t for c in CHAINS.values() for t in c["stages"]}
                    | {c["terminate"] for c in CHAINS.values() if c["terminate"]})
STAGE_TAG = {fam: {i: t for t, i in c["stages"].items()} for fam, c in CHAINS.items()}


def build_chains(events: pd.DataFrame) -> dict[tuple[str, str], dict]:
    """(symbol, family) -> {live: {...}|None, closed: {...}|None}.

    live   = 终止公告之后仍在推进的链(stage / tag / first / last)
    closed = 最近一次被终止的链(first = 链上第一条公告的日期, end = 终止公告日期)
    """
    out: dict[tuple[str, str], dict] = {}
    if events.empty:
        return out
    ev = events[events["event"].isin(CHAIN_TAGS)].sort_values("ann_date")
    for sym, g in ev.groupby("symbol", sort=False):
        for fam, c in CHAINS.items():
            rows = g[g["event"].isin(set(c["stages"]) | ({c["terminate"]} if c["terminate"] else set()))]
            if rows.empty:
                continue
            chain: list[tuple[pd.Timestamp, str]] = []
            closed = None
            for d, e in zip(rows["ann_date"], rows["event"]):
                if c["terminate"] and e == c["terminate"]:
                    closed = {"first": chain[0][0] if chain else d, "end": d}
                    chain = []
                else:
                    chain.append((d, e))
            live = None
            if chain:
                idx = max(c["stages"][e] for _, e in chain)
                live = {"stage": idx, "tag": STAGE_TAG[fam][idx] if idx in STAGE_TAG[fam] else chain[-1][1],
                        "first": chain[0][0], "last": chain[-1][0], "n": len(chain)}
                # done is a terminal state, not something to buy into
                live["done"] = c["done"] is not None and any(e == c["done"] for _, e in chain)
            out[(sym, fam)] = {"live": live, "closed": closed}
    return out


# ---- PB 面板 --------------------------------------------------------------------------
class PBPanel:
    """周频 hfq 收盘价和当时可见的 hfq 每股净资产,用来算 PB 的历史分位 / 底部 / 中枢。"""

    def __init__(self, md, pit: PIT, start, end) -> None:
        days = pd.DatetimeIndex(pd.to_datetime(
            md.sql("SELECT DISTINCT date FROM daily WHERE date BETWEEN ? AND ? ORDER BY date", [start, end])["date"]))
        iso = days.isocalendar()
        key = (iso["year"].astype(int) * 100 + iso["week"].astype(int)).to_numpy()
        wk = pd.Series(days.to_numpy()).groupby(key).max().sort_values()
        self.dates = pd.DatetimeIndex(wk.to_numpy()).astype("datetime64[ns]")
        lit = ",".join(f"DATE '{d:%Y-%m-%d}'" for d in self.dates)
        q = md.sql(f"SELECT symbol, date, close * adj_factor AS c FROM daily_hfq WHERE date IN ({lit})")
        q["date"] = pd.to_datetime(q["date"]).astype("datetime64[ns]")
        px = q.pivot(index="date", columns="symbol", values="c").reindex(self.dates)
        f = pit.fin[["symbol", "ann_date", "bvps_h"]].dropna().copy()
        f["ann_date"] = f["ann_date"].astype("datetime64[ns]")
        f = f.sort_values("ann_date")
        left = px.stack().rename("c").reset_index().rename(columns={"level_0": "date"})
        left = left.sort_values("date")
        m = pd.merge_asof(left, f, left_on="date", right_on="ann_date", by="symbol", direction="backward")
        bv = m.pivot(index="date", columns="symbol", values="bvps_h").reindex(index=self.dates, columns=px.columns)
        self.pb = (px / bv.where(bv > 0)).astype("float32")
        self.cols = px.columns

    def stats(self, day, weeks: int, floor_q: float, min_weeks: int = 104) -> pd.DataFrame:
        n = int(np.searchsorted(self.dates.values, np.datetime64(pd.Timestamp(day)), side="right"))
        a = self.pb.to_numpy()[max(0, n - weeks):n]
        cnt = np.isfinite(a).sum(0)
        with np.errstate(all="ignore"):
            floor = np.nanquantile(a, floor_q, axis=0)
            center = np.nanmedian(a, axis=0)
        ok = cnt >= min_weeks
        return pd.DataFrame({"pb_floor": np.where(ok, floor, np.nan), "pb_center": np.where(ok, center, np.nan),
                             "pb_n": cnt}, index=self.cols), a

    @staticmethod
    def percentile(hist: np.ndarray, now: np.ndarray) -> np.ndarray:
        with np.errstate(all="ignore"):
            cnt = np.isfinite(hist).sum(0)
            below = (hist <= now[None, :]).sum(0)
        return np.where(cnt > 0, below / np.maximum(cnt, 1), np.nan)


_PANELS: dict = {}


def get_panel(md, pit: PIT) -> PBPanel:
    latest = md.latest_date()
    key = (str(md.lake.root), latest)
    if key not in _PANELS:
        _PANELS.clear()
        _PANELS[key] = PBPanel(md, pit, pd.Timestamp(latest) - pd.Timedelta(days=365 * 11), latest)
    return _PANELS[key]


# ---- 快照 -----------------------------------------------------------------------------
def _trailing_losses(annual: pd.DataFrame) -> pd.Series:
    """每个股票最近连续亏损的年数(以归母净利润为准)。"""
    a = annual.sort_values(["symbol", "period"])
    a = a.assign(loss=(a["np_parent"] < 0).astype(int))
    out = {}
    for sym, g in a.groupby("symbol", sort=False):
        n = 0
        for v in g["loss"].to_numpy()[::-1]:
            if v:
                n += 1
            else:
                break
        out[sym] = n
    return pd.Series(out, dtype="float64")


def build_snapshot(ctx, pit: PIT, panel: PBPanel, cfg) -> pd.DataFrame:
    """今天收盘后,股票池里每只股票的行情、估值和基本面状态。"""
    day = pd.Timestamp(ctx.today)
    pool = ctx.universe({"exclude_st": cfg.exclude_st, "exclude_bj": cfg.exclude_bj, "boards": cfg.boards,
                         "min_list_days": cfg.min_list_days, "min_amount": cfg.min_amount})
    if pool.empty:
        return pd.DataFrame()
    S = pool.set_index("symbol")[["name", "board", "exchange"]].copy()
    tb = ctx.today_bars()
    S = S.join(tb[["close", "adj_factor"]].rename(columns={"close": "px_raw", "adj_factor": "F"}), how="inner")
    S["px"] = S["px_raw"] * S["F"]

    bars = ctx.bars(60, S.index, adjust="hfq")
    g = bars.groupby("symbol", sort=False)
    last = g["close"].last()
    first = g["close"].first()
    n = g["close"].count()
    S["ret60"] = (last / first - 1).where(n >= 50).reindex(S.index)
    t21 = bars.groupby("symbol", sort=False).tail(21).groupby("symbol", sort=False)
    c20 = t21["close"].first().where(t21["close"].count() >= 21)  # close 20 trading days ago
    S["ret20"] = (last / c20 - 1).reindex(S.index)
    tail = bars.groupby("symbol", sort=False).tail(20)
    S["amount20"] = (tail.groupby("symbol")["amount"].mean() / 1e8).reindex(S.index)

    st = ctx.st_symbols()
    S["is_st"] = S.index.isin(st)
    nm = ctx.md.sql("""SELECT symbol, name FROM names WHERE start <= ? AND ("end" IS NULL OR "end" >= ?)""",
                    [ctx.today, ctx.today]).drop_duplicates("symbol").set_index("symbol")["name"]
    S["name_then"] = nm.reindex(S.index).fillna(S["name"])
    S["star_st"] = S["name_then"].str.contains(r"^\*ST|^S\*ST|退", regex=True)

    # ---- 财务 ----
    lf = pit.latest_fin(day)
    cols = ["period", "ann_date", "revenue", "np_parent", "np_total", "total_profit", "equity", "total_liab",
            "cash", "debt_ratio", "bvps_h", "eps_h", "shares", "F_ann", "ocf_q", "np_q", "rev_q", "np_yoy",
            "rev_yoy", "roe", "industry", "gross_margin"]
    S = S.join(lf[cols].rename(columns={"period": "fin_period", "ann_date": "fin_ann"}), how="left")
    prev_q = pit.prior_quarters(day, 2)
    two = prev_q.groupby("symbol", sort=False).agg(ocf_first=("ocf_q", "first"), ocf_last=("ocf_q", "last"),
                                                   np_first=("np_q", "first"), np_last=("np_q", "last"))
    S = S.join(two, how="left")
    S["ocf_pos2"] = (S["ocf_first"] > 0) & (S["ocf_last"] > 0)
    annual = pit.annual(day, 5)
    S["eps_norm"] = annual.groupby("symbol")["eps_h"].mean().reindex(S.index)
    S["loss_years"] = _trailing_losses(annual).reindex(S.index).fillna(0)
    S["ann_rev"] = annual.groupby("symbol")["revenue"].last().reindex(S.index)
    S["ann_np"] = annual.groupby("symbol")["np_parent"].last().reindex(S.index)
    S["ann_profit"] = annual.groupby("symbol")["total_profit"].last().reindex(S.index)

    S["mcap_yi"] = S["px"] * S["shares"] / S["F_ann"] / 1e8
    S["netcash_ps"] = (S["cash"] - S["total_liab"]) / S["shares"] * S["F_ann"]  # hfq, 保守口径:货币资金 - 总负债
    S["bvps"] = S["bvps_h"].where(S["bvps_h"] > 0)
    S["pb"] = S["px"] / S["bvps"]

    # ---- PB 历史 ----
    stats, hist = panel.stats(day, max(104, int(cfg.pb_window_days / 5)), cfg.pb_floor_pct)
    S = S.join(stats, how="left")
    now = S["pb"].reindex(panel.cols).to_numpy(dtype="float64")
    S["pb_pct"] = pd.Series(panel.percentile(hist, now), index=panel.cols).reindex(S.index)

    pl = pit.pledge_latest(day)
    S["pledge"] = pl.reindex(S.index)
    return S


def latest_fin_for(pit: PIT, symbols, day) -> pd.DataFrame:
    """持仓监控用的轻量版:只取指定股票,最近一期已公告的财报。"""
    f = pit.fin
    f = f[f["symbol"].isin(list(symbols)) & (f["ann_date"] <= pd.Timestamp(day))]
    return f.sort_values(["symbol", "period"]).groupby("symbol").tail(1).set_index("symbol")


# ---- 一票否决(文档 2.2) --------------------------------------------------------------
HARD = "hard"
SOFT = "soft"


def veto_table(S: pd.DataFrame, events: pd.DataFrame, day, cfg) -> pd.DataFrame:
    """每只股票的否决原因。hard=放弃;soft=最多进期权仓。

    已实现:造假/立案、非标审计、终止上市/退市整理、财务类退市指标(净利润为负且营收不足)、
    市值类(< 5 亿,创业板 / 科创板 3 亿)、面值类(< 1 元)、质押 > 阈值、*ST 退市风险警示。
    未实现(没有免费的批量数据):经营现金流长期无法解释利润、频繁更换审计机构、短债长投、司法冻结。"""
    d1 = pd.Timestamp(day)
    yr = events[(events["ann_date"] > d1 - pd.Timedelta(days=365)) & (events["ann_date"] <= d1)]
    fraud = set(yr.loc[yr["event"] == "fraud_risk", "symbol"])
    audit = set(yr.loc[yr["event"] == "audit_bad", "symbol"])
    half = yr[yr["ann_date"] > d1 - pd.Timedelta(days=180)]
    delist = set(half.loc[half["event"].isin(["delist_notice"]), "symbol"])
    hard, soft = pd.Series("", index=S.index), pd.Series("", index=S.index)

    def add(series: pd.Series, mask, text: str) -> pd.Series:
        m = mask.fillna(False) if hasattr(mask, "fillna") else mask
        return series.where(~m, series + text + ";")

    hard = add(hard, S.index.isin(fraud), "立案调查/造假嫌疑")
    hard = add(hard, S.index.isin(audit), "非标审计意见")
    hard = add(hard, S.index.isin(delist), "终止上市/退市整理")
    small = S["board"].isin(["创业板", "科创板"])
    rev_min = np.where(small, 1e8, 3e8)
    worst = S[["ann_np", "ann_profit"]].min(axis=1)
    hard = add(hard, (worst < 0) & (S["ann_rev"] < rev_min), "财务类退市指标")
    hard = add(hard, S["mcap_yi"] < np.where(small, 3.0, 5.0), "市值过小(退市风险)")
    hard = add(hard, S["px_raw"] < 1.0, "股价低于面值")
    hard = add(hard, S["equity"] <= 0, "净资产为负")
    soft = add(soft, S["pledge"] > cfg.pledge_veto, "大股东质押过高")
    soft = add(soft, S["star_st"], "退市风险警示(*ST)")
    return pd.DataFrame({"veto_hard": hard, "veto_soft": soft})
