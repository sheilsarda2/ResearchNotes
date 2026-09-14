# Verifier image for cpp-zenoh-cpp-connectivity-api (separate container, no network at run time).
#
# Same base and the same zenoh-c / zenoh-pico builds as the agent image, plus: zenohd 1.8.0 (router),
# zenoh-python 1.8.0 under a pinned CPython (the cross-language oracle), the hidden tests, and a
# pristine, pre-configured copy of the task tree at /work/repo. Harbor re-materialises the agent's
# /workspace/repo/include at the same path in this container; test.sh overlays it onto /work/repo.
FROM rust:1.93.0-slim-bookworm@sha256:776861219cd851131c1cec3bbd7cbeb16b99a794048097eb69ad9682a8ed0d57

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git pkg-config ca-certificates curl unzip python3 gdb less procps \
    && rm -rf /var/lib/apt/lists/*

ENV CARGO_BUILD_JOBS=4

COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN mkdir -p /work/repo && tar -xzf /tmp/upstream.tar.gz -C /work/repo && rm /tmp/upstream.tar.gz

RUN cmake -S /work/repo/zenoh-c -B /tmp/build-zenoh-c -DCMAKE_BUILD_TYPE=Debug \
        -DZENOHC_BUILD_WITH_UNSTABLE_API=TRUE -DZENOHC_BUILD_WITH_SHARED_MEMORY=TRUE \
        -DCMAKE_INSTALL_PREFIX=/opt/zenoh \
    && cmake --build /tmp/build-zenoh-c --target install --config Debug -j4 \
    && rm -rf /tmp/build-zenoh-c

RUN cmake -S /work/repo/zenoh-pico -B /tmp/build-zenoh-pico -DCMAKE_BUILD_TYPE=Release \
        -DZ_FEATURE_UNSTABLE_API=1 -DZ_FEATURE_BATCHING=1 -DZ_FEATURE_ADVANCED_SUBSCRIPTION=1 \
        -DZ_FEATURE_ADVANCED_PUBLICATION=1 -DZ_FEATURE_MATCHING=1 -DZ_FEATURE_LOCAL_SUBSCRIBER=1 \
        -DZ_FEATURE_LOCAL_QUERYABLE=1 -DCMAKE_INSTALL_PREFIX=/opt/zenoh \
    && cmake --build /tmp/build-zenoh-pico --target install -j4 \
    && rm -rf /tmp/build-zenoh-pico

# zenohd 1.8.0, release binary for the glibc target, sha256-pinned per architecture (the "musl standalone"
# assets are dynamically linked against musl and cannot run on Debian).
COPY hidden/zenohd.sha256 /tmp/zenohd.sha256
RUN set -eux; cd /tmp; arch="$(uname -m)"; \
    case "$arch" in x86_64) t=x86_64;; aarch64|arm64) t=aarch64;; *) echo "unsupported arch: $arch" >&2; exit 1;; esac; \
    f="zenoh-1.8.0-${t}-unknown-linux-gnu-standalone.zip"; \
    curl -fsSL -o "$f" "https://github.com/eclipse-zenoh/zenoh/releases/download/1.8.0/$f"; \
    grep " ${f}\$" zenohd.sha256 | sha256sum -c -; \
    unzip -q "$f" zenohd; install -D -m 0755 zenohd /opt/zenoh/bin/zenohd; rm -f "$f" zenohd zenohd.sha256

# zenoh-python 1.8.0 (hash-pinned wheels). The release ships only a cp38-cp38 wheel for x86_64 Linux
# and a cp39-abi3 wheel for aarch64, so uv installs a pinned CPython 3.8.20 on x86_64 and 3.11 on
# aarch64; the parity script is written to 3.8 syntax.
COPY --from=ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 /uv /uvx /usr/local/bin/
COPY hidden/requirements-zenoh-python.txt /tmp/requirements-zenoh-python.txt
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python UV_LINK_MODE=copy UV_NO_PROGRESS=1
RUN set -eux; arch="$(uname -m)"; \
    case "$arch" in x86_64) py=3.8.20;; aarch64|arm64) py=3.11;; *) exit 1;; esac; \
    uv python install "$py"; uv venv --python "$py" /opt/venv; \
    uv pip install --python /opt/venv/bin/python --require-hashes --only-binary :all: -r /tmp/requirements-zenoh-python.txt; \
    /opt/venv/bin/python -c 'import sys, zenoh; zenoh.Config(); print("zenoh-python import OK on", sys.version.split()[0])'; \
    rm -f /tmp/requirements-zenoh-python.txt

# Hidden tests, then the pristine tree prepared and pre-configured. The registered ctest list must
# equal hidden/expected_tests.txt exactly (fails the image build otherwise). Building the `tests`
# target against the pristine headers is expected to fail for the three connectivity-dependent
# targets; the remaining objects warm the build directory.
COPY hidden /tests/hidden
RUN bash /tests/hidden/prepare_tests.sh /work/repo \
    && cmake -S /work/repo -B /work/repo/build -DCMAKE_BUILD_TYPE=Debug \
        -DZENOHCXX_ZENOHC=ON -DZENOHCXX_ZENOHPICO=ON -DZENOHCXX_EXAMPLES_PROTOBUF=OFF \
        -DCMAKE_PREFIX_PATH=/opt/zenoh -DCMAKE_INSTALL_PREFIX=/opt/zenoh \
    && (cd /work/repo/build && ctest -N | sed -n 's/^ *Test *#[0-9]*: *//p' | LC_ALL=C sort > /tmp/registered_tests.txt) \
    && LC_ALL=C sort /tests/hidden/expected_tests.txt | diff -u - /tmp/registered_tests.txt \
    && (cmake --build /work/repo/build --target tests -j4 -- -k || echo "pristine build: connectivity-dependent targets are expected to fail") \
    && mkdir -p /opt/pristine && cp -a /work/repo/include /opt/pristine/include && cp -a /work/repo/tests /opt/pristine/tests \
    && python3 /tests/hidden/anti_cheat.py baseline /work/repo/include > /opt/pristine/token_baseline.json \
    && rm -f /tmp/registered_tests.txt

# Landing zone for the collected artifact and the log directory Harbor expects.
RUN mkdir -p /workspace/repo /logs/verifier

COPY . /tests
RUN chmod +x /tests/test.sh /tests/hidden/*.sh

ENV LD_LIBRARY_PATH=/opt/zenoh/lib:/opt/zenoh/lib64 \
    PATH=/opt/zenoh/bin:/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    CARGO_NET_OFFLINE=true \
    ZENOH_TEST_ROUTER=tcp/127.0.0.1:27447
WORKDIR /work/repo
