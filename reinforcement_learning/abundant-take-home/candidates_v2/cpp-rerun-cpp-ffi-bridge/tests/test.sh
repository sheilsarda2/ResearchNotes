#!/bin/bash
# Verifier for cpp-rerun-cpp-ffi-bridge.  Runs offline in the verifier image after Harbor re-materializes the
# submission at its source path (/workspace/repo/rerun_cpp/src).
#
# Groups (all must pass for reward 1; each is recorded in /logs/verifier/score.json):
#   restore          generated code, the C header dir, tests and docs restored from the pristine tree
#   anticheat        hand-written submitted sources contain none of the forbidden patterns
#   build            `snippets` and `rerun_sdk_tests` build from the submission (clean-room overlay, incremental)
#   catch2           rerun_sdk_tests: exactly EXPECTED_CATCH2_CASES test cases, all passed, 0 failures/errors
#   snippets_run     all planned C++ snippets exit 0 and write a recording (run as unprivileged user)
#   snippets_compare all planned comparisons match the Rust reference via `rerun rrd compare`
set -uo pipefail

REPO=/workspace/repo
PRISTINE=/workspace/pristine
REF=/opt/reference
OUT=/logs/verifier
WORK=/tmp/verify
EXPECTED_CATCH2_CASES=45
EXPECTED_RUN=112
EXPECTED_COMPARE=104

mkdir -p "$OUT" "$WORK"
echo 0 > "$OUT/reward.txt"
SCORE="$OUT/score.json"
python3 - "$SCORE" <<'PY'
import json, sys
json.dump({"groups": {}, "reward": 0}, open(sys.argv[1], "w"), indent=1)
PY

record() { # record <group> <passed:true|false> <json-details>
  python3 - "$SCORE" "$1" "$2" "$3" <<'PY'
import json, sys
path, group, passed, details = sys.argv[1:5]
score = json.load(open(path))
try:
    det = json.loads(details)
except Exception:
    det = {"note": details}
score["groups"][group] = {"passed": passed == "true", **det}
json.dump(score, open(path, "w"), indent=1)
PY
}

finish() { # finish <reward>
  python3 - "$SCORE" "$1" <<'PY'
import json, sys
path, reward = sys.argv[1:3]
score = json.load(open(path))
score["reward"] = int(reward)
json.dump(score, open(path, "w"), indent=1)
PY
  echo "$1" > "$OUT/reward.txt"
  echo "reward=$1"
  exit 0
}

SUB="$REPO/rerun_cpp/src"
if [ ! -d "$SUB" ]; then
  record build false '{"error": "submission directory /workspace/repo/rerun_cpp/src missing"}'
  finish 0
fi

# ---------------------------------------------------------------------------------------------------------------
# 1. Restore what the submission must not change, and record what it touched.
PSRC="$PRISTINE/rerun_cpp/src"
MODIFIED_PROTECTED=()
for protected in rerun/c rerun/archetypes rerun/components rerun/encodings rerun/blueprint rerun/datatypes.hpp; do
  if ! diff -rq "$PSRC/$protected" "$SUB/$protected" > /dev/null 2>&1; then
    MODIFIED_PROTECTED+=("$protected")
  fi
  rm -rf "$SUB/$protected"
  cp -a "$PSRC/$protected" "$SUB/$protected"
