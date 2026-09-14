#!/bin/bash
# Oracle solution: apply the upstream PR #1647 non-test diff to the base tree.
set -euo pipefail
cd /workspace/repo
git apply --check /solution/changes.patch
git apply /solution/changes.patch
# Rebuild so the binary at rust/target/debug/mcap reflects the patch (offline; deps are pre-fetched).
cd rust && cargo build --offline -p mcap-cli
