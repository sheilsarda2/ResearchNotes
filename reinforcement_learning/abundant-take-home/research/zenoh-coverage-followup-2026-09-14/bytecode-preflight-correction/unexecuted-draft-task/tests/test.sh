#!/bin/bash
# Verifier for rs-zenoh-timestamp-instrumentation.
#
# Clean-room rebuild: the agent's `src/` directories of four crates arrive (via Harbor
# artifacts) at /workspace/repo/<crate>/src. They are overlaid onto the pristine base tree
# at /workspace/build (which owns the warm target dir); everything else (Cargo manifests,
# lockfile, test directories, test helpers, examples) stays pristine. Hidden tests are then
# added and the whole thing is built with `cargo --offline`.
#
# Groups (all must pass for reward 1; per-group results go to /logs/verifier/score.json):
#   anti_cheat            transferred sources contain none of the banned patterns
#   build_a / build_b     `cargo test --no-run` for zenoh(+codec) and zenoh-ext
#   lib_test_names        every pristine in-crate test of zenoh/zenoh-codec still exists
#   hidden_pr_tests       upstream PR #2620 integration test: 47 tests
#   interop_gold          6 differential tests against the gold-built reference peer
#   parity_python         4 differential tests against zenoh-python built on the gold tree
#   zenoh_ext             authored AdvancedPublisher test (2) + upstream zenoh-ext/tests/advanced.rs
#   upstream_regressions  selected pristine suites + in-crate tests, exact pristine names
#
# Test execution policy:
#   * Every test binary runs with --test-threads=1: the upstream suites synchronize with
#     one-second sleeps and are load-sensitive on a 4 vCPU verifier.
#   * Exact names: the pristine `cargo test -- --list` output recorded at image build
#     (/opt/expected/*.list, captured with 2>&1 so cargo's per-binary headers are present)
#     must all pass, except tests upstream itself marks #[ignore] (*.ignored.list) and the
#     MULTICAST_ONLY tests below. A cargo exit code != 0 fails the group.
#   * Retry policy: if a group fails with at most MAX_RETRY_NAMES failed tests, each failed
#     test is rerun once, alone (--exact, single-threaded). The group passes only if every
#     rerun passes; the retried names are recorded in score.json ("retried").
#   * NEEDS_NETWORK_IFACE: upstream tests that cannot pass in this verifier's network
#     namespace (Harbor network_mode "no-network" = loopback only, no multicast-capable
#     interface). Two kinds, both verified on the PRISTINE tree in this image: they fail under
#     --network none and pass on the same image with a bridge network (see STATUS.md):
#       - IP multicast: default-config peers discover each other only via UDP multicast
#         scouting on 224.0.0.224 ("No such device"), or the test binds a udp/224.0.0.x link:
#         zenoh_session_multicast test_adminspace_read qos_pubsub qos_pubsub_overwrite_config
#         test_accept_replies test_queryable_different_sessions
#       - gossip-discovered peer links: peers listening on the default tcp/[::]:0 advertise no
#         locator when loopback is the only interface, so the peer<->peer links these tests
#         wait for never form: gossip three_node_combination test_advanced_late_joiner
#     They are skipped (--skip, made exact-match by --exact so e.g. gossip_regression_* still
#     run) and removed from the expected names (--exclude); every other test in their binaries
#     still runs and must pass. Nothing about the timestamp feature is asserted by these tests.
set -uo pipefail

export CARGO_NET_OFFLINE=true CARGO_INCREMENTAL=0 CARGO_PROFILE_DEV_DEBUG=line-tables-only
export CARGO_TERM_COLOR=never RUST_BACKTRACE=1 CARGO_TARGET_DIR=/workspace/build/target
export VERIFIER_LOGS=/logs/verifier
LOGS=/logs/verifier
mkdir -p "$LOGS"
echo 0 > "$LOGS/reward.txt"
rm -f "$LOGS/score_parts.jsonl"

