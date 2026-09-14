#!/bin/bash
# Verifier for cpp-zenoh-cpp-connectivity-api. Runs in the separate, network-less verifier container.
#
# Groups (all must pass for reward 1; each is recorded in /logs/verifier/score.json):
#   anti_cheat           transferred include/ is .hxx-only, no symlinks, suspicious-token counts <= pristine
#   build                clean-room rebuild of every test target (both backends) from pristine + submitted include/
#   router               zenohd 1.8.0 accepted connections on ZENOH_TEST_ROUTER
#   registered_tests     ctest registered exactly hidden/expected_tests.txt (32 tests)
#   pr_tests             upstream PR #750 connectivity test, zenoh-c and zenoh-pico builds, all 10 sub-tests
#   regression_zenohc    every other zenoh-c test (upstream suites + strict-warnings build), exact count
#   regression_zenohpico every other zenoh-pico test, exact count
#   authored             interop_optional (as_moved_c_ptr(std::optional<...>) contract)
#   parity               C++ connectivity dump == zenoh-python 1.8.0 dump against the same router
set -uo pipefail

LOG=/logs/verifier
mkdir -p "$LOG"
echo 0 > "$LOG/reward.txt"

SUB=/workspace/repo/include          # Harbor uploads the [[artifacts]] entry to its original source path
REPO=/work/repo
PRISTINE=/opt/pristine
HID=/tests/hidden
export ZENOH_TEST_ROUTER="${ZENOH_TEST_ROUTER:-tcp/127.0.0.1:27447}"
export LD_LIBRARY_PATH="/opt/zenoh/lib:/opt/zenoh/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
ROUTER_PORT="${ZENOH_TEST_ROUTER##*:}"
export RUST_LOG="${RUST_LOG:-error}"

echo "[verifier] start $(date -u +%FT%TZ)"

# 1. Submission inventory and anti-cheat scan --------------------------------------------------
AC_OK=0
if [ -d "$SUB" ]; then
    if python3 "$HID/anti_cheat.py" check "$SUB" "$PRISTINE/token_baseline.json" > "$LOG/anti_cheat.json"; then AC_OK=1; fi
else
    echo '{"ok": false, "problems": ["no submission directory at /workspace/repo/include"]}' > "$LOG/anti_cheat.json"
fi
echo "[verifier] anti_cheat ok=$AC_OK"

# 2. Clean-room overlay: pristine tests, submitted headers ---------------------------------------
rm -rf "$REPO/include" "$REPO/tests"
cp -a "$PRISTINE/tests" "$REPO/tests"
if [ -d "$SUB" ]; then cp -a "$SUB" "$REPO/include"; else cp -a "$PRISTINE/include" "$REPO/include"; fi
diff -rq "$PRISTINE/include" "$REPO/include" > "$LOG/include_diff.txt" 2>&1 || true
diff -ru "$PRISTINE/include" "$REPO/include" > "$LOG/include_diff.patch" 2>&1 || true

# 3. Build every test target for both backends -----------------------------------------------------
BUILD_OK=0
if timeout 600 cmake -S "$REPO" -B "$REPO/build" > "$LOG/cmake_configure.log" 2>&1 \
   && timeout 1500 cmake --build "$REPO/build" --target tests -j4 -- -k > "$LOG/build.log" 2>&1; then
    BUILD_OK=1
fi
echo "[verifier] build ok=$BUILD_OK"

# 4. Router ---------------------------------------------------------------------------------------
/opt/zenoh/bin/zenohd -l "$ZENOH_TEST_ROUTER" --no-multicast-scouting --cfg 'scouting/gossip/enabled:false' \
    > "$LOG/zenohd.log" 2>&1 &
ZPID=$!
ROUTER_OK=0
python3 - "$ROUTER_PORT" <<'PY' && ROUTER_OK=1
import socket, sys, time
port = int(sys.argv[1]); deadline = time.time() + 60
while time.time() < deadline:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close(); sys.exit(0)
    except OSError:
        time.sleep(0.2)
sys.exit(1)
PY
echo "[verifier] router ok=$ROUTER_OK"

# 5. Registered test list must be exactly the expected one ----------------------------------------
(cd "$REPO/build" && ctest -N | sed -n 's/^ *Test *#[0-9]*: *//p' | LC_ALL=C sort) > "$LOG/registered_tests.txt" 2>/dev/null
LIST_OK=0
if LC_ALL=C sort "$HID/expected_tests.txt" | diff -u - "$LOG/registered_tests.txt" > "$LOG/registered_tests.diff" 2>&1; then LIST_OK=1; fi
echo "[verifier] registered_tests ok=$LIST_OK"

# 6. ctest, serial (fixed ports), verbose log for marker parsing, JUnit for status ----------------
CTEST_RC=99
if [ "$ROUTER_OK" = 1 ]; then
    (cd "$REPO/build" && timeout 2100 ctest -j1 -V --timeout 300 --output-junit "$LOG/ctest.xml" > "$LOG/ctest.log" 2>&1)
    CTEST_RC=$?
fi
echo "[verifier] ctest rc=$CTEST_RC"

# 7. Cross-language parity against zenoh-python ---------------------------------------------------
PARITY_OK=0
PARITY_BIN="$(find "$REPO/build" -type f -name parity_dump_zenohc -perm -u+x | head -n 1)"
if [ "$ROUTER_OK" = 1 ] && [ -n "$PARITY_BIN" ]; then
    timeout 120 "$PARITY_BIN" > "$LOG/parity_cpp.json" 2> "$LOG/parity_cpp.err"
    timeout 120 /opt/venv/bin/python "$HID/parity_dump.py" > "$LOG/parity_py.json" 2> "$LOG/parity_py.err"
    if python3 "$HID/compare_parity.py" "$LOG/parity_cpp.json" "$LOG/parity_py.json" > "$LOG/parity_result.json" 2>&1; then
        PARITY_OK=1
    fi
