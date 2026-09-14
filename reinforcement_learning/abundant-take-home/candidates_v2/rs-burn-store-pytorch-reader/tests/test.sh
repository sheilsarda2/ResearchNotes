#!/bin/bash
# Verifier for rs-burn-store-pytorch-reader.
#
# Harbor re-materializes the [[artifacts]] entry at its original path, so the submission
# arrives at /workspace/repo/crates/burn-store/src inside this container; everything else in
# /workspace/repo is the pristine base commit with a warm target/. This script:
#   1. records and anti-cheat-scans the submitted sources;
#   2. replaces the submitted test directories with the pristine/hidden ones;
#   3. rebuilds burn-store offline;
#   4. runs the hidden and upstream test groups with exact counts;
#   5. runs the PyTorch-written fixture matrix and the reject cases (one process each);
#   6. writes /logs/verifier/reward.txt (0/1) and /logs/verifier/score.json (attribution).
set -uo pipefail

LOG=/logs/verifier
mkdir -p "$LOG"
echo 0 > "$LOG/reward.txt"

REPO=/workspace/repo
SUB=$REPO/crates/burn-store/src
PRISTINE=/opt/pristine
FIX=/opt/fixtures
HIDDEN=/tests/hidden
HARNESS=/tests/harness/fixture_matrix.rs

export CARGO_NET_OFFLINE=true CARGO_TERM_COLOR=never RUST_BACKTRACE=1
cd "$REPO" || exit 0

# Exact expectations. The three fixture totals are also asserted at image build.
EXPECTED_PYTORCH_UNIT=108      # src/pytorch/tests: reader 71 + store 37
EXPECTED_SAFETENSORS_UNIT=52   # src/safetensors/tests (pristine)
EXPECTED_INTEGRATION=20        # crates/burn-store/tests/*.rs (pristine): 8+1+5+2+4
EXPECTED_PYTORCH_TESTS_CRATE=37
EXPECTED_ACCEPT_FILES=28
EXPECTED_REJECT_FILES=32
EXPECTED_TENSOR_ROWS=428

STATE=$LOG/groups.env
: > "$STATE"
note() { echo "$1=$2" >> "$STATE"; echo "[verifier] $1=$2"; }

# ---- 1. submission inventory and anti-cheat ---------------------------------------------
if [ ! -d "$SUB" ]; then
    note build_ok false
    note failure_reason "no submission at $SUB"
    python3 /tests/score.py "$STATE" "$LOG" ; exit 0
fi
find "$SUB" -type f | sort > "$LOG/submission_files.txt"
diff -rq "$PRISTINE/crates/burn-store/src" "$SUB" > "$LOG/submission_diff.txt" 2>&1 || true
wc -l < "$LOG/submission_diff.txt" | xargs -I{} echo "changed_entries={}" >> "$STATE"

# Sources the verifier compiles and runs must not reach for the verifier's own files, spawn
# processes, or embed fixtures. Test directories are excluded because they are replaced.
ANTICHEAT_HITS=$(grep -rn -E \
    '/logs|/tests/|/opt/|manifest\.tsv|BURN_PT_|fixture_matrix|Command::new|process::Command|include_bytes!|include_str!|cfg!\(test\)' \
    "$SUB" --include='*.rs' \
    | grep -v -E '^[^:]*/(pytorch|safetensors)/tests/' \
    | grep -v -E '^[^:]*:[0-9]+:\s*//' || true)
if [ -n "$ANTICHEAT_HITS" ]; then
    printf '%s\n' "$ANTICHEAT_HITS" > "$LOG/anticheat_hits.txt"
    note anticheat_ok false
else
    note anticheat_ok true
fi

# ---- 2. clean-room test directories -----------------------------------------------------
rm -rf "$SUB/pytorch/tests" "$SUB/safetensors/tests"
mkdir -p "$SUB/pytorch" "$SUB/safetensors"
cp -r "$HIDDEN" "$SUB/pytorch/tests"
cp -r "$PRISTINE/crates/burn-store/src/safetensors/tests" "$SUB/safetensors/tests"
# The test module must be wired in; the crate author may have moved the declaration.
if ! grep -qE '^\s*(pub(\(crate\))?\s+)?mod\s+tests\s*;' "$SUB/pytorch/mod.rs" 2>/dev/null; then
    printf '\n#[cfg(test)]\nmod tests;\n' >> "$SUB/pytorch/mod.rs"
    note pytorch_tests_mod_appended true