SUB=/workspace/repo
BUILD=/workspace/build
PRISTINE=/opt/pristine
HIDDEN=/tests/hidden
EXPECTED=/opt/expected
SCORE="python3 /tests/score.py"
CRATES=(commons/zenoh-protocol commons/zenoh-codec zenoh zenoh-ext)
PKG_A=(-p zenoh -p zenoh-codec --features zenoh/test,zenoh/unstable,zenoh/internal)
PKG_B=(-p zenoh-ext --features unstable,internal)
REGRESSION=(session routing queryable attachments source_info qos unicity matching adminspace)
REGRESSION_TARGETS=(--lib --test codec); for t in "${REGRESSION[@]}"; do REGRESSION_TARGETS+=(--test "$t"); done
NEEDS_NETWORK_IFACE_A=(zenoh_session_multicast test_adminspace_read qos_pubsub qos_pubsub_overwrite_config
                       test_accept_replies test_queryable_different_sessions gossip three_node_combination)
NEEDS_NETWORK_IFACE_B=(test_advanced_late_joiner)
SKIP_A=(); EXCLUDE_A=(); SKIP_B=(); EXCLUDE_B=()
for t in "${NEEDS_NETWORK_IFACE_A[@]}"; do SKIP_A+=(--skip "$t"); EXCLUDE_A+=(--exclude "$t"); done
for t in "${NEEDS_NETWORK_IFACE_B[@]}"; do SKIP_B+=(--skip "$t"); EXCLUDE_B+=(--exclude "$t"); done
HIDDEN_PR_COUNT=47
INTEROP_COUNT=6
PARITY_COUNT=4
TS_ADVANCED_COUNT=2
MAX_RETRY_NAMES=5
REQUIRED_GROUPS=(anti_cheat build_a build_b lib_test_names hidden_pr_tests robustness_tests admin_timestamp_tests interop_gold parity_python zenoh_ext upstream_regressions)

START=$(date +%s)
DEADLINE=$((START + 3450))   # leave headroom inside the 3600 s verifier budget
log() { echo "[verifier $(( $(date +%s) - START ))s] $*"; }
budget() { # budget <cap_secs> -> min(cap, time left)
  local left=$(( DEADLINE - $(date +%s) )); local cap=$1
  if (( left < cap )); then cap=$left; fi
  if (( cap < 30 )); then cap=30; fi
  echo "$cap"
}
finish() { $SCORE finalize "${REQUIRED_GROUPS[@]}"; cat "$LOGS/reward.txt"; exit 0; }
fail_rest() { for g in "$@"; do $SCORE record "$g" fail reason='"not run: earlier group failed"'; done; finish; }

cd "$BUILD" || { $SCORE record build_a fail reason='"no build tree"'; fail_rest lib_test_names hidden_pr_tests robustness_tests admin_timestamp_tests interop_gold parity_python zenoh_ext upstream_regressions build_b anti_cheat; }

# ---------------------------------------------------------------- anti-cheat
log "anti-cheat scan of transferred sources"
if ! $SCORE anticheat "$SUB" "$PRISTINE" "${CRATES[@]}" > "$LOGS/anticheat.json"; then
  cat "$LOGS/anticheat.json"
  fail_rest build_a build_b lib_test_names hidden_pr_tests robustness_tests admin_timestamp_tests interop_gold parity_python zenoh_ext upstream_regressions
fi

# ---------------------------------------------------------------- clean-room overlay
log "overlaying submitted src/ dirs onto the pristine tree"
for c in "${CRATES[@]}"; do
  rm -rf "$BUILD/$c/src"
  cp -a "$SUB/$c/src" "$BUILD/$c/src"
  # The build tree carries a warm target/ from the image build; copied sources may carry older
  # mtimes than the compiled artifacts, and cargo would then treat the pristine crate as fresh.
  find "$BUILD/$c/src" -type f -exec touch {} +
done
# Everything the verifier executes is pristine or hidden: restore test dirs and helpers.
for d in zenoh/tests zenoh-ext/tests commons/zenoh-codec/tests commons/zenoh-test commons/zenoh-protocol/tests; do
  if [ -d "$PRISTINE/$d" ]; then rsync -a --delete "$PRISTINE/$d/" "$BUILD/$d/"; fi
done
for f in Cargo.toml Cargo.lock rust-toolchain.toml zenoh/Cargo.toml zenoh-ext/Cargo.toml commons/zenoh-codec/Cargo.toml commons/zenoh-protocol/Cargo.toml; do
  cp -a "$PRISTINE/$f" "$BUILD/$f"
