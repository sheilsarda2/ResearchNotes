#!/bin/bash
# Oracle: apply the non-test part of tracel-ai/burn-onnx PR #466 (merge 438d2cdb) to the
# base tree. Touches crates/onnx-ir/src, crates/burn-onnx/src, the 11 expectations.toml
# promotions and TODO.md. The PR's new tests and fixtures are hidden verifier material
# (tests/hidden-overlay.tar.gz) and are not part of this patch.
set -euo pipefail
cd /workspace/repo
git apply --verbose /solution/changes.patch
