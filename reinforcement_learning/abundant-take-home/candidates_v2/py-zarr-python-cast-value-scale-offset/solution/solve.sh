#!/usr/bin/env bash
# Oracle solution: apply the upstream PR's non-test diff (zarr-developers/zarr-python #3874).
set -euo pipefail
cd /workspace/repo
git apply --check /solution/changes.patch
git apply /solution/changes.patch
python -c "import zarr; from zarr.codecs import CastValue, ScaleOffset; print('oracle applied', zarr.__version__)"