fi
if ! grep -qE '^\s*(pub(\(crate\))?\s+)?mod\s+tests\s*;' "$SUB/safetensors/mod.rs" 2>/dev/null; then
    printf '\n#[cfg(test)]\nmod tests;\n' >> "$SUB/safetensors/mod.rs"
    note safetensors_tests_mod_appended true
fi
# Integration tests dir is pristine (not transferred); add the fixture harness.
cp "$HARNESS" "$REPO/crates/burn-store/tests/fixture_matrix.rs"
# Uploaded files may carry old mtimes; make cargo rebuild the crate and its dependents.
find "$SUB" "$REPO/crates/burn-store/tests" -type f -exec touch {} +
cargo clean --offline -p burn-store >/dev/null 2>&1 || true

# ---- 3. build ---------------------------------------------------------------------------
BUILD_START=$(date +%s)
timeout 2400 cargo test --offline --locked --no-run -p burn-store --lib --tests \
    > "$LOG/build.log" 2>&1
BUILD_RC=$?
timeout 900 cargo test --offline --locked --no-run -p pytorch-tests --tests \
    >> "$LOG/build.log" 2>&1
BUILD_RC2=$?
note build_seconds $(( $(date +%s) - BUILD_START ))
if [ $BUILD_RC -ne 0 ] || [ $BUILD_RC2 -ne 0 ]; then
    note build_ok false
    grep -E '^(error|warning: unused)' "$LOG/build.log" | head -40 > "$LOG/build_errors.txt"
    python3 /tests/score.py "$STATE" "$LOG" ; exit 0
fi
note build_ok true

# Parse libtest summaries: sums passed/failed/ignored over every "test result:" line.
summarize() {  # $1 = log file ; prints "passed failed ignored lines"
    awk '/^test result:/ {
            for (i = 1; i <= NF; i++) {
                if ($(i+1) == "passed;") p += $i;
                if ($(i+1) == "failed;") f += $i;
                if ($(i+1) == "ignored;") g += $i;
            } n++
        } END { printf "%d %d %d %d\n", p, f, g, n }' "$1"
}
run_group() {  # $1 name, $2 expected passed, $3 timeout, rest = command
    local name=$1 expected=$2 tmo=$3; shift 3
    timeout "$tmo" "$@" > "$LOG/$name.log" 2>&1
    local rc=$?
    read -r p f g n <<< "$(summarize "$LOG/$name.log")"
    note "${name}_passed" "$p"; note "${name}_failed" "$f"; note "${name}_ignored" "$g"; note "${name}_rc" "$rc"
    if [ "$rc" -eq 0 ] && [ "$p" -eq "$expected" ] && [ "$f" -eq 0 ] && [ "$g" -eq 0 ]; then
        note "${name}_ok" true
    else
        note "${name}_ok" false
        grep -E '^test .* (FAILED|ignored)$|^---- .* stdout ----$|panicked at' "$LOG/$name.log" | head -60 > "$LOG/${name}_failures.txt"
    fi
}

# ---- 4. hidden and upstream test groups ---------------------------------------------------
run_group pytorch_unit "$EXPECTED_PYTORCH_UNIT" 900 \
    cargo test --offline --locked -p burn-store --lib -- 'pytorch::tests::' --test-threads=2
run_group safetensors_unit "$EXPECTED_SAFETENSORS_UNIT" 600 \
    cargo test --offline --locked -p burn-store --lib -- 'safetensors::tests::' --test-threads=2
run_group integration "$EXPECTED_INTEGRATION" 600 \
    cargo test --offline --locked -p burn-store \
        --test apply_dtype --test apply_unwind --test burnpack_store \
        --test direct_writer --test safetensors_atomic_save
run_group pytorch_tests_crate "$EXPECTED_PYTORCH_TESTS_CRATE" 600 \
    cargo test --offline --locked -p pytorch-tests --tests

