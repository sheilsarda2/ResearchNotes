#!/bin/bash
# Oracle: apply the non-test part of eclipse-zenoh/zenoh-cpp PR #750 (connectivity api) to the
# task tree. Only include/ is collected by the verifier; the patch also carries the PR's docs,
# example and CLAUDE.md changes, which are harmless extras here.
set -euo pipefail
cd /workspace/repo
git apply --check /solution/changes.patch
git apply /solution/changes.patch
echo "applied changes.patch: $(git status --porcelain | wc -l) paths changed"
