#!/usr/bin/env bash
# One Harbor job per (model × effort), with memory-gated parallel execution.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a; . ./.env; set +a
export ANTHROPIC_API_KEY="${TAKE_HOME_TOKEN:?TAKE_HOME_TOKEN missing}"
RUN_ID="${BENCHMARK_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
HARBOR_PY="$(dirname "$(readlink -f "$(command -v harbor)")")/python"
mkdir -p jobs
bash scripts/patch-harbor-viewer.sh
bash scripts/patch-harbor-agent.sh
exec "$HARBOR_PY" scripts/benchmark-efforts.py \
  --run-id "$RUN_ID" --workers "${BENCHMARK_WORKERS:-10}"