# ---- 5. fixture matrix (PyTorch as the reference) -----------------------------------------
ACCEPT_ROWS=$(grep -c $'^entries\t' "$FIX/manifest.tsv")
META_ROWS=$(grep -c $'^meta\t' "$FIX/manifest.tsv")
PICKLE_ROWS=$(grep -c $'^pickle_int\t' "$FIX/manifest.tsv")
TENSOR_ROWS=$(grep -c $'^tensor\t' "$FIX/manifest.tsv")
REJECT_ROWS=$(grep -c $'^reject\t' "$FIX/manifest.tsv")
note manifest_accept_files "$META_ROWS"; note manifest_reject_files "$REJECT_ROWS"; note manifest_tensor_rows "$TENSOR_ROWS"
if [ "$META_ROWS" -ne "$EXPECTED_ACCEPT_FILES" ] || [ "$REJECT_ROWS" -ne "$EXPECTED_REJECT_FILES" ] || [ "$TENSOR_ROWS" -ne "$EXPECTED_TENSOR_ROWS" ]; then
    note manifest_ok false
else
    note manifest_ok true
fi

: > "$LOG/fixture_matrix.txt"
BURN_PT_FIXTURES=$FIX BURN_PT_RESULTS=$LOG/fixture_matrix.txt \
    timeout 900 cargo test --offline --locked -p burn-store --test fixture_matrix \
        -- --exact matrix_accepts --test-threads=1 --nocapture > "$LOG/matrix.log" 2>&1
MATRIX_RC=$?
MATRIX_PASS=$(grep -c '^PASS ' "$LOG/fixture_matrix.txt")
MATRIX_FAIL=$(grep -c '^FAIL ' "$LOG/fixture_matrix.txt")
MATRIX_EXPECTED=$(( ACCEPT_ROWS + META_ROWS + PICKLE_ROWS ))
note matrix_rc "$MATRIX_RC"; note matrix_pass "$MATRIX_PASS"; note matrix_fail "$MATRIX_FAIL"; note matrix_expected "$MATRIX_EXPECTED"
if [ "$MATRIX_RC" -eq 0 ] && [ "$MATRIX_FAIL" -eq 0 ] && [ "$MATRIX_PASS" -eq "$MATRIX_EXPECTED" ]; then
    note matrix_ok true
else
    note matrix_ok false
fi

# ---- 6. reject cases, one process each, bounded address space -----------------------------
BIN=$(ls -t "$REPO"/target/debug/deps/fixture_matrix-* 2>/dev/null | grep -v -E '\.(d|rlib|rmeta)$' | head -1)
: > "$LOG/reject_cases.txt"
REJECT_PASS=0; REJECT_FAIL=0
if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
    note reject_binary_missing true
else
    while IFS=$'\t' read -r kind file phase notetext; do
        [ "$kind" = "reject" ] || continue
        BURN_PT_FIXTURES=$FIX BURN_PT_RESULTS=$LOG/reject_cases.txt \
        BURN_PT_REJECT_FILE=$file BURN_PT_REJECT_PHASE=$phase \
            timeout 180 bash -c 'ulimit -v 6291456; exec "$0" --exact reject_case --test-threads=1 --nocapture' "$BIN" \
            > "$LOG/reject_$file.log" 2>&1
        rc=$?
        if [ $rc -eq 0 ] && grep -q "^PASS REJECT $file " "$LOG/reject_cases.txt"; then
            REJECT_PASS=$((REJECT_PASS + 1))
        else
            REJECT_FAIL=$((REJECT_FAIL + 1))
            grep -q "^FAIL REJECT $file " "$LOG/reject_cases.txt" \
                || echo "FAIL REJECT $file phase=$phase: process exit $rc (crash, abort or timeout)" >> "$LOG/reject_cases.txt"
        fi
    done < "$FIX/manifest.tsv"
fi
note reject_pass "$REJECT_PASS"; note reject_fail "$REJECT_FAIL"; note reject_expected "$REJECT_ROWS"
if [ "$REJECT_FAIL" -eq 0 ] && [ "$REJECT_PASS" -eq "$REJECT_ROWS" ]; then
    note reject_ok true
else
    note reject_ok false
fi

python3 /tests/score.py "$STATE" "$LOG"
exit 0
