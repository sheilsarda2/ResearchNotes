#!/bin/bash
# Verifier for cpp-rosbag2-mixed-serialization-playback.
#
# Runs in the separate, offline verifier container. Harbor has re-uploaded the transferred
# artifacts (the src/ and include/ directories of the six touched packages) to their original
# paths under /workspace/ws/src/rosbag2. Everything else in the tree is restored from the pristine
# copy, the PR's test-side diff is applied, the touched packages and their dependents are rebuilt,
# and four verification groups run:
#   gtests        the PR's own gtest suites plus pristine regression suites, exact case counts
#   differential  sqlite3 vs mcap storage plugins on synthetic mixed-format bags (C++ harness)
#   cli_play      `ros2 bag play` behaviour on those bags, messages received by a subscriber
#   build/overlay/anticheat bookkeeping
# Writes /logs/verifier/reward.txt (0/1) and /logs/verifier/score.json (per-group attribution).
set -uo pipefail

LOG=/logs/verifier
mkdir -p "$LOG" "$LOG/gtest"
echo 0 > "$LOG/reward.txt"
rm -f "$LOG/score.json"

WS=/workspace/ws
SRC=$WS/src/rosbag2
PRISTINE=/opt/pristine/rosbag2
SUB=/tmp/submission
WORK=/tmp/verifier_work
mkdir -p "$WORK"
# Do not depend on the caller's working directory: the source tree is deleted and recreated below,
# and a process whose cwd was inside it would fail with "Current working directory cannot be established".
cd "$WORK"

PKGS="rosbag2_storage rosbag2_storage_sqlite3 rosbag2_storage_mcap rosbag2_cpp rosbag2_compression rosbag2_transport"
REBUILD_PKGS="rosbag2_storage rosbag2_storage_sqlite3 rosbag2_storage_mcap rosbag2_storage_default_plugins rosbag2_cpp rosbag2_compression rosbag2_compression_zstd rosbag2_transport rosbag2_py ros2bag"
SCORE="python3 /tests/score.py"

export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ASAN_OPTIONS=detect_leaks=0
unset ROS_DOMAIN_ID

note() { echo "[verifier $(date -u +%H:%M:%S)] $*"; }
finish() { $SCORE finalize; exit 0; }

# ---------------------------------------------------------------------------------------------
# 1. Stash the transferred sources exactly as uploaded.
# ---------------------------------------------------------------------------------------------
rm -rf "$SUB"; mkdir -p "$SUB"
transferred=0
for p in $PKGS; do
  for d in src include; do
    if [ -d "$SRC/$p/$d" ]; then
      mkdir -p "$SUB/$p"
      cp -a "$SRC/$p/$d" "$SUB/$p/$d"
      transferred=$((transferred + 1))
    fi
  done
done
note "transferred directories: $transferred"
if [ "$transferred" -eq 0 ]; then
  $SCORE record overlay 0 "no transferred source directories found"
  finish
fi

# ---------------------------------------------------------------------------------------------
# 2. Anti-cheat: the submission must not touch the verifier, spawn processes, or reference the
#    hidden harness. None of these patterns occur in the pristine trees of the six packages.
# ---------------------------------------------------------------------------------------------
cheat_hits=""
for pat in '/logs' 'reward' 'score\.json' 'atexit' 'std::system' 'system(' 'popen(' 'execv' 'execl' 'fork(' \
           'LD_PRELOAD' '/tests' 'hidden_tests' 'gtest' 'GTEST' 'ctest' 'dlopen(' 'constructor))' \
           'test_results' '\.gtest\.xml' 'mixed_formats_differential' 'cli_play_check' 'ASAN_OPTIONS' 'getenv('; do
  hits=$(grep -rIl -- "$pat" "$SUB" 2>/dev/null | head -5 | tr '\n' ' ')
  if [ -n "$hits" ]; then cheat_hits="$cheat_hits [$pat: $hits]"; fi
done
# Symlinks could point the build at files outside the transferred directories.
links=$(find "$SUB" -type l | head -5 | tr '\n' ' ')
if [ -n "$links" ]; then cheat_hits="$cheat_hits [symlinks: $links]"; fi
if [ -n "$cheat_hits" ]; then
  $SCORE record anticheat 0 "suspicious patterns:$cheat_hits"
  finish
