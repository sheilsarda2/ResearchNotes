#!/bin/bash
# Verifier for rs-burn-onnx-rnn-runtime-weights (Harbor separate verifier, no network).
#
# Harbor re-materializes the [[artifacts]] entries at their original paths before this
# script runs, i.e. the submission's crates/onnx-ir/src and crates/burn-onnx/src already
# sit inside /workspace/repo. Everything else in /workspace/repo is restored from the
# pristine tarball baked into this image, so the only code the submission controls is
# those two directories.
#
# Groups (all must pass for reward 1; per-group results in /logs/verifier/score.json):
#   anticheat    submission is plain source with no reference/verifier-path strings
#   build        cargo test --no-run for onnx-ir, onnx-tests, onnx-official-tests; onnx2burn
#   onnx_tests   crates/onnx-tests integration suite incl. the PR's 4 hidden tests (exact count)
#   official     crates/onnx-official-tests: 830 upstream harness rows incl. the 11 promoted
#                RNN-family rows, drift checks, and 24 onnxruntime differential rows (exact count)
#   onnx_ir      crates/onnx-ir integration tests (tests/*.rs; exact count)
#   rejects      onnx2burn must refuse the mixed-weight-group, sequence_lens and peephole
#                models and accept a runtime-weight model
set -uo pipefail

LOGS=/logs/verifier
mkdir -p "$LOGS"
echo 0 > "$LOGS/reward.txt"

REPO=/workspace/repo
SUB=/tmp/submission
OUT=/tmp/verifier-out
PY=/opt/ref/bin/python
ARTIFACT_DIRS="crates/onnx-ir/src crates/burn-onnx/src"
# Same cargo settings as the image's warm build (changing e.g. CARGO_INCREMENTAL would
# invalidate the pre-built target/ and force a full dependency rebuild).
export CARGO_NET_OFFLINE=true CARGO_TERM_COLOR=never

rm -rf "$SUB" "$OUT"
mkdir -p "$SUB" "$OUT"
echo "verifier start $(date -u +%FT%TZ)" | tee "$OUT/timeline.txt"

# ---- 1. capture the submission exactly as Harbor placed it --------------------------
for d in $ARTIFACT_DIRS; do
    if [ -d "$REPO/$d" ]; then
        mkdir -p "$SUB/$(dirname "$d")"
        cp -a "$REPO/$d" "$SUB/$d"
        echo "captured $d ($(find "$SUB/$d" -type f | wc -l) files)" >> "$OUT/timeline.txt"
    else
        echo "MISSING artifact dir $d" >> "$OUT/timeline.txt"
    fi
done

# ---- 2. clean room: pristine tree, keep the warm target/, overlay submission ----------
find "$REPO" -mindepth 1 -maxdepth 1 ! -name target -exec rm -rf {} +
tar -xzf /opt/task/pristine.tar.gz -C "$REPO"
for d in $ARTIFACT_DIRS; do
    rm -rf "$REPO/$d"
    if [ -d "$SUB/$d" ]; then cp -a "$SUB/$d" "$REPO/$d"; fi
done
echo "clean room ready $(date -u +%FT%TZ)" >> "$OUT/timeline.txt"

# ---- 3. anti-cheat scan of the submitted sources -------------------------------------
# Patterns that a clean-room implementation of an ONNX importer has no reason to contain:
# verifier paths, the reference implementation, the corpus expected-output files, and the
# corpus manifest. Pristine sources contain none of these (checked at authoring time).
{
    echo "symlinks:"; find "$SUB" -type l
    echo "non-source files:"; find "$SUB" -type f ! -name '*.rs' ! -name '*.md' ! -name '*.proto' ! -name '*.snap' ! -name '*.toml' ! -name '*.txt'
    echo "patterns:"
    grep -rnE -e '/logs' -e 'reward\.txt' -e 'score\.json' -e 'onnxruntime' -e '\bort::' -e 'ort_sys' \
              -e 'test_data_set' -e 'vendor/node' -e 'expectations\.toml' -e 'output_[0-9]+\.pb' \
              -e 'test_rnnrt' -e '/opt/task' -e '/opt/ref' "$SUB" || true
} > "$OUT/anticheat.txt" 2>&1

