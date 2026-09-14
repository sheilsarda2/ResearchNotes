#!/bin/bash
# Oracle: apply the upstream PR (tracel-ai/burn #5593) on top of the base commit.
# The tree is a clean archive (no .git); `git apply` works outside a repository and the
# patch carries binary fixtures, hence --binary.
set -euo pipefail
cd /workspace/repo
git apply --binary --whitespace=nowarn /solution/changes.patch
# Sanity: the crate still builds offline with the applied change.
cargo build --offline --locked -p burn-store