fi
$SCORE record anticheat 1 "no forbidden patterns in transferred sources"

# ---------------------------------------------------------------------------------------------
# 3. Clean room: pristine tree, then only the transferred src/ and include/ directories replaced.
#    CMakeLists, package.xml, test/ and every other package stay pristine.
# ---------------------------------------------------------------------------------------------
rm -rf "$SRC"
cp -a "$PRISTINE" "$SRC" || { $SCORE record overlay 0 "could not restore pristine tree"; finish; }
for p in $PKGS; do
  for d in src include; do
    if [ -d "$SUB/$p/$d" ]; then
      rm -rf "$SRC/$p/$d"
      cp -a "$SUB/$p/$d" "$SRC/$p/$d"
    fi
  done
done

# The PR's test-side diff: new and extended gtest cases, the mock reader, one test link line.
if ! (cd "$SRC" && git apply --whitespace=nowarn /tests/hidden_tests.patch) > "$LOG/hidden_tests_apply.log" 2>&1; then
  if ! (cd "$SRC" && patch -p1 --forward < /tests/hidden_tests.patch) >> "$LOG/hidden_tests_apply.log" 2>&1; then
    $SCORE record overlay 0 "hidden test patch did not apply (verifier bug, see hidden_tests_apply.log)"
    finish
  fi
fi

# Uploaded files may carry mtimes older than the warm objects; force recompilation of everything
# in the transferred directories and of the patched tests.
for p in $PKGS; do
  for d in src include test; do
    [ -d "$SRC/$p/$d" ] && find "$SRC/$p/$d" -type f -exec touch {} +
  done
done
touch "$SRC/rosbag2_transport/CMakeLists.txt"
$SCORE record overlay 1 "pristine tree + $transferred transferred directories + hidden tests"

# ---------------------------------------------------------------------------------------------
# 4. Rebuild the touched packages and their dependents (rosbag2_py and ros2bag included, since
#    `ros2 bag play` must run against the submission).
# ---------------------------------------------------------------------------------------------
note "building: $REBUILD_PKGS"
timeout 5400 ws-build $REBUILD_PKGS > "$LOG/build.log" 2>&1
BUILD_RC=$?
if [ "$BUILD_RC" -ne 0 ]; then
  tail -n 60 "$LOG/build.log" || true
  $SCORE record build 0 "colcon build failed (rc=$BUILD_RC), see build.log"
  finish
fi
$SCORE record build 1 "colcon build of touched packages and dependents succeeded"