# ---- 4. build -----------------------------------------------------------------------
cd "$REPO"
timeout 1800 cargo build -p burn-onnx --bin onnx2burn --locked --offline > "$OUT/build_onnx2burn.txt" 2>&1
echo "onnx2burn_rc=$?" >> "$OUT/build_onnx2burn.txt"
timeout 1800 cargo test --no-run --locked --offline -p onnx-ir -p onnx-tests -p onnx-official-tests > "$OUT/build_tests.txt" 2>&1
echo "build_rc=$?" >> "$OUT/build_tests.txt"
echo "build done $(date -u +%FT%TZ)" >> "$OUT/timeline.txt"

# ---- 5. test groups ------------------------------------------------------------------
timeout 900 cargo test --locked --offline -p onnx-tests -- --test-threads 4 > "$OUT/onnx_tests.txt" 2>&1
echo "rc=$?" >> "$OUT/onnx_tests.txt"
echo "onnx-tests done $(date -u +%FT%TZ)" >> "$OUT/timeline.txt"

timeout 900 cargo test --locked --offline -p onnx-official-tests -- --test-threads 4 > "$OUT/official.txt" 2>&1
echo "rc=$?" >> "$OUT/official.txt"
echo "official done $(date -u +%FT%TZ)" >> "$OUT/timeline.txt"

# The upstream drift gate `verify_fail_compare_still_fails` re-runs all 96 fail-compare
# rows through catch_unwind; one of them was observed to flake once in 10 authoring runs.
# If it is the failing test, re-run it alone up to twice; score.py accepts a retry pass
# and records the retries.
: > "$OUT/official_drift_retry.txt"
if grep -q '^test verify_fail_compare_still_fails \.\.\. FAILED' "$OUT/official.txt"; then
    for attempt in 1 2; do
        echo "== retry $attempt $(date -u +%FT%TZ)" >> "$OUT/official_drift_retry.txt"
        timeout 600 cargo test --locked --offline -p onnx-official-tests --test test_mod -- verify_fail_compare_still_fails --exact >> "$OUT/official_drift_retry.txt" 2>&1
        echo "rc=$?" >> "$OUT/official_drift_retry.txt"
        grep -q '^test verify_fail_compare_still_fails \.\.\. ok' "$OUT/official_drift_retry.txt" && break
    done
fi

timeout 600 cargo test --locked --offline -p onnx-ir \
    --test basic --test custom_ops --test edge_cases --test external_data --test infrastructure \
    --test noop_elimination --test opset_compliance --test simplification --test test_utils \
    -- --test-threads 4 > "$OUT/onnx_ir.txt" 2>&1
echo "rc=$?" >> "$OUT/onnx_ir.txt"
echo "onnx-ir done $(date -u +%FT%TZ)" >> "$OUT/timeline.txt"

# Import-time rejections. A rejection is a non-zero exit (ProcessError surfaced as a panic
# exits 101) AND no generated .rs file; the accept probe must exit 0 and produce one.
O2B="$REPO/target/debug/onnx2burn"
: > "$OUT/rejects.txt"
for f in /opt/task/extra/reject/*.onnx; do
    rm -rf /tmp/o2b_out
    timeout 300 "$O2B" "$f" /tmp/o2b_out --no-development > /tmp/o2b.log 2>&1
    rc=$?
    echo "reject $(basename "$f") rc=$rc files=$(ls /tmp/o2b_out 2>/dev/null | tr '\n' ' ')" >> "$OUT/rejects.txt"
    head -c 4000 /tmp/o2b.log >> "$OUT/rejects.txt"; echo >> "$OUT/rejects.txt"
done
rm -rf /tmp/o2b_out
timeout 300 "$O2B" "$REPO/crates/onnx-tests/tests/gru/gru_runtime_weights.onnx" /tmp/o2b_out --no-development > /tmp/o2b.log 2>&1
rc=$?
echo "accept gru_runtime_weights.onnx rc=$rc files=$(ls /tmp/o2b_out 2>/dev/null | tr '\n' ' ')" >> "$OUT/rejects.txt"
head -c 4000 /tmp/o2b.log >> "$OUT/rejects.txt"
echo "rejects done $(date -u +%FT%TZ)" >> "$OUT/timeline.txt"

# ---- 6. score -----------------------------------------------------------------------
cp "$OUT"/*.txt "$LOGS"/ 2>/dev/null || true
cp /opt/task/extra/manifest.json "$LOGS/differential_manifest.json" 2>/dev/null || true
"$PY" /tests/score.py "$OUT" "$LOGS" || echo 0 > "$LOGS/reward.txt"
echo "reward: $(cat "$LOGS/reward.txt")"
exit 0
