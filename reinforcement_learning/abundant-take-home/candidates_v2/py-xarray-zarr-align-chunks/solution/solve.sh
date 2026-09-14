#!/usr/bin/env bash
# Oracle: apply the composite upstream patch (pydata/xarray PR #10336 plus the
# PR #10516 follow-up fix, test files removed) onto the pinned base checkout.
set -euo pipefail
cd /workspace/repo
git apply --check /solution/changes.patch
git apply /solution/changes.patch
python -c "import xarray, xarray.backends.chunks as c; print('xarray', xarray.__version__, 'chunks module ok:', c.__name__)"