else
    echo '{"ok": false, "checks": [{"check": "parity_binary_built_and_router_up", "ok": false}]}' > "$LOG/parity_result.json"
fi
echo "[verifier] parity ok=$PARITY_OK"

ROUTER_ALIVE=0
kill -0 "$ZPID" 2>/dev/null && ROUTER_ALIVE=1
kill "$ZPID" 2>/dev/null; wait "$ZPID" 2>/dev/null

# 8. Score and reward --------------------------------------------------------------------------------
python3 - "$LOG" "$HID/expected_tests.txt" "$AC_OK" "$BUILD_OK" "$ROUTER_OK" "$LIST_OK" "$CTEST_RC" "$PARITY_OK" "$ROUTER_ALIVE" <<'PY'
import json, os, re, sys
import xml.etree.ElementTree as ET

log, expected_path = sys.argv[1], sys.argv[2]
ac_ok, build_ok, router_ok, list_ok = (sys.argv[3] == "1"), (sys.argv[4] == "1"), (sys.argv[5] == "1"), (sys.argv[6] == "1")
ctest_rc, parity_ok, router_alive = int(sys.argv[7]), (sys.argv[8] == "1"), (sys.argv[9] == "1")

expected = sorted(l.strip() for l in open(expected_path) if l.strip())

# JUnit: name -> passed
results = {}
try:
    root = ET.parse(os.path.join(log, "ctest.xml")).getroot()
    for tc in root.iter("testcase"):
        name = tc.get("name")
        status = tc.get("status", "")
        failed = tc.find("failure") is not None or tc.find("error") is not None or tc.find("skipped") is not None
        results[name] = (status == "run") and not failed
except Exception as e:  # noqa: BLE001
    results = {}
    junit_error = repr(e)
else:
    junit_error = None

# Connectivity sub-test markers from the verbose ctest log: "<n>: === test_x ===" / "<n>: PASS".
markers = {}
try:
    num_to_name = {}
    per_test_lines = {}
    for line in open(os.path.join(log, "ctest.log"), errors="replace"):
        m = re.match(r"\s*Start\s+(\d+): (\S+)", line)
        if m:
            num_to_name[m.group(1)] = m.group(2)
            continue
        m = re.match(r"(\d+): (.*)$", line.rstrip("\n"))
        if m:
            per_test_lines.setdefault(m.group(1), []).append(m.group(2))
    for num, name in num_to_name.items():
        if "connectivity" not in name:
            continue
        lines = per_test_lines.get(num, [])
        headers = [l for l in lines if re.match(r"=== test_\w+ ===", l.strip())]
        passes = [l for l in lines if l.strip() == "PASS"]
        done = any("All connectivity tests passed!" in l for l in lines)
        markers[name] = {"sub_tests": len(headers), "passes": len(passes), "all_passed_line": done,
                         "ok": len(headers) == 10 and len(passes) == 10 and done}
except Exception as e:  # noqa: BLE001
    markers = {"error": repr(e)}

def group(names):
    detail = {n: results.get(n, False) for n in names}
    return {"ok": len(names) > 0 and all(detail.values()), "expected": len(names), "passed": sum(detail.values()), "tests": detail}

pr_names = [n for n in expected if "connectivity" in n]
authored_names = [n for n in expected if "interop_optional" in n]
reg_c = [n for n in expected if n.endswith("_zenohc") and n not in pr_names and n not in authored_names]
reg_p = [n for n in expected if n.endswith("_zenohpico") and n not in pr_names]

pr = group(pr_names)
pr["markers"] = markers
pr["ok"] = pr["ok"] and all(isinstance(markers.get(n), dict) and markers[n].get("ok") for n in pr_names)

groups = {
    "anti_cheat": {"ok": ac_ok, "report": "anti_cheat.json"},
    "build": {"ok": build_ok, "log": "build.log"},
    "router": {"ok": router_ok, "alive_after_run": router_alive},
    "registered_tests": {"ok": list_ok, "expected_count": len(expected), "registered_count": len(results)},
    "pr_tests": pr,
    "regression_zenohc": group(reg_c),
    "regression_zenohpico": group(reg_p),
    "authored": group(authored_names),
    "parity": {"ok": parity_ok, "report": "parity_result.json"},
}
try:
    groups["parity"]["checks"] = json.load(open(os.path.join(log, "parity_result.json"))).get("checks")
except Exception:  # noqa: BLE001
    pass

reward = all(g["ok"] for g in groups.values()) and ctest_rc == 0 and len(results) == len(expected)
score = {
    "reward": 1 if reward else 0,
    "ctest_exit_code": ctest_rc,
    "junit_error": junit_error,
    "tests_expected": len(expected),
    "tests_reported": len(results),
    "tests_passed": sum(1 for v in results.values() if v),
    "groups": groups,
}
json.dump(score, open(os.path.join(log, "score.json"), "w"), indent=1, sort_keys=True)
open(os.path.join(log, "reward.txt"), "w").write("1\n" if reward else "0\n")
print("[verifier] reward=%d groups=%s" % (1 if reward else 0, {k: v["ok"] for k, v in groups.items()}))
PY

echo "[verifier] end $(date -u +%FT%TZ)"
exit 0
