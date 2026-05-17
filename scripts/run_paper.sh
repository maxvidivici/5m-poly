#!/usr/bin/env bash
# Smoke-test paper-mode for a fixed duration (used in PR validation).
set -euo pipefail

DURATION="${1:-90}"
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env

export EDGE_MODE=paper
export LOG_LEVEL=${LOG_LEVEL:-INFO}

timeout "${DURATION}s" python -m edge_bot.cli run --mode paper || ec=$?
ec=${ec:-0}
case "$ec" in
  124) echo "[run_paper.sh] paper-mode duration completed (timeout-stopped)";;
  0)   echo "[run_paper.sh] paper-mode exited cleanly";;
  *)   echo "[run_paper.sh] paper-mode exited with $ec"; exit "$ec";;
esac