set +u; source /opt/ros/rolling/setup.bash; set -u  # ROS setup scripts are not set -u safe
set +u; source "$WS/install/setup.bash"; set -u  # ROS setup scripts are not set -u safe
# ---------------------------------------------------------------------------------------------
# 5. gtests: the PR's suites and pristine regressions, exact counts. Each suite runs through
#    `ctest -R '^name$'` in its package build dir, i.e. through ament's run_test.py wrapper with the
#    environment the upstream CMake gives it (APPEND_LIBRARY_DIRS / APPEND_ENV AMENT_PREFIX_PATH for
#    the build-tree-only test plugins: rosbag2_cpp's converter test plugin, rosbag2_compression's fake
#    compressor, rosbag2_storage's test plugin; ENV RMW_IMPLEMENTATION), and ament writes the gtest
#    XML to build/<pkg>/test_results/<pkg>/<name>.gtest.xml. Running the binaries directly cannot
#    load those plugins. Each suite gets its own ROS_DOMAIN_ID (32.., all <= 101 so the DDS ports
#    stay clear of the Linux ephemeral-port range); playback tests must not see one another.
# ---------------------------------------------------------------------------------------------
domain=31
run_gtest() {  # pkg test_name timeout_sec
  local pkg=$1 test=$2 to=$3 xml
  mkdir -p "$LOG/gtest/$pkg"
  # Suites registered per RMW implementation are named <target>__<rmw> by ament_add_gmock_test.
  if ! (cd "$WS/build/$pkg" 2>/dev/null && ctest -N -R "^$test(__.*)?\$" 2>/dev/null | grep -q "Total Tests: 1"); then
    echo "test $pkg/$test is not registered with ctest (package or test target not built)" > "$LOG/gtest/$pkg/$test.log"
    return
  fi
  domain=$((domain + 1))
  note "gtest $pkg/$test (domain $domain)"
  rm -f "$WS/build/$pkg/test_results/$pkg/$test.gtest.xml" "$WS/build/$pkg/test_results/$pkg/${test}__"*.gtest.xml
  (cd "$WS/build/$pkg" && ROS_DOMAIN_ID=$domain timeout "$to" ctest -R "^$test(__.*)?\$" --output-on-failure) \
      > "$LOG/gtest/$pkg/$test.log" 2>&1
  echo "exit=$?" >> "$LOG/gtest/$pkg/$test.log"
  xml=$(find "$WS/build/$pkg/test_results" \( -name "$test.gtest.xml" -o -name "${test}__*.gtest.xml" \) 2>/dev/null | head -1)
  if [ -n "$xml" ]; then cp -f "$xml" "$LOG/gtest/$pkg/$test.xml"; fi
}
run_gtest rosbag2_storage_sqlite3 test_sqlite_storage 600
run_gtest rosbag2_storage_sqlite3 test_sqlite_topic_filter 600
run_gtest rosbag2_storage_mcap test_mcap_storage 600
run_gtest rosbag2_storage_mcap test_mcap_topic_filter 600
run_gtest rosbag2_cpp test_sequential_reader 900
run_gtest rosbag2_cpp test_serialization_converter 600
run_gtest rosbag2_cpp test_multifile_reader 600
run_gtest rosbag2_cpp test_converter_factory 600
run_gtest rosbag2_cpp test_storage_without_metadata_file 600
run_gtest rosbag2_cpp test_sequential_writer 900
run_gtest rosbag2_compression test_sequential_compression_reader 600
run_gtest rosbag2_compression test_sequential_compression_writer 900
run_gtest rosbag2_transport test_readers_manager 600
run_gtest rosbag2_transport test_rewrite 900
run_gtest rosbag2_transport test_play 1800
$SCORE gtests /tests/expected_counts.json "$LOG/gtest"

# ---------------------------------------------------------------------------------------------
# 6. Storage-plugin differential: one C++ harness, linked against the rebuilt overlay, writes the
#    same synthetic mixed-format bags with the sqlite3 and the mcap plugin and dumps what the
#    reader API returns. The two dumps must agree with each other and with the contract.
# ---------------------------------------------------------------------------------------------
note "building differential harness"
rm -rf "$WORK/diff_build" "$WORK/diff"; mkdir -p "$WORK/diff"
if cmake -S /tests/differential -B "$WORK/diff_build" -DCMAKE_BUILD_TYPE=Release > "$LOG/differential_build.log" 2>&1 \
   && cmake --build "$WORK/diff_build" -j2 >> "$LOG/differential_build.log" 2>&1; then
  for s in sqlite3 mcap; do
    note "differential harness: $s"
    (cd "$WORK" && ROS_DOMAIN_ID=51 timeout 600 "$WORK/diff_build/mixed_formats_differential" "$WORK/diff" "$s") \
        > "$LOG/differential_$s.log" 2>&1
    echo "exit=$?" >> "$LOG/differential_$s.log"
    cp -f "$WORK/diff/$s.json" "$LOG/differential_$s.json" 2>/dev/null || true
  done
  $SCORE differential "$WORK/diff/sqlite3.json" "$WORK/diff/mcap.json"
else
  tail -n 40 "$LOG/differential_build.log" || true
  $SCORE record differential 0 "differential harness did not compile/link against the submission"
fi

# ---------------------------------------------------------------------------------------------
# 7. CLI playback: `ros2 bag play` from the rebuilt ros2bag/rosbag2_py on the bags above, for both
#    storage plugins, with an rclpy subscriber counting what is actually published.
# ---------------------------------------------------------------------------------------------
if [ -d "$WORK/diff/bags/sqlite3" ] && [ -d "$WORK/diff/bags/mcap" ]; then
  note "cli playback checks"
  ROS_DOMAIN_ID=61 timeout 1500 python3 /tests/cli_play_check.py --bags "$WORK/diff/bags" --out "$LOG/cli_play.json" \
      > "$LOG/cli_play.log" 2>&1
  echo "exit=$?" >> "$LOG/cli_play.log"
  $SCORE cli "$LOG/cli_play.json"
else
  $SCORE record cli_play 0 "bags for the CLI check were not produced by the differential harness"
fi

finish
