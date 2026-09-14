#!/bin/bash
# Oracle: apply the non-test part of ros2/rosbag2 PR #2476 (merge 08780f9e) onto the base tree.
# The verifier rebuilds from the transferred sources, so no build is needed here; `ws-build` is the
# incremental rebuild an agent would run (~30-45 min for the touched packages on 4 cpus).
set -euo pipefail
cd /workspace/ws/src/rosbag2
if ! git apply --whitespace=nowarn /solution/changes.patch 2>/dev/null; then
  patch -p1 --forward < /solution/changes.patch
fi
echo "applied changes.patch to $(pwd)"
