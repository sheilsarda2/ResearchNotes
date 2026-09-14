#!/bin/bash
# Oracle: restore the excised rerun_c FFI bridge of the C++ SDK (upstream implementation at the base commit).
set -euo pipefail
cd /workspace/repo
git apply /solution/changes.patch
# Optional: rebuild so the oracle mirrors an agent's workflow (the verifier rebuilds clean-room regardless).
if [ "${SOLVE_BUILD:-0}" = "1" ]; then
  cmake --build build/debug --target snippets rerun_sdk_tests
fi
