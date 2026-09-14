#!/bin/bash
# Oracle: apply the upstream non-test diff of foxglove/foxglove-sdk#1258 (squash a9e4bb7a) on top of
# its first parent b2fb3fab. The patch includes the cbindgen-generated header exactly as upstream
# committed it, so the verifier's header-in-sync check passes without a rebuild here.
set -euo pipefail
cd /workspace/repo
git apply --whitespace=nowarn /solution/changes.patch
