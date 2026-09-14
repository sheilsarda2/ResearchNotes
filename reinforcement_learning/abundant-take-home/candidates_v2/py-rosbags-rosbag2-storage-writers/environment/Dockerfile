# Agent environment for py-rosbags-rosbag2-storage-writers.
# Base: python:3.12.11-slim-bookworm (multi-arch index digest, validated in v1 tasks).
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ROSBAGS=0.11.5

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git ca-certificates sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz

# All dependencies are fetched at build time; the agent runs without network.
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install "setuptools==84.0.0" "setuptools-scm==10.2.3" "wheel==0.48.0" \
    && python -m pip install -r /tmp/requirements.txt \
    && python -m pip install --no-deps --no-build-isolation -e . \
    && python -c "import rosbags, mcap, apsw, zstandard, lz4, numpy; from importlib.metadata import version; assert version('rosbags') == '0.11.5'"

# Example bags recorded by the ROS 2 rosbag2 C++ recorder (ros2/rosbag2 test resources,
# rosbag2_tests/resources/{mcap,sqlite3}/cdr_test, Apache-2.0). Reference data only.
COPY fixtures /workspace/fixtures

RUN git init -q && git config user.email task@example.invalid && git config user.name Task \
    && git add . && git commit -qm "rosbags 0.11.5 (47be3eed) with rosbag2 storage writers excised"
