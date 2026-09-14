#!/bin/bash
# Verifier for rs-mcap-cli-recover-parity (separate, offline verifier container).
#
# Harbor re-materializes the two transferred artifacts at their original paths:
#   /workspace/repo/rust/cli/src   and   /workspace/repo/rust/mcap/src
# Everything else under /workspace/repo is the pristine warm build tree from the image.
# Steps: anti-cheat scan -> restore every non-transferred path from /opt/pristine ->
# offline clean-room build -> upstream mcap integration tests (restored from pristine) ->
# agent's CLI unit tests -> differential harness against the Go reference CLI.
set -uo pipefail
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
SCORE=/logs/verifier/score.json
REPO=/workspace/repo
PRISTINE=/opt/pristine/repo
REF=/opt/reference/bin
export CARGO_NET_OFFLINE=true
export CARGO_TERM_COLOR=never
export NO_COLOR=1

python3 - <<'PY'
import json
json.dump({"groups": {}, "reward": 0}, open("/logs/verifier/score.json", "w"))
PY

record() {  # record <group> <passed 0/1> <detail-json-or-string>
python3 - "$1" "$2" "$3" <<'PY'
import json, sys
p = "/logs/verifier/score.json"
s = json.load(open(p))
detail = sys.argv[3]
try:
    detail = json.loads(detail)
except Exception:
    pass
s["groups"][sys.argv[1]] = {"passed": sys.argv[2] == "1", "detail": detail}
json.dump(s, open(p, "w"), indent=1)
PY
}

fail_out() { echo "VERIFIER: $1"; exit 0; }

# ------------------------------------------------------------------ 0. artifacts present
for d in rust/cli/src rust/mcap/src; do
  if [ ! -d "$REPO/$d" ]; then record build 0 "missing transferred directory $d"; fail_out "missing $d"; fi
done
[ -f "$REPO/rust/cli/src/main.rs" ] || { record build 0 "rust/cli/src/main.rs missing"; fail_out "no main.rs"; }
[ -f "$REPO/rust/mcap/src/lib.rs" ] || { record build 0 "rust/mcap/src/lib.rs missing"; fail_out "no lib.rs"; }

# ------------------------------------------------------------------ 1. anti-cheat scan
AC_OK=1; AC_MSG=""
if find "$REPO/rust/cli/src" "$REPO/rust/mcap/src" -type l | grep -q .; then AC_OK=0; AC_MSG="symlink in transferred sources"; fi
if grep -rInE 'mcap-go|/opt/reference|/opt/pristine|test-read-conformance|process::Command|Command::new\(|/logs/|reward\.txt|score\.json|/tests/|include_bytes!\("/|include_str!\("/' \
     "$REPO/rust/cli/src" "$REPO/rust/mcap/src" > /logs/verifier/anticheat-hits.txt 2>/dev/null; then
  AC_OK=0; AC_MSG="forbidden pattern in sources (see anticheat-hits.txt)"
fi
record anticheat "$AC_OK" "{\"ok\": $AC_OK, \"msg\": \"$AC_MSG\"}"
[ "$AC_OK" = 1 ] || fail_out "anti-cheat failed: $AC_MSG"

# ------------------------------------------------------------------ 2. restore everything except the two src dirs
# (Cargo manifests, build.rs, tests/, examples/, benches/, corpus). The agent cannot change
# the build recipe, add dependencies, or edit tests.
cp -f "$PRISTINE/rust/Cargo.toml" "$REPO/rust/Cargo.toml"
cp -f "$PRISTINE/rust/Cargo.lock" "$REPO/rust/Cargo.lock"
cp -f "$PRISTINE/rust/cli/Cargo.toml" "$REPO/rust/cli/Cargo.toml"
cp -f "$PRISTINE/rust/cli/build.rs" "$REPO/rust/cli/build.rs"
cp -f "$PRISTINE/rust/mcap/Cargo.toml" "$REPO/rust/mcap/Cargo.toml"
rm -rf "$REPO/rust/mcap/tests" "$REPO/rust/mcap/examples" "$REPO/rust/mcap/benches" "$REPO/rust/cli/tests"
cp -a "$PRISTINE/rust/mcap/tests" "$REPO/rust/mcap/tests"
cp -a "$PRISTINE/rust/mcap/examples" "$REPO/rust/mcap/examples"
cp -a "$PRISTINE/rust/mcap/benches" "$REPO/rust/mcap/benches"
rm -rf "$REPO/tests/conformance/data" && mkdir -p "$REPO/tests/conformance" && cp -a "$PRISTINE/tests/conformance/data" "$REPO/tests/conformance/data"
rm -rf "$REPO/.cargo"

# ------------------------------------------------------------------ 3. clean-room offline build
cd "$REPO/rust"
if ! cargo build --offline --locked -p mcap-cli > /logs/verifier/build.log 2>&1; then
  record build 0 "$(tail -c 1500 /logs/verifier/build.log | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
  fail_out "build failed"
