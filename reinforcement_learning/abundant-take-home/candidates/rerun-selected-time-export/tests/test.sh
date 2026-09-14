#!/bin/bash
set -euo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
cd /workspace/repo
cp /tests/selected_time_export.rs crates/store/re_entity_db/tests/selected_time_export.rs
# Transferred files may retain old mtimes; invalidate cached crate fingerprints.
python - <<'PYTOUCH'
from pathlib import Path
for tree in ("crates/store/re_entity_db/src", "crates/store/re_chunk/src"):
    for path in Path(tree).rglob("*"):
        if path.is_file():
            path.touch()
PYTOUCH
python /tests/test_export.py
