#!/usr/bin/env bash
# Reference solution: restore the excised writer subsystem (inverse of the excision patch).
set -euo pipefail
cd /workspace/repo
git apply --check /solution/changes.patch
git apply /solution/changes.patch
python -c "import mcap.writer, mcap._chunk_builder; print('restored', mcap.writer.LIBRARY_IDENTIFIER)"
