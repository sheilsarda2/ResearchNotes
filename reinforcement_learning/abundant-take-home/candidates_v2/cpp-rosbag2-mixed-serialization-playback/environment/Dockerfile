# Agent environment: ros2/rosbag2 at 0580af8a26da42443a2df678e5d1346abc1a9046 (rolling, 2026-09-07),
# built from source as a colcon overlay on the official ROS Rolling image (Ubuntu 26.04 "resolute",
# ros-base variant, image created 2026-09-09; rolling debs rosbag2 0.34.0 / rclcpp 33.0.4).
# Network is used only during this build (apt, rosdep, mcap_vendor FetchContent). Runtime is offline.
FROM ros:rolling@sha256:7fd952a7f1f87c990f7b55f1e9cdf4e267010e52efd990665a94f7e41c7ce561

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 ROS_DISTRO=rolling

# 1. Remove the binary rosbag2 stack that ros-base ships. rosbag2's DEVELOPING.md calls a source
#    overlay on top of the ros-*-rosbag2 debs undefined behaviour (headers may be picked up from
#    the debs), so the source overlay is the only rosbag2 in the image.
RUN apt-get update \
 && dpkg-query -W -f='${Package}\n' 'ros-rolling-rosbag2*' 'ros-rolling-ros2bag' 'ros-rolling-mcap-vendor' 'ros-rolling-ros-base' 2>/dev/null \
      | xargs -r apt-get purge -y \
 && apt-get install -y --no-install-recommends procps patch less vim-tiny \
 && rm -rf /var/lib/apt/lists/*

# 2. Source tree at the base commit (clean `git archive`, prefix rosbag2/).
WORKDIR /workspace/ws
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN mkdir -p src && tar -xzf /tmp/upstream.tar.gz -C src && rm /tmp/upstream.tar.gz

# 3. Every build, run and test dependency of the packages that get built, resolved by rosdep from
#    the rolling apt repository (test_msgs, ament_cmake_gmock, pybind11, sqlite3, zstd, lz4, ...).
#    Keys satisfied inside the source tree are ignored, so no rosbag2 deb comes back.
RUN apt-get update && rosdep update --rosdistro rolling \
 && cd src/rosbag2 && rosdep install --from-paths \
      lz4_cmake_module zstd_cmake_module mcap_vendor rosbag2_interfaces rosbag2_test_msgdefs \
      rosbag2_test_common rosbag2_storage rosbag2_storage_sqlite3 rosbag2_storage_mcap \
      rosbag2_storage_default_plugins rosbag2_cpp rosbag2_compression rosbag2_compression_zstd \
      rosbag2_transport rosbag2_py ros2bag rosbag2_tests \
      --ignore-src -y --rosdistro rolling \
 && rm -rf /var/lib/apt/lists/*

# 4. Warm colcon build of the task's package set, tests included (BUILD_TESTING=ON), merged install.
#    2 colcon workers x make -j2 keeps peak memory under 8 GB; see STATUS.md for measured times.
COPY build_ws.sh /usr/local/bin/ws-build
COPY test_ws.sh /usr/local/bin/ws-test
RUN chmod 0755 /usr/local/bin/ws-build /usr/local/bin/ws-test \
 && ws-build \
 && rm -rf /workspace/ws/log

# 5. Shell environment. Every bash (interactive, login, or `bash -c` via BASH_ENV) sees the ROS
#    underlay and the workspace overlay, plus the RMW settings used by the verifier.
RUN bash -c 'source /opt/ros/rolling/setup.bash && source /workspace/ws/install/setup.bash \
      && for v in AMENT_PREFIX_PATH CMAKE_PREFIX_PATH COLCON_PREFIX_PATH LD_LIBRARY_PATH PATH PYTHONPATH ROS_DISTRO ROS_VERSION ROS_PYTHON_VERSION; do \
           printf "export %s=%q\n" "$v" "${!v:-}"; done > /etc/ros_ws_env.sh' \
 && printf '%s\n' \
      'export RMW_IMPLEMENTATION=rmw_fastrtps_cpp' \
      'export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST' \
      'export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-21}"' \
      'export ASAN_OPTIONS="${ASAN_OPTIONS:-detect_leaks=0}"' \
      'export WS=/workspace/ws' \
      >> /etc/ros_ws_env.sh \
 && echo 'source /etc/ros_ws_env.sh' >> /etc/bash.bashrc \
 && echo 'source /etc/ros_ws_env.sh' > /etc/profile.d/ros_ws.sh \
 && printf '#!/bin/bash\nset -e\nsource /etc/ros_ws_env.sh\nexec "$@"\n' > /ros_ws_entrypoint.sh \
 && chmod 0755 /ros_ws_entrypoint.sh
ENV BASH_ENV=/etc/ros_ws_env.sh
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST ASAN_OPTIONS=detect_leaks=0

WORKDIR /workspace/ws/src/rosbag2
ENTRYPOINT ["/ros_ws_entrypoint.sh"]
CMD ["bash"]
