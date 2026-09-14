# Verifier image (Harbor separate verifier, run offline). Same base and warm workspace as the agent
# image, plus a pristine copy of the source tree for the clean-room overlay and the hidden harness.
# Build context: this tests/ directory; image-source/ holds byte-identical copies of the environment
# inputs (upstream.tar.gz, build_ws.sh, test_ws.sh).
#
# Every instruction up to and including the warm `ws-build` layer is identical to
# environment/Dockerfile (only the COPY source paths differ, which BuildKit keys by content), so the
# 40-70 minute colcon layer is shared through the Docker build cache instead of being built twice.
# Keep the two files in lock-step; verifier-specific layers come after the shared prefix.
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
COPY image-source/upstream.tar.gz /tmp/upstream.tar.gz
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
COPY image-source/build_ws.sh /usr/local/bin/ws-build
COPY image-source/test_ws.sh /usr/local/bin/ws-test
RUN chmod 0755 /usr/local/bin/ws-build /usr/local/bin/ws-test \
 && ws-build \
 && rm -rf /workspace/ws/log

# ---- verifier-specific layers below; everything above is shared with environment/Dockerfile ----

# 5. Pristine copy of the source tree for the clean-room overlay. Extracted from the same archive
#    (rather than copied from src/ before the build) so the shared prefix above stays identical.
COPY image-source/upstream.tar.gz /tmp/pristine.tar.gz
RUN mkdir -p /opt/pristine && tar -xzf /tmp/pristine.tar.gz -C /opt/pristine && rm /tmp/pristine.tar.gz \
 && test -f /opt/pristine/rosbag2/rosbag2_cpp/package.xml

# 6. Shell environment: underlay + overlay + RMW settings in every bash (BASH_ENV, bashrc, entrypoint).
#    No ROS_DOMAIN_ID default here; tests/test.sh assigns explicit domains per step.
RUN bash -c 'source /opt/ros/rolling/setup.bash && source /workspace/ws/install/setup.bash \
      && for v in AMENT_PREFIX_PATH CMAKE_PREFIX_PATH COLCON_PREFIX_PATH LD_LIBRARY_PATH PATH PYTHONPATH ROS_DISTRO ROS_VERSION ROS_PYTHON_VERSION; do \
           printf "export %s=%q\n" "$v" "${!v:-}"; done > /etc/ros_ws_env.sh' \
 && printf '%s\n' \
      'export RMW_IMPLEMENTATION=rmw_fastrtps_cpp' \
      'export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST' \
      'export ASAN_OPTIONS="${ASAN_OPTIONS:-detect_leaks=0}"' \
      'export WS=/workspace/ws' \
      >> /etc/ros_ws_env.sh \
 && echo 'source /etc/ros_ws_env.sh' >> /etc/bash.bashrc \
 && printf '#!/bin/bash\nset -e\nsource /etc/ros_ws_env.sh\nexec "$@"\n' > /ros_ws_entrypoint.sh \
 && chmod 0755 /ros_ws_entrypoint.sh
ENV BASH_ENV=/etc/ros_ws_env.sh
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST ASAN_OPTIONS=detect_leaks=0

# 7. Hidden harness: hidden_tests.patch (the PR's test-side diff), the storage-plugin differential
#    harness, the CLI playback check, expected test counts and the scorer.
COPY . /tests
RUN chmod 0755 /tests/test.sh

ENTRYPOINT ["/ros_ws_entrypoint.sh"]
CMD ["bash"]
