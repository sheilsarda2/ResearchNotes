#!/bin/bash
# Build environment/upstream.tar.gz: clean `git archive` of the base commit with the FFI bridge excised.
# Run from anywhere; requires the shared partial clone (research/cache/v2/rerun-io__rerun) with the commit present.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
TASK=$(dirname "$HERE")
REPO_ROOT=$(cd "$TASK/../.." && pwd)
CLONE=${CLONE:-$REPO_ROOT/research/cache/v2/rerun-io__rerun}
BASE_COMMIT=ddd684110e1f200ee13ba482ab42689b78dd0551
PY=${PY:-python3}
# git-lfs smudges LFS-tracked assets (the two video clips and the mcap used by snippets) when git-lfs is installed.
git -C "$CLONE" archive --format=tar "$BASE_COMMIT" \
  | "$PY" "$HERE/filter_tar.py" "$HERE/excised_files.txt" \
  | gzip -n -6 > "$TASK/environment/upstream.tar.gz"
cp "$TASK/environment/upstream.tar.gz" "$TASK/tests/upstream.tar.gz"
shasum -a 256 "$TASK/environment/upstream.tar.gz"
ls -la "$TASK/environment/upstream.tar.gz"
