"""FastAPI entry point.

    uvicorn backend.app.main:app --reload            # from the project root
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app import settings
from backend.app.routers import data_admin, market, screening, strategies

app = FastAPI(title="Financial_Analysis Quant API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def local_only_writes(request: Request, call_next):
    """Without ADMIN_TOKEN, write requests are only accepted from localhost."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and not settings.ADMIN_TOKEN:
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return JSONResponse({"detail": "write endpoints are local-only unless ADMIN_TOKEN is set"}, status_code=403)
    return await call_next(request)


app.include_router(market.router)
app.include_router(data_admin.router)
app.include_router(screening.router)
app.include_router(strategies.router)


@app.get("/health")
def health():
    return {"status": "ok"}
