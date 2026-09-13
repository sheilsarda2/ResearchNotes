#!/usr/bin/env bash
# Restart our monitor if it exits unexpectedly. It stops when its campaign ends.
set -u
benchmark_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
benchmark_python="${BENCHMARK_PYTHON:-/home/vscode/.local/share/uv/tools/harbor/bin/python}"
cd "$benchmark_root"
while true; do
  "$benchmark_python" scripts/watch-candidate-campaign.py "$@" --apply
  benchmark_exit=$?
  if [ "$benchmark_exit" -eq 0 ]; then
    exit 0
  fi
  sleep 30
done
