#!/bin/bash
# Reference solution: the upstream non-test diff of rerun-io/rerun commits bef4ed8d92 (2026-09-02,
# "Basic end-to-end chunk-index-based optimizer") and 38a25c277e (2026-09-09, "Add support for
# `own_chunk` to chunk optimizer"), restricted to the three store crates (+ three one-line
# `re_server` import fixes), applied on top of the pre-seeded base tree.
set -euo pipefail
cd /workspace/repo
git apply --check /solution/changes.patch
git apply /solution/changes.patch
# Sanity: the feature must compile offline with the graded package selection.
cargo check --offline --locked -p re_chunk_optimizer -p re_log_encoding -p re_chunk_store --tests
