"""Data-management endpoints: status, quality check, updates, run history and logs."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app import settings
from backend.app.deps import maintenance_job, market_data
from data import symbols as sym
from data.maintenance import runs
from data.maintenance.checks import probe_sources, quality_report
from data.maintenance.checks import status as lake_status

router = APIRouter(prefix="/api/v1/data", tags=["data"])


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """Write endpoints need ADMIN_TOKEN when it is set. Without it they only accept localhost (see main.py)."""
    if settings.ADMIN_TOKEN and x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(401, "invalid or missing X-Admin-Token")


class UpdateRequest(BaseModel):
    kind: Literal["update", "repair", "names"] = "update"
    symbols: list[str] | None = Field(default=None, max_length=500)
    refresh_meta: bool = True


@router.get("/status")
def data_status():
    md = market_data()
    out = lake_status(md.lake)
    out["job"] = maintenance_job().snapshot()
    out["locked"] = runs.is_locked(md.lake)
    return out


@router.get("/check")
def data_check():
    return quality_report(market_data().lake)


@router.get("/sources")
def data_sources():
    return probe_sources()


@router.post("/update", dependencies=[Depends(require_admin)], status_code=202)
def start_update(req: UpdateRequest | None = None):
    req = req or UpdateRequest()
    symbols = None
    if req.symbols:
        try:
            symbols = [sym.normalize(s) for s in req.symbols]
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    if req.kind == "repair" and not symbols:
        raise HTTPException(400, "repair needs at least one symbol")
    try:
        return maintenance_job().start(req.kind, symbols, refresh_meta=req.refresh_meta)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/update")
def update_state():
    return maintenance_job().snapshot()


@router.get("/runs")
def list_runs(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    total, items = runs.list_runs(market_data().lake, limit=limit, offset=offset)
    return {"total": total, "items": items}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    r = runs.get_run(market_data().lake, run_id)
    if r is None:
        raise HTTPException(404, f"no run {run_id}")
    return r


@router.get("/runs/{run_id}/log")
def get_run_log(run_id: str, offset: int = Query(0, ge=0)):
    try:
        text, nxt = runs.read_log(market_data().lake, run_id, offset=offset)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    job = maintenance_job().snapshot()
    running = job.get("status") == "running" and job.get("run_id") == run_id
    return {"run_id": run_id, "text": text, "next_offset": nxt, "running": running}
