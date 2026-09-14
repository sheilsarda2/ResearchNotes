#!/bin/bash
# ws-build: build the rosbag2 overlay workspace at /workspace/ws in a clean shell.
#
#   ws-build                      # the task's package set (see DEFAULT_PKGS)
#   ws-build rosbag2_cpp ...      # only the named packages (colcon --packages-select)
#
# colcon must not run in a shell that already has the overlay sourced (this image
# sources it in every bash via BASH_ENV), so the build is executed under `env -i`
# with only the ROS underlay sourced. Parallelism is capped for an 8 GB host:
# WS_COLCON_WORKERS (default 2) packages at once, each with make -j WS_MAKE_JOBS
# (default 2). Warm build/ and install/ directories make rebuilds incremental.
set -euo pipefail
WS=${WS:-/workspace/ws}
DEFAULT_PKGS="lz4_cmake_module zstd_cmake_module mcap_vendor rosbag2_interfaces rosbag2_test_msgdefs rosbag2_test_common rosbag2_storage rosbag2_storage_sqlite3 rosbag2_storage_mcap rosbag2_storage_default_plugins rosbag2_cpp rosbag2_compression rosbag2_compression_zstd rosbag2_transport rosbag2_py ros2bag"
PKGS="${*:-$DEFAULT_PKGS}"
exec env -i \
  HOME="${HOME:-/root}" TERM="${TERM:-dumb}" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  MAKEFLAGS="-j${WS_MAKE_JOBS:-2}" COLCON_WORKERS="${WS_COLCON_WORKERS:-2}" \
  RMW_IMPLEMENTATION=rmw_fastrtps_cpp WS="$WS" PKGS="$PKGS" \
  bash --noprofile --norc -c '
    set -euo pipefail
    set +u; source /opt/ros/rolling/setup.bash; set -u  # ROS setup scripts are not set -u safe
    cd "$WS"
    colcon build --merge-install --parallel-workers "$COLCON_WORKERS" \
      --event-handlers console_cohesion+ \
      --packages-select $PKGS \
      --cmake-args -DBUILD_TESTING=ON -DDISABLE_SANITIZERS=ON -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
  '
