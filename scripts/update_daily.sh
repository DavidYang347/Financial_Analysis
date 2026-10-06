#!/usr/bin/env bash
# Manual incremental update of A-share daily bars.
# Usage: scripts/update_daily.sh [extra args, e.g. --symbols 600000.SH]
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m data.maintenance update "$@"
uv run python -m data.maintenance check > /dev/null && echo "quality check passed"
