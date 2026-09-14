#!/usr/bin/env bash
# Oracle: restore the excised storage writers (upstream 47be3eed, rosbags 0.11.5).
set -euo pipefail
cd /workspace/repo
git apply --check /solution/changes.patch
git apply /solution/changes.patch
python -c "import rosbags.rosbag2.storage_mcap as m, rosbags.rosbag2.storage_sqlite3 as s; assert m.McapWriter and s.Sqlite3Writer"
