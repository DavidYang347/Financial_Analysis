"""Stock-screening endpoints. Methods are Python scripts in screening/screeners/."""
from __future__ import annotations

import io
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.app.deps import market_data
from screening.params import ParamError
from screening.registry import registry
from screening.runner import ScreenError, ScreenStore, load_run, run_screener

router = APIRouter(prefix="/api/v1/screeners", tags=["screening"])


class RunRequest(BaseModel):
    params: dict = Field(default_factory=dict)
    as_of: date | None = None


def _get(sid: str):
    s = registry().get(sid)
    if s is None:
        raise HTTPException(404, f"no screener {sid!r}; add screening/screeners/{sid}.py to create it")
    return s


@router.get("")
def list_screeners():
    items = [s.summary() for s in registry().scan()]
    return {"count": len(items), "directory": str(registry().directory), "items": items}


@router.get("/history")
def all_history(limit: int = Query(50, ge=1, le=500)):
    return {"items": ScreenStore(market_data()).history(limit=limit)}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    store = ScreenStore(market_data())
    try:
        rec, _ = store.get(run_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    if rec is None:
        raise HTTPException(404, f"no run {run_id}")
    return load_run(store, run_id, registry().get(rec["screener_id"]))


@router.get("/runs/{run_id}/csv")
def export_run(run_id: str):
    store = ScreenStore(market_data())
    try:
        rec, df = store.get(run_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    if rec is None or df is None:
        raise HTTPException(404, f"no saved result for {run_id}")
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    data = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel opens Chinese correctly
    return StreamingResponse(iter([data]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{run_id}.csv"'})


@router.get("/{sid}")
def get_screener(sid: str):
    return _get(sid).detail()


@router.get("/{sid}/source")
def get_source(sid: str):
    s = _get(sid)
    return {"id": s.id, "file": s.path.name, "source": s.source()}


@router.get("/{sid}/history")
def screener_history(sid: str, limit: int = Query(50, ge=1, le=500)):
    _get(sid)
    return {"items": ScreenStore(market_data()).history(sid, limit=limit)}


@router.post("/{sid}/run")
async def run(sid: str, req: RunRequest | None = None):
    s = _get(sid)
    req = req or RunRequest()
    if not s.ok:
        raise HTTPException(422, f"{s.path.name} 加载失败: {s.error}")
    try:
        return await run_in_threadpool(run_screener, s, market_data(), req.params, req.as_of)
    except ParamError as e:
        raise HTTPException(422, str(e)) from e
    except ScreenError as e:
        raise HTTPException(422, str(e)) from e
