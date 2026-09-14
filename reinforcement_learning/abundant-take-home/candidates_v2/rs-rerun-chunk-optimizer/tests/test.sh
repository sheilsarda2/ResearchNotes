#!/bin/bash
# Verifier for rs-rerun-chunk-optimizer.
#
# Clean-room protocol:
#   1. Harbor has uploaded the agent's three `src/` directories to their original paths under
#      /workspace/repo. Everything else in /workspace/repo is reset to the pristine tree kept at
#      /opt/pristine (manifests, Cargo.lock, tests/, benches/, every other crate).
#   2. The hidden tests (the upstream PR's test files plus authored contract tests) are copied
#      into the pristine `tests/` directories.
#   3. Anti-cheat greps over the transferred sources.
#   4. One offline, locked `cargo test --no-run` with the same package selection the image was
#      warmed with (`-p re_chunk_optimizer -p re_log_encoding -p re_chunk_store`); the test
#      executables are then run directly (no feature-unification drift).
#   5. Exact per-binary test counts are required; skips/ignores fail; reward is binary and
#      score.json carries per-group attribution.
set -uo pipefail

LOGDIR=/logs/verifier
mkdir -p "$LOGDIR"
echo 0 > "$LOGDIR/reward.txt"

REPO=/workspace/repo
PRISTINE=/opt/pristine
HIDDEN=/tests/hidden
export CARGO_NET_OFFLINE=true CI=true INSTA_UPDATE=no \
       CARGO_TERM_COLOR=never RUST_BACKTRACE=1

ARTIFACT_DIRS=(
  crates/store/re_chunk_optimizer/src
  crates/store/re_log_encoding/src
  crates/store/re_chunk_store/src
)

# Expected test binaries and their exact test counts (0 ignored, 0 failed required).
declare -A EXPECTED=(
  [optimize]=22
  [optimizer_contract]=15
  [analysis]=5
  [footers_and_manifests]=10
  [arrow_encode_roundtrip]=1
  [compact]=18
  [correctness]=4
  [dataframe]=3
  [drop_time_range]=1
  [formatting]=1
  [gc]=5
  [reads]=10
  [stats]=1
)
# Which package each target belongs to (for reporting only).
declare -A GROUP=(
  [optimize]=pr_tests
  [footers_and_manifests]=pr_tests
  [optimizer_contract]=contract_tests
  [analysis]=upstream_regression
  [arrow_encode_roundtrip]=upstream_regression
  [compact]=upstream_regression
  [correctness]=upstream_regression
  [dataframe]=upstream_regression
  [drop_time_range]=upstream_regression
  [formatting]=upstream_regression
  [gc]=upstream_regression
  [reads]=upstream_regression
  [stats]=upstream_regression
)

SCORE_JSON="$LOGDIR/score.json"
python3 - "$SCORE_JSON" <<'PY'
import json, sys
json.dump({"anticheat": None, "build": None, "tests": {}, "groups": {}, "reward": 0}, open(sys.argv[1], "w"), indent=1)
PY

update_score() { # key json-value
  python3 - "$SCORE_JSON" "$1" "$2" <<'PY'
import json, sys
p, key, val = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
d = json.load(open(p))
cur = d
parts = key.split(".")
for k in parts[:-1]:
    cur = cur.setdefault(k, {})
cur[parts[-1]] = val
json.dump(d, open(p, "w"), indent=1)
PY
}

fail() { # reason
  echo "VERIFIER FAIL: $1" | tee -a "$LOGDIR/verifier.log"
  update_score "failure_reason" "\"$1\""
  echo 0 > "$LOGDIR/reward.txt"
  exit 0
}

cd "$REPO" || fail "repo missing"

