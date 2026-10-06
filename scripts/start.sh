#!/usr/bin/env bash
# Start the FastAPI backend and the Node.js frontend together, then open the page.
#
#   scripts/start.sh          # dev: uvicorn --reload + Vite dev server (hot reload)
#   scripts/start.sh prod     # prod: uvicorn + built frontend served by frontend/server.js
#
# Missing dependencies are installed on the first run (uv sync, npm ci).
# Ctrl+C stops both. Ports: API_PORT (default 8000), WEB_PORT (default 5173).
# Set NO_OPEN=1 to skip opening the browser.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-dev}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_PORT="${WEB_PORT:-5173}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
URL="http://$WEB_HOST:$WEB_PORT"

die() { echo "error: $*" >&2; exit 1; }

command -v uv >/dev/null || die "uv not found; install it: curl -LsSf https://astral.sh/uv/install.sh | sh  (or: brew install uv)"
command -v node >/dev/null || die "node not found; install Node.js >= 20.19"

port_busy() { (exec 3<>"/dev/tcp/$1/$2") 2>/dev/null; }
port_busy "$API_HOST" "$API_PORT" && die "port $API_PORT is in use (backend already running?)"
port_busy "$WEB_HOST" "$WEB_PORT" && die "port $WEB_PORT is in use (frontend already running?)"

echo "checking Python dependencies (uv sync) ..."
uv sync --quiet

# node_modules holds platform-specific binaries (esbuild, rollup). If the folder was
# installed on another OS/CPU (or Node major), reinstall instead of failing at startup.
NM_STAMP="frontend/node_modules/.installed-for"
NM_WANT="$(uname -sm) node$(node -p 'process.versions.node.split(".")[0]')"
if [[ ! -d frontend/node_modules || "$(cat "$NM_STAMP" 2>/dev/null)" != "$NM_WANT" ]]; then
  echo "installing frontend dependencies (npm ci) ..."
  (cd frontend && npm ci --registry="$NPM_REGISTRY" --no-audit --no-fund)
  echo "$NM_WANT" > "$NM_STAMP"
fi
# Production serves a fresh build every time, so a stale or foreign dist/ is never reused.
if [[ "$MODE" == "prod" ]]; then
  echo "building frontend ..."
  (cd frontend && npm run build)
fi

pids=()
cleanup() {
  trap - INT TERM EXIT
  # ${arr[@]+...} keeps bash 3.2 (macOS default) happy with set -u on an empty array
  for p in ${pids[@]+"${pids[@]}"}; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# --no-sync: dependencies were just synced above, so uvicorn's reloader doesn't re-check.
if [[ "$MODE" == "prod" ]]; then
  uv run --no-sync uvicorn backend.app.main:app --host "$API_HOST" --port "$API_PORT" &
  pids+=($!)
  (cd frontend && HOST="$WEB_HOST" PORT="$WEB_PORT" API_BASE="http://$API_HOST:$API_PORT" node server.js) &
  pids+=($!)
else
  uv run --no-sync uvicorn backend.app.main:app --host "$API_HOST" --port "$API_PORT" \
    --reload --reload-dir backend --reload-dir data --reload-dir screening &
  pids+=($!)
  (cd frontend && HOST="$WEB_HOST" PORT="$WEB_PORT" API_BASE="http://$API_HOST:$API_PORT" npx vite) &
  pids+=($!)
fi

echo ""
echo "  页面     $URL"
echo "  接口文档 http://$API_HOST:$API_PORT/docs"
echo "  Ctrl+C 停止"
echo ""

# Open the page once both servers answer.
if [[ -z "${NO_OPEN:-}" ]]; then
  (
    for _ in $(seq 1 60); do
      if port_busy "$WEB_HOST" "$WEB_PORT" && port_busy "$API_HOST" "$API_PORT"; then
        if command -v open >/dev/null; then open "$URL"
        elif command -v xdg-open >/dev/null; then xdg-open "$URL" >/dev/null 2>&1
        fi
        exit 0
      fi
      sleep 0.5
    done
  ) &
fi

# Exit as soon as either server dies, and take the other one down with it.
while true; do
  for p in "${pids[@]}"; do
    if ! kill -0 "$p" 2>/dev/null; then
      echo "a process exited; stopping" >&2
      exit 1
    fi
  done
  sleep 1
done