done
cp "$HIDDEN/zenoh/tests/timestamp_instrumentation.rs" "$BUILD/zenoh/tests/"
cp "$HIDDEN/zenoh/tests/timestamp_robustness.rs" "$BUILD/zenoh/tests/"
cp "$HIDDEN/zenoh/tests/timestamp_adminspace.rs" "$BUILD/zenoh/tests/"
cp "$HIDDEN/zenoh/tests/timestamp_adminspace_reply_stack.rs" "$BUILD/zenoh/tests/"
cp "$HIDDEN/zenoh/tests/ts_interop.rs" "$BUILD/zenoh/tests/"
cp "$HIDDEN/zenoh-ext/tests/ts_advanced.rs" "$BUILD/zenoh-ext/tests/"

# ---------------------------------------------------------------- build
log "build A: zenoh + zenoh-codec test targets"
timeout -k 30 "$(budget 1800)" cargo test --offline --no-run "${PKG_A[@]}" \
  --test timestamp_instrumentation --test timestamp_robustness \
  --test timestamp_adminspace --test timestamp_adminspace_reply_stack \
  --test ts_interop "${REGRESSION_TARGETS[@]}" \
  > "$LOGS/build_a.log" 2>&1
rc=$?
if (( rc != 0 )); then
  tail -n 80 "$LOGS/build_a.log"
  $SCORE record build_a fail exit_code="$rc"
  fail_rest build_b lib_test_names hidden_pr_tests robustness_tests admin_timestamp_tests interop_gold parity_python zenoh_ext upstream_regressions
fi
$SCORE record build_a pass seconds="$(( $(date +%s) - START ))"

log "build B: zenoh-ext test targets"
timeout -k 30 "$(budget 900)" cargo test --offline --no-run "${PKG_B[@]}" \
  --test advanced --test ts_advanced > "$LOGS/build_b.log" 2>&1
rc=$?
if (( rc != 0 )); then
  tail -n 80 "$LOGS/build_b.log"
  $SCORE record build_b fail exit_code="$rc"
else
  $SCORE record build_b pass
fi

# ---------------------------------------------------------------- in-crate test names preserved
log "checking that every pristine in-crate test still exists"
cargo test --offline "${PKG_A[@]}" --lib -- --list > "$LOGS/lib_actual.list" 2>&1
python3 - "$EXPECTED/group_a.list" "$LOGS/lib_expected.list" <<'PY'
# keep only the --lib ("Running unittests ...") binaries of the pristine list for comparison
import re, sys
src = open(sys.argv[1], errors="replace").read().splitlines()
out, keep = [], False
for line in src:
    if re.match(r"^\s*Running ", line):
        keep = "unittests" in line
    if keep:
        out.append(line)
open(sys.argv[2], "w").write("\n".join(out) + "\n")
PY
if $SCORE check-list "$LOGS/lib_expected.list" "$LOGS/lib_actual.list" > "$LOGS/lib_test_names.json"; then
  $SCORE record lib_test_names pass
else
  cat "$LOGS/lib_test_names.json"
  $SCORE record lib_test_names fail
fi

