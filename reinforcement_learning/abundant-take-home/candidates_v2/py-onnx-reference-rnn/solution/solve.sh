#!/usr/bin/env bash
# Reference solution: restore the upstream RNN, GRU and LSTM implementations of the
# ONNX reference evaluator and their registration (git diff excised..base -- onnx/reference).
set -euo pipefail
cd /workspace/repo
git apply --check /solution/changes.patch
git apply /solution/changes.patch
python -c "from onnx.reference.ops import load_op; [load_op('', op) for op in ('RNN', 'GRU', 'LSTM')]; print('RNN family restored')"