done
rm -rf "$REPO/rerun_cpp/tests" "$REPO/rerun_cpp/docs" "$REPO/docs/snippets"
cp -a "$PRISTINE/rerun_cpp/tests" "$REPO/rerun_cpp/tests"
cp -a "$PRISTINE/rerun_cpp/docs" "$REPO/rerun_cpp/docs"
cp -a "$PRISTINE/docs/snippets" "$REPO/docs/snippets"
# Files that differ from (or are new relative to) the pristine tree drive the incremental rebuild; ninja compares
# mtimes and the transferred files may carry old timestamps, so bump exactly those.
CHANGED=()
while IFS= read -r -d '' f; do
  rel=${f#"$SUB"/}
  if [ ! -f "$PSRC/$rel" ] || ! cmp -s "$f" "$PSRC/$rel"; then
    touch "$f"
    CHANGED+=("$rel")
  fi
done < <(find "$SUB" -type f -print0)
DELETED=()
while IFS= read -r -d '' f; do
  rel=${f#"$PSRC"/}
  [ -f "$SUB/$rel" ] || DELETED+=("$rel")
done < <(find "$PSRC" -type f -print0)
record restore true "$(python3 - "${#CHANGED[@]}" "${#DELETED[@]}" "${#MODIFIED_PROTECTED[@]}" "${CHANGED[@]}" -- "${DELETED[@]}" -- "${MODIFIED_PROTECTED[@]}" <<'PY'
import json, sys
n_changed, n_deleted, n_prot = map(int, sys.argv[1:4])
rest = sys.argv[4:]
changed = rest[:n_changed]; rest = rest[n_changed:]
assert rest[0] == "--"; rest = rest[1:]
deleted = rest[:n_deleted]; rest = rest[n_deleted:]
assert rest[0] == "--"; rest = rest[1:]
prot = rest[:n_prot]
print(json.dumps({"changed_or_added": changed, "deleted": deleted, "protected_dirs_modified_and_restored": prot}))
PY
)"

# ---------------------------------------------------------------------------------------------------------------
# 2. Anti-cheat on the hand-written part of the submission (the restored generated dirs and c/ are pristine and skipped):
#    the SDK never needs to spawn processes, scan directories, or know about verifier paths.
FORBIDDEN='/logs|reward\.txt|score\.json|/opt/reference|snippets_rust|/workspace/pristine|_RERUN_TEST_FORCE_SAVE|RERUN_FLUSH_NUM_ROWS|\bsystem[[:space:]]*\(|\bpopen[[:space:]]*\(|\bexecl[pe]?[[:space:]]*\(|\bexecv[pe]*[[:space:]]*\(|posix_spawn|\bfork[[:space:]]*\(|\bdlopen[[:space:]]*\(|directory_iterator|/proc/self|LD_PRELOAD|\.rrd"'
HITS=$(grep -rnIE --exclude-dir=archetypes --exclude-dir=components --exclude-dir=encodings --exclude-dir=blueprint --exclude-dir=c "$FORBIDDEN" "$SUB" 2>/dev/null | head -50)
if [ -n "$HITS" ]; then
  echo "$HITS" > "$OUT/anticheat_hits.txt"
  record anticheat false "$(python3 -c 'import json,sys; print(json.dumps({"hits": open(sys.argv[1]).read().splitlines()[:50]}))' "$OUT/anticheat_hits.txt")"
  finish 0
fi
BINARIES=$(find "$SUB" -type f \( -name '*.o' -o -name '*.a' -o -name '*.so' -o -name '*.rrd' \) | head -20)
if [ -n "$BINARIES" ]; then
  record anticheat false "{\"binaries\": $(printf '%s\n' "$BINARIES" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().split()))')}"
  finish 0
fi
record anticheat true '{}'

# ---------------------------------------------------------------------------------------------------------------
# 3. Build (incremental on the warm build dir; a header change may trigger a full SDK + snippets rebuild).
cd "$REPO"
BUILD_START=$(date +%s)
timeout 3000 cmake --build build/debug -j "${CMAKE_BUILD_PARALLEL_LEVEL:-4}" --target snippets rerun_sdk_tests > "$OUT/build.log" 2>&1
BUILD_STATUS=$?
BUILD_SECS=$(( $(date +%s) - BUILD_START ))
tail -n 60 "$OUT/build.log"
if [ $BUILD_STATUS -ne 0 ] || [ ! -x build/debug/docs/snippets/snippets ] || [ ! -x build/debug/rerun_cpp/tests/rerun_sdk_tests ]; then
  record build false "{\"exit_code\": $BUILD_STATUS, \"seconds\": $BUILD_SECS, \"log\": \"build.log\"}"
  finish 0
fi
# The build must link the prebuilt rerun_c and Arrow only (no reference binary, no extra shared objects).
if ldd build/debug/docs/snippets/snippets | grep -vE 'linux-vdso|libstdc\+\+|libm\.so|libgcc_s|libc\.so|libdl|libpthread|librt|ld-linux|libssl|libcrypto|libz\.so' | grep -q '=>'; then
  record build false "{\"error\": \"unexpected shared library dependency\", \"ldd\": $(ldd build/debug/docs/snippets/snippets | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}"
  finish 0
fi
record build true "{\"seconds\": $BUILD_SECS}"

# ---------------------------------------------------------------------------------------------------------------
# 4. Catch2 unit tests (restored from pristine; compiled against the submission's headers).
LISTED=$(RERUN_STRICT=1 timeout 300 ./build/debug/rerun_cpp/tests/rerun_sdk_tests --list-tests --verbosity quiet 2>/dev/null | grep -c .)
mkdir -p build/test_output
RERUN_STRICT=1 PYTHONWARNINGS=error timeout 1200 ./build/debug/rerun_cpp/tests/rerun_sdk_tests \
  --reporter "console::out=$OUT/catch2.log" --reporter "junit::out=$OUT/catch2.xml" > /dev/null 2> "$OUT/catch2.stderr"
CATCH_STATUS=$?
CATCH_JSON=$(python3 - "$OUT/catch2.log" "$OUT/catch2.xml" "$CATCH_STATUS" "$LISTED" "$EXPECTED_CATCH2_CASES" <<'PY'
import json, re, sys
import xml.etree.ElementTree as ET
log, xml_path, status, listed, expected = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
text = open(log, errors="replace").read()
# Catch2 v3 console summary has two shapes: with any failure/skip it prints
#   "test cases: 45 | 44 passed | 1 failed" / "assertions: N | M passed | K failed";
# when every test passes it prints only "All tests passed (N assertions in M test cases)".
m = re.search(r"test cases:\s*(\d+)\s*\|\s*(\d+)\s*passed", text)
ok_all = re.search(r"All tests passed \((\d+) assertions? in (\d+) test cases?\)", text)
if m:
    cases, passed = int(m.group(1)), int(m.group(2))
elif ok_all:
    cases = passed = int(ok_all.group(2))
else:
    cases = passed = -1
a = re.search(r"assertions:\s*(\d+)\s*\|\s*(\d+)\s*passed", text)
assertions = a.group(1) if a else (ok_all.group(1) if ok_all else None)
failures = errors = -1
try:
    root = ET.parse(xml_path).getroot()
    suites = list(root.iter("testsuite"))
    failures = sum(int(s.get("failures", 0)) for s in suites)
    errors = sum(int(s.get("errors", 0)) for s in suites)
except Exception:
    pass
ok = status == 0 and cases == expected and passed == expected and listed == expected and failures == 0 and errors == 0
print(json.dumps({"passed": ok, "exit_code": status, "listed_cases": listed, "expected_cases": expected,
                  "cases": cases, "cases_passed": passed, "assertions": assertions,
                  "junit_failures": failures, "junit_errors": errors}))
PY
)
if [ "$(echo "$CATCH_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["passed"])')" = "True" ]; then
  record catch2 true "$CATCH_JSON"; CATCH_OK=1
else
  record catch2 false "$CATCH_JSON"; CATCH_OK=0
fi

# ---------------------------------------------------------------------------------------------------------------
# 5. Snippets: run as the unprivileged `runner` user (reference recordings unreadable), then compare.
chmod -R o-rwx "$REF" "$PRISTINE"
PLAN_RUN=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run_count"])' "$REF/plan.json")
PLAN_CMP=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["compare_count"])' "$REF/plan.json")
# Re-derive the plan from the restored snippets.toml and require it to match the image-build plan exactly.
FRESH=$(python3 /tests/snippet_plan.py "$REPO" --json "$WORK/plan_fresh.json" | head -1)
FRESH_RUN=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run_count"])' "$WORK/plan_fresh.json")
FRESH_CMP=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["compare_count"])' "$WORK/plan_fresh.json")
rm -rf "$WORK/cpp" && mkdir -p "$WORK/cpp" && chown -R runner "$WORK/cpp"
timeout 2400 python3 /tests/run_snippets.py "$REPO" "$REF" "$WORK/cpp" "$OUT/snippets.json" --user runner --jobs 4 > "$OUT/snippets.log" 2>&1
SNIP_STATUS=$?
cat "$OUT/snippets.log" | tail -n 40
SNIP_JSON=$(python3 - "$OUT/snippets.json" "$SNIP_STATUS" "$EXPECTED_RUN" "$EXPECTED_COMPARE" "$PLAN_RUN" "$PLAN_CMP" "$FRESH_RUN" "$FRESH_CMP" <<'PY'
import json, sys
path, status, exp_run, exp_cmp, plan_run, plan_cmp, fresh_run, fresh_cmp = sys.argv[1], *map(int, sys.argv[2:9])
try:
    s = json.load(open(path))["summary"]
except Exception as e:
    s = {"run_ok": 0, "compare_ok": 0, "run_failed": [], "compare_failed": [], "error": str(e)}
plan_ok = plan_run == exp_run == fresh_run and plan_cmp == exp_cmp == fresh_cmp
run_ok = status == 0 and plan_ok and s["run_ok"] == exp_run
cmp_ok = status == 0 and plan_ok and s["compare_ok"] == exp_cmp
print(json.dumps({"run": {"passed": run_ok, "expected": exp_run, "planned": plan_run, "planned_fresh": fresh_run,
                          "ok": s["run_ok"], "failed": s["run_failed"][:40]},
                  "compare": {"passed": cmp_ok, "expected": exp_cmp, "planned": plan_cmp, "planned_fresh": fresh_cmp,
                              "ok": s["compare_ok"], "failed": s["compare_failed"][:40]},
                  "runner_exit_code": status}))
PY
)
RUN_OK=$(echo "$SNIP_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run"]["passed"])')
CMP_OK=$(echo "$SNIP_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["compare"]["passed"])')
record snippets_run "$([ "$RUN_OK" = True ] && echo true || echo false)" "$(echo "$SNIP_JSON" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["run"]))')"
record snippets_compare "$([ "$CMP_OK" = True ] && echo true || echo false)" "$(echo "$SNIP_JSON" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["compare"]))')"

# ---------------------------------------------------------------------------------------------------------------
if [ "$CATCH_OK" = 1 ] && [ "$RUN_OK" = True ] && [ "$CMP_OK" = True ]; then
  finish 1
fi
finish 0