# ---------------------------------------------------------------- 1. clean-room overlay
# Snapshot the transferred sources, reset the tree from pristine, put the sources back.
STAGE=$(mktemp -d)
for d in "${ARTIFACT_DIRS[@]}"; do
  if [ -d "$REPO/$d" ]; then
    mkdir -p "$STAGE/$d" && rsync -a "$REPO/$d/" "$STAGE/$d/"
  else
    echo "warning: transferred directory $d is missing; pristine sources will be used" | tee -a "$LOGDIR/verifier.log"
  fi
done
rsync -a --delete --exclude='/target' "$PRISTINE/" "$REPO/" || fail "pristine restore failed"
for d in "${ARTIFACT_DIRS[@]}"; do
  if [ -d "$STAGE/$d" ]; then
    rm -rf "$REPO/$d" && mkdir -p "$REPO/$d" && rsync -a "$STAGE/$d/" "$REPO/$d/"
  fi
done
rm -rf "$STAGE"
# insta `.snap` files under src/**/snapshots are test expectations, not implementation: always take
# the pristine copies so a submission cannot rewrite them, and drop any snapshot dir the submission added.
for d in "${ARTIFACT_DIRS[@]}"; do
  find "$REPO/$d" -type d -name snapshots -prune -exec rm -rf {} + 2>/dev/null || true
  ( cd "$PRISTINE" && find "$d" -type d -name snapshots -prune -print 2>/dev/null ) | while read -r snapdir; do
    mkdir -p "$REPO/$snapdir" && rsync -a "$PRISTINE/$snapdir/" "$REPO/$snapdir/"
  done
done

# ---------------------------------------------------------------- 2. hidden tests
install -m 0644 "$HIDDEN/optimize.rs"              crates/store/re_chunk_optimizer/tests/optimize.rs
install -m 0644 "$HIDDEN/optimizer_contract.rs"    crates/store/re_chunk_optimizer/tests/optimizer_contract.rs
install -m 0644 "$HIDDEN/footers_and_manifests.rs" crates/store/re_log_encoding/tests/footers_and_manifests.rs

# ---------------------------------------------------------------- 3. anti-cheat
AC_LOG="$LOGDIR/anticheat.txt"; : > "$AC_LOG"
# Only Rust sources may live in the transferred directories.
find "${ARTIFACT_DIRS[@]}" -type f ! -name '*.rs' ! -path '*/snapshots/*.snap' >> "$AC_LOG" 2>/dev/null
# Patterns that have no business in these library crates (zero hits in upstream sources).
grep -rnE --include='*.rs' \
  'std::process|process::Command|Command::new|include!\(|include_str!\(|include_bytes!\(|option_env!\(|[^_a-zA-Z]env!\(|#!?\[path|/logs/|reward\.txt|score\.json|std::net|libc::|cargo:rustc|#\[ctor|no_mangle|link_section|global_asm|std::os::unix::process' \
  "${ARTIFACT_DIRS[@]}" >> "$AC_LOG" 2>/dev/null
# Test-harness or snapshot tampering hooks.
grep -rnE --include='*.rs' 'INSTA_|insta::_macro_support|test_harness|#!\[test_runner|custom_test_frameworks' "${ARTIFACT_DIRS[@]}" >> "$AC_LOG" 2>/dev/null
if [ -s "$AC_LOG" ]; then
  update_score "anticheat" "{\"passed\": false, \"hits\": $(wc -l < "$AC_LOG")}"
  fail "anti-cheat patterns found in transferred sources (see anticheat.txt)"
fi
update_score "anticheat" '{"passed": true, "hits": 0}'

# ---------------------------------------------------------------- 4. build (offline, locked)
BUILD_JSON="$LOGDIR/build.json"; BUILD_LOG="$LOGDIR/build.log"
timeout 2400 cargo test --offline --locked -p re_chunk_optimizer -p re_log_encoding -p re_chunk_store --no-run \
  --message-format=json-render-diagnostics > "$BUILD_JSON" 2> "$BUILD_LOG"
