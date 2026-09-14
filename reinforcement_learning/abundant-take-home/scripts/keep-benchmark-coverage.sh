#!/usr/bin/env bash
set -u
benchmark_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
while true; do
  /home/vscode/.local/share/uv/tools/harbor/bin/python "$benchmark_root/scripts/benchmark_coverage_priority.py" \
    --shared "$benchmark_root/jobs/candidate-campaigns-shared.control.json" --watch --interval 5
  sleep 5
done
