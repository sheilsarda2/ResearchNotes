#!/bin/bash
# ws-test: run gtest executables of one package from the warm build directory.
#
#   ws-test rosbag2_cpp                          # every gtest/gmock test of the package
#   ws-test rosbag2_cpp test_sequential_reader   # one executable, extra args go to gtest
#   ws-test rosbag2_transport test_play --gtest_filter='*serialization*'
#
# Runs the executable directly (what ament's run_test.py wrapper does), with the
# workspace sourced and a per-invocation ROS_DOMAIN_ID so playback tests do not see
# stray publishers. Without a test name, ctest is used to run the package's tests
# (linters are skipped).
set -euo pipefail
WS=${WS:-/workspace/ws}
PKG=${1:?usage: ws-test <package> [test_executable] [gtest args...]}
shift || true
set +u; source /opt/ros/rolling/setup.bash; set -u  # ROS setup scripts are not set -u safe
set +u; source "$WS/install/setup.bash"; set -u  # ROS setup scripts are not set -u safe
export RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}
export ROS_AUTOMATIC_DISCOVERY_RANGE=${ROS_AUTOMATIC_DISCOVERY_RANGE:-LOCALHOST}
export ROS_DOMAIN_ID=$(( ($$ % 70) + 30 ))  # 30..99: below the Linux ephemeral-port band
export ASAN_OPTIONS=${ASAN_OPTIONS:-detect_leaks=0}
cd "$WS/build/$PKG"
if [ $# -eq 0 ]; then
  exec ctest --output-on-failure -E '^(cppcheck|cpplint|uncrustify|lint_cmake|xmllint|copyright|flake8|pep257|clang_format)$'
fi
TEST=$1; shift
exec "./$TEST" "$@"