BUILD_STATUS=$?
if [ "$BUILD_STATUS" -ne 0 ]; then
  update_score "build" "{\"passed\": false, \"exit\": $BUILD_STATUS}"
  tail -60 "$BUILD_LOG" | tee -a "$LOGDIR/verifier.log"
  fail "cargo test --no-run failed (exit $BUILD_STATUS)"
fi
update_score "build" '{"passed": true, "exit": 0}'

# Map test-target name -> executable, from cargo's JSON stream.
python3 - "$BUILD_JSON" "$LOGDIR/test_bins.txt" <<'PY'
import json, sys
out = open(sys.argv[2], "w")
for line in open(sys.argv[1]):
    try:
        m = json.loads(line)
    except Exception:
        continue
    if m.get("reason") == "compiler-artifact" and m.get("executable") and m.get("profile", {}).get("test"):
        if m["target"]["kind"] and m["target"]["kind"][0] == "test":
            out.write(f'{m["target"]["name"]}\t{m["executable"]}\n')
PY

# ---------------------------------------------------------------- 5. run test binaries
ALL_OK=1
for name in "${!EXPECTED[@]}"; do
  want=${EXPECTED[$name]}
  exe=$(awk -F'\t' -v n="$name" '$1==n{print $2}' "$LOGDIR/test_bins.txt" | head -1)
  OUT="$LOGDIR/test_${name}.log"
  if [ -z "$exe" ] || [ ! -x "$exe" ]; then
    echo "missing test binary for $name" | tee -a "$LOGDIR/verifier.log"
    update_score "tests.$name" "{\"passed\": false, \"reason\": \"binary missing\", \"expected\": $want}"
    ALL_OK=0; continue
  fi
  ( cd "$REPO" && timeout 900 "$exe" --test-threads=2 ) > "$OUT" 2>&1
  status=$?
  read -r passed failed ignored <<< "$(python3 - "$OUT" <<'PY'
import re, sys
txt = open(sys.argv[1], errors="replace").read()
m = re.search(r"test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored", txt)
print(f"{m.group(2)} {m.group(3)} {m.group(4)}" if m else "-1 -1 -1")
PY
)"
  ok=0
  if [ "$status" -eq 0 ] && [ "$passed" = "$want" ] && [ "$failed" = "0" ] && [ "$ignored" = "0" ]; then ok=1; fi
  [ "$ok" -eq 1 ] || ALL_OK=0
  update_score "tests.$name" "{\"passed\": $( [ $ok -eq 1 ] && echo true || echo false ), \"exit\": $status, \"tests_passed\": $passed, \"tests_failed\": $failed, \"tests_ignored\": $ignored, \"expected\": $want, \"group\": \"${GROUP[$name]}\"}"
  echo "[$name] exit=$status passed=$passed failed=$failed ignored=$ignored expected=$want ok=$ok" | tee -a "$LOGDIR/verifier.log"
  grep -E "^test .* (FAILED|ignored)$|panicked at" "$OUT" | head -40 >> "$LOGDIR/verifier.log"
done

# Per-group summary for attribution.
python3 - "$SCORE_JSON" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
groups = {}
for name, r in d["tests"].items():
    g = r.get("group", "other")
    groups.setdefault(g, {"passed": 0, "total": 0})
    groups[g]["total"] += 1
    groups[g]["passed"] += 1 if r.get("passed") else 0
d["groups"] = groups
json.dump(d, open(p, "w"), indent=1)
PY

if [ "$ALL_OK" -eq 1 ]; then
  update_score "reward" 1
  echo 1 > "$LOGDIR/reward.txt"
  echo "VERIFIER PASS" | tee -a "$LOGDIR/verifier.log"
else
  update_score "reward" 0
  echo 0 > "$LOGDIR/reward.txt"
  echo "VERIFIER FAIL: one or more test groups failed" | tee -a "$LOGDIR/verifier.log"
fi
exit 0