fi
RUST_BIN="$REPO/rust/target/debug/mcap"
[ -x "$RUST_BIN" ] || { record build 0 "binary missing after build"; fail_out "no binary"; }
"$RUST_BIN" --version > /logs/verifier/version.txt 2>&1 || true
record build 1 "{\"version\": $(python3 -c 'import json; print(json.dumps(open("/logs/verifier/version.txt").read().strip()))')}"

# ------------------------------------------------------------------ 4. upstream mcap crate integration tests (pristine)
MCAP_TESTS=(attachment chunks compression flush handles_time0_messages message metadata round_trip)
EXPECTED_MCAP_TESTS=17
args=(); for t in "${MCAP_TESTS[@]}"; do args+=(--test "$t"); done
cargo test --offline --locked -p mcap --all-features "${args[@]}" -- --test-threads=2 > /logs/verifier/mcap-tests.log 2>&1
MCAP_RC=$?
MCAP_PASSED=$(grep -E '^test result:' /logs/verifier/mcap-tests.log | sed -E 's/.* ([0-9]+) passed.*/\1/' | paste -sd+ - | bc 2>/dev/null || echo 0)
MCAP_FAILED=$(grep -E '^test result:' /logs/verifier/mcap-tests.log | sed -E 's/.* ([0-9]+) failed.*/\1/' | paste -sd+ - | bc 2>/dev/null || echo 999)
MCAP_IGNORED=$(grep -E '^test result:' /logs/verifier/mcap-tests.log | sed -E 's/.* ([0-9]+) ignored.*/\1/' | paste -sd+ - | bc 2>/dev/null || echo 999)
MCAP_OK=0
if [ "$MCAP_RC" = 0 ] && [ "${MCAP_PASSED:-0}" = "$EXPECTED_MCAP_TESTS" ] && [ "${MCAP_FAILED:-1}" = 0 ] && [ "${MCAP_IGNORED:-1}" = 0 ]; then MCAP_OK=1; fi
record upstream_mcap_tests "$MCAP_OK" "{\"rc\": $MCAP_RC, \"passed\": ${MCAP_PASSED:-0}, \"failed\": ${MCAP_FAILED:-0}, \"ignored\": ${MCAP_IGNORED:-0}, \"expected\": $EXPECTED_MCAP_TESTS}"

# ------------------------------------------------------------------ 5. CLI crate unit tests (agent-authored, must build and pass)
cargo test --offline --locked -p mcap-cli -- --test-threads=2 > /logs/verifier/cli-tests.log 2>&1
CLI_RC=$?
CLI_PASSED=$(grep -E '^test result:' /logs/verifier/cli-tests.log | sed -E 's/.* ([0-9]+) passed.*/\1/' | paste -sd+ - | bc 2>/dev/null || echo 0)
CLI_FAILED=$(grep -E '^test result:' /logs/verifier/cli-tests.log | sed -E 's/.* ([0-9]+) failed.*/\1/' | paste -sd+ - | bc 2>/dev/null || echo 999)
CLI_OK=0; [ "$CLI_RC" = 0 ] && [ "${CLI_FAILED:-1}" = 0 ] && CLI_OK=1
record cli_unit_tests "$CLI_OK" "{\"rc\": $CLI_RC, \"passed\": ${CLI_PASSED:-0}, \"failed\": ${CLI_FAILED:-0}}"

# ------------------------------------------------------------------ 6. differential harness vs Go reference
timeout 2400 python3 /tests/harness/run_differential.py \
  --rust-bin "$RUST_BIN" --go-bin "$REF/mcap-go" --dump-bin "$REF/mcap-go-dump" \
  --fixtures /tests/fixtures --report /logs/verifier/differential.json > /logs/verifier/differential.log 2>&1
DIFF_RC=$?
if [ -f /logs/verifier/differential.json ]; then
  python3 - <<'PY'
import json
s = json.load(open("/logs/verifier/score.json"))
r = json.load(open("/logs/verifier/differential.json"))
for g, v in r["groups"].items():
    s["groups"]["diff:" + g] = {"passed": v["passed"] == v["total"], "detail": v}
s["groups"]["differential_total"] = {"passed": r["all_passed"], "detail": {"ran": r["ran"], "expected_total": r["expected_total"], "passed": r["passed"], "elapsed_sec": r["elapsed_sec"]}}
json.dump(s, open("/logs/verifier/score.json", "w"), indent=1)
PY
else
  record differential_total 0 "{\"rc\": $DIFF_RC, \"msg\": \"harness produced no report\"}"
fi

# ------------------------------------------------------------------ 7. reward
python3 - <<'PY'
import json
p = "/logs/verifier/score.json"
s = json.load(open(p))
required = ["anticheat", "build", "upstream_mcap_tests", "cli_unit_tests", "differential_total"]
ok = all(s["groups"].get(k, {}).get("passed") for k in required) and all(v["passed"] for k, v in s["groups"].items() if k.startswith("diff:"))
s["reward"] = 1 if ok else 0
json.dump(s, open(p, "w"), indent=1)
open("/logs/verifier/reward.txt", "w").write("1\n" if ok else "0\n")
print("REWARD", s["reward"])
for k, v in s["groups"].items():
    print(f"  {k}: {'pass' if v['passed'] else 'FAIL'}")
PY
exit 0
