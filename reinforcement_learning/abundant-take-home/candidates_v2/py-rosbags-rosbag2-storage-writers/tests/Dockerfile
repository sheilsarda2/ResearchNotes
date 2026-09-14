# Verifier image for py-rosbags-rosbag2-storage-writers (separate, offline at run time).
# Contains: the excised upstream tree (same as the agent image), the pristine upstream
# test-suite, mcap-python, the Go/Rust `mcap` CLI (independent MCAP implementation,
# used for `mcap doctor`), the rosbag2-recorded fixture bags and the hidden tests.
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ROSBAGS=0.11.5

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git ca-certificates curl sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/repo
COPY image-source/upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz

COPY image-source/requirements.txt /tmp/requirements.txt
RUN python -m pip install "setuptools==84.0.0" "setuptools-scm==10.2.3" "wheel==0.48.0" \
    && python -m pip install -r /tmp/requirements.txt \
    && python -m pip install --no-deps --no-build-isolation -e . \
    && python -c "import rosbags, mcap; from importlib.metadata import version; assert version('rosbags') == '0.11.5'"

# Pristine copy of the upstream tests (base commit 47be3eed), restored over the
# submission by test.sh so the agent cannot alter what the verifier executes.
COPY image-source/pristine-tests.tar.gz /tmp/pristine-tests.tar.gz
RUN mkdir -p /pristine && tar -xzf /tmp/pristine-tests.tar.gz -C /pristine && rm /tmp/pristine-tests.tar.gz \
    && test -f /pristine/tests/rosbags/rosbag2/test_writer.py

# Independent MCAP implementation: foxglove `mcap` CLI v0.3.0 (releases/mcap-cli/v0.3.0),
# pinned by sha256 per architecture.
RUN set -eu; arch="$(uname -m)"; \
    case "$arch" in \
      x86_64)  asset=mcap-linux-amd64; sum=5d4100573fab880f1c6400952466275bb3b32c3d230a54f7c380ee0d08e59eef ;; \
      aarch64) asset=mcap-linux-arm64; sum=be9734ef63ada9d0cc7a3aa41378ab65fd482601e5f5b3b52098d8e6553deabf ;; \
      *) echo "unsupported arch $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/mcap "https://github.com/foxglove/mcap/releases/download/releases/mcap-cli/v0.3.0/${asset}"; \
    echo "${sum}  /usr/local/bin/mcap" | sha256sum -c -; \
    chmod +x /usr/local/bin/mcap; \
    /usr/local/bin/mcap --help >/dev/null

COPY fixtures /tests/fixtures
RUN cd /tests/fixtures && sha256sum -c SHA256SUMS >/dev/null

COPY hidden /tests/hidden
COPY test.sh /tests/test.sh
RUN chmod +x /tests/test.sh
