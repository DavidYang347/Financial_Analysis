"""Market reviews (行情复盘): monthly / weekly statistics and report texts.

Statistics are computed by ``review.stats`` and stored in data/lake/reviews/; report texts live in
materials/复盘报告/. Computing a new period is a write action (local-only, like other jobs).
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from review import store

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])
_lock = threading.Lock()

# Tables the page can request without loading the whole statistics file.
SECTIONS = {"gainers", "losers", "doubled_l2h", "doubled_cc", "doubled_new", "doubled_delist", "side_st", "side_new",
            "industries", "concepts", "emotion", "weeks", "events", "risk", "modes"}


def _check(kind: str, label: str) -> None:
    try:
        store.check(kind, label)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("")
def list_reviews():
    return store.list_reviews()


@router.get("/{kind}/{label}")
def get_review(kind: str, label: str):
    """Report text + summary statistics (large tables are fetched separately)."""
    _check(kind, label)
    stats = store.load_stats(kind, label)
    report = store.load_report(kind, label)
    if stats is None and report is None:
        raise HTTPException(404, f"no {kind} review {label}")
    out = {"kind": kind, "label": label, "report": report, "has_stats": stats is not None}
    if stats:
        out.update({k: stats.get(k) for k in ("start", "end", "prev_end", "trading_days", "summary", "indices",
                                              "valuation", "macro", "margin", "breadth", "volume", "style",
                                              "leaders", "genes", "profile", "ma_reaction", "supply", "method")})
        y = stats.get("ytd_doubled") or {}
        out["ytd_doubled"] = {k: y.get(k) for k in ("count", "new")} if y else None
    return out


@router.get("/{kind}/{label}/table/{name}")
def get_table(kind: str, label: str, name: str, limit: int = Query(500, ge=1, le=5000)):
    _check(kind, label)
    if name not in SECTIONS:
        raise HTTPException(404, f"unknown table {name}")
    stats = store.load_stats(kind, label)
    if stats is None:
        raise HTTPException(404, f"no statistics for {kind} {label}")
    rows = stats.get(name) or []
    return {"name": name, "total": len(rows), "items": rows[:limit]}


class ComputeRequest(BaseModel):
    kind: str = Field(..., pattern="^(monthly|weekly)$")
    period: str = Field(..., max_length=10, description="monthly: YYYY-MM; weekly: any YYYY-MM-DD in the week")


@router.post("/compute")
def compute(req: ComputeRequest):
    """Recompute statistics for one period from the local data (a few seconds)."""
    from review import stats as rs
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "已有复盘统计在计算，稍后再试")
    try:
        r = rs.monthly(req.period) if req.kind == "monthly" else rs.weekly(req.period)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    finally:
        _lock.release()
    return {"kind": req.kind, "label": r["label"], "summary": r["summary"]}
