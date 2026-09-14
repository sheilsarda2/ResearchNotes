#!/usr/bin/env bash
set -euo pipefail
cd /workspace/repo
# Apply only the source repair; dependency/build/verifier files remain pristine.
git apply /solution/changes.patch
