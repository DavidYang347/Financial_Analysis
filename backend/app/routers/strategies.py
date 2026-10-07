"""Strategy management and backtest endpoints. Strategies are Python scripts in strategy/strategies/."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.app.deps import market_data
from screening.params import ParamError
from strategy.engine import BacktestError
from strategy.registry import registry
from strategy.runner import (EXTRA_RE, BacktestStore, jobs, list_extras, load_extra, load_holdings,
                             load_report, load_trades, trades_csv)

router = APIRouter(prefix="/api/v1", tags=["strategies"])


class BacktestRequest(BaseModel):
    strategy: str = Field(..., max_length=64)
    params: dict = Field(default_factory=dict)
    settings: dict = Field(default_factory=dict)


def _get(sid: str):
    s = registry().get(sid)
    if s is None:
        raise HTTPException(404, f"no strategy {sid!r}; add strategy/strategies/{sid}.py to create it")
    return s


def _store() -> BacktestStore:
    return BacktestStore(market_data())


def _record(run_id: str) -> dict:
    try:
        rec = _store().record(run_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    if rec is None:
        raise HTTPException(404, f"no backtest {run_id}")
    return rec


# ---- strategies --------------------------------------------------------------
@router.get("/strategies")
def list_strategies():
    items = [s.summary() for s in registry().scan()]
    return {"count": len(items), "directory": str(registry().directory), "items": items}


@router.get("/strategies/{sid}")
def get_strategy(sid: str):
    return _get(sid).detail()


@router.get("/strategies/{sid}/source")
def get_source(sid: str):
    s = _get(sid)
    return {"id": s.id, "file": s.path.name, "source": s.source()}


# ---- backtests ---------------------------------------------------------------
@router.post("/backtests")
def start_backtest(req: BacktestRequest):
    s = _get(req.strategy)
    if not s.ok:
        raise HTTPException(422, f"{s.path.name} 加载失败: {s.error}")
    try:
        return jobs().start(s, market_data(), req.params, req.settings)
    except (ParamError, BacktestError) as e:
        raise HTTPException(422, str(e)) from e


@router.get("/backtests/job")
def backtest_job():
    return jobs().snapshot()


@router.post("/backtests/job/cancel")
def cancel_backtest():
    return jobs().cancel()


@router.get("/backtests")
def list_backtests(strategy: str | None = None, limit: int = Query(100, ge=1, le=1000)):
    return {"items": _store().history(strategy, limit=limit)}


@router.get("/backtests/{run_id}")
def get_backtest(run_id: str):
    _record(run_id)
    return load_report(_store(), run_id)


@router.get("/backtests/{run_id}/trades")
def get_trades(run_id: str, status: str | None = Query(None, pattern="^(filled|blocked)$"),
               symbol: str | None = Query(None, max_length=20),
               offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=2000)):
    _record(run_id)
    return load_trades(_store(), run_id, status, symbol, offset, limit)


@router.get("/backtests/{run_id}/holdings")
def get_holdings(run_id: str, date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    _record(run_id)
    return load_holdings(_store(), run_id, date)


@router.get("/backtests/{run_id}/trades.csv")
def export_trades(run_id: str):
    _record(run_id)
    data = trades_csv(_store(), run_id)
    if data is None:
        raise HTTPException(404, f"no trades saved for {run_id}")
    return StreamingResponse(iter([data]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{run_id}-trades.csv"'})


@router.get("/backtests/{run_id}/extras")
def get_extras(run_id: str):
    """Extra tables the strategy exported (finalize hook), e.g. pools, odds cards, decision journal."""
    _record(run_id)
    return {"items": list_extras(_store(), run_id)}


@router.get("/backtests/{run_id}/extras/{name}")
def get_extra(run_id: str, name: str, symbol: str | None = Query(None, max_length=20),
              search: str | None = Query(None, max_length=40), sort: str | None = Query(None, max_length=40),
              desc: bool = True, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=5000)):
    _record(run_id)
    if not EXTRA_RE.match(name):
        raise HTTPException(404, f"no table {name!r}")
    out = load_extra(_store(), run_id, name, symbol, search, sort, desc, offset, limit)
    if out is None:
        raise HTTPException(404, f"{run_id} has no table {name}")
    return out