# run_group <group> <cap_secs> <parse-run expectations...> -- <cargo pkg args...> -- <cargo target args...> -- <libtest args...>
run_group() {
  local group=$1 cap=$2; shift 2
  local expect=(); while [ "$1" != "--" ]; do expect+=("$1"); shift; done; shift
  local pkg=();    while [ "$1" != "--" ]; do pkg+=("$1"); shift; done; shift
  local target=(); while [ "$1" != "--" ]; do target+=("$1"); shift; done; shift
  local libtest=("$@")
  log "running group $group"
  timeout -k 30 "$(budget "$cap")" cargo test --offline --no-fail-fast "${pkg[@]}" "${target[@]}" -- "${libtest[@]}" \
    > "$LOGS/$group.log" 2>&1
  local rc=$?
  if $SCORE parse-run "$LOGS/$group.log" "${expect[@]}" --exit-code "$rc" --json "$LOGS/$group.json" > "$LOGS/$group.summary" 2>&1; then
    $SCORE record "$group" pass exit_code="$rc"
    return 0
  fi
  cat "$LOGS/$group.summary"
  # Retry policy (see header): rerun each failed test once, alone; all must pass.
  local -a failed=(); mapfile -t failed < <($SCORE failed-bins "$LOGS/$group.log")
  local names_json; names_json=$(printf '%s\n' "${failed[@]}" | python3 -c 'import json,sys; print(json.dumps([l.replace("\t","::") for l in sys.stdin.read().splitlines() if l]))')
  local ok=1
  if (( ${#failed[@]} == 0 || ${#failed[@]} > MAX_RETRY_NAMES )); then ok=0; fi
  if (( ok == 1 )); then
    log "retrying ${#failed[@]} failed test(s) of $group once, individually"
    local pair bin name tflag rlog
    for pair in "${failed[@]}"; do
      bin=${pair%%$'\t'*}; name=${pair#*$'\t'}
      case "$bin" in zenoh|zenoh_codec) tflag=(--lib) ;; *) tflag=(--test "$bin") ;; esac
      rlog="$LOGS/${group}_retry_${bin}_${name//[^A-Za-z0-9_]/_}.log"
      timeout -k 30 "$(budget 300)" cargo test --offline "${pkg[@]}" "${tflag[@]}" -- --exact "$name" --test-threads=1 \
        > "$rlog" 2>&1
      if grep -qF -- "test $name ... ok" "$rlog"; then log "retry ok: $bin $name"; else log "retry failed: $bin $name"; ok=0; fi
    done
  fi
  if (( ok == 1 )); then
    $SCORE record "$group" pass exit_code="$rc" retried="$names_json"
    return 0
  fi
  $SCORE record "$group" fail exit_code="$rc" failed="$names_json"
  return 1
}

# ---------------------------------------------------------------- hidden PR tests
run_group hidden_pr_tests 900 --expect "timestamp_instrumentation=$HIDDEN_PR_COUNT" -- \
  "${PKG_A[@]}" -- --test timestamp_instrumentation -- --test-threads=1

# Follow-up contracts 6 and 7; every original group is still required.
run_group robustness_tests 180 --expect "timestamp_robustness=5" -- \
  "${PKG_A[@]}" -- --test timestamp_robustness -- --test-threads=1
run_group admin_timestamp_tests 180 --expect "timestamp_adminspace=1" \
    --expect "timestamp_adminspace_reply_stack=1" -- \
  "${PKG_A[@]}" -- --test timestamp_adminspace --test timestamp_adminspace_reply_stack -- --test-threads=1

# ---------------------------------------------------------------- differential interop
export TS_PEER_BIN=/opt/gold/bin/ts_peer
export TS_PY_PEER="/opt/zpy/bin/python3 /tests/py_peer.py"
run_group interop_gold 600 --expect "ts_interop=$INTEROP_COUNT" -- \
  "${PKG_A[@]}" -- --test ts_interop -- --test-threads=1 interop_
run_group parity_python 600 --expect "ts_interop=$PARITY_COUNT" -- \
  "${PKG_A[@]}" -- --test ts_interop -- --test-threads=1 pyparity_

# ---------------------------------------------------------------- zenoh-ext
if grep -q '"group": "build_b", "pass": true' "$LOGS/score_parts.jsonl"; then
  run_group zenoh_ext 900 --expect "ts_advanced=$TS_ADVANCED_COUNT" \
      --expect-list "$EXPECTED/group_b.list" --expect-ignored "$EXPECTED/group_b.ignored.list" "${EXCLUDE_B[@]}" -- \
    "${PKG_B[@]}" -- --test ts_advanced --test advanced -- --test-threads=1 --exact "${SKIP_B[@]}"
else
  $SCORE record zenoh_ext fail reason='"build_b failed"'
fi

# ---------------------------------------------------------------- upstream regressions (exact pristine names)
run_group upstream_regressions 1500 \
    --expect-list "$EXPECTED/group_a.list" --expect-ignored "$EXPECTED/group_a.ignored.list" "${EXCLUDE_A[@]}" -- \
  "${PKG_A[@]}" -- "${REGRESSION_TARGETS[@]}" -- --test-threads=1 --exact "${SKIP_A[@]}"

finish
