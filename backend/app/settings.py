from __future__ import annotations

import os

from data import config as _data_config  # noqa: F401  (loads .env)

API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
# Optional shared secret for write endpoints (POST /api/v1/data/update).
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
