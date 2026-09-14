# Agent environment: foxglove/foxglove-sdk @ b2fb3fab2954d34fba6408fff1b4b7a7d58e6bab (2026-05-22).
#
# Ubuntu 22.04 mirrors the upstream local dev image (Dockerfile at the repo root) and the
# remote-access CI runner (ubuntu-22.04, gcc 11, cmake 3.22). Every network-dependent step runs
# here at image build; at runtime the container is offline:
#   - Rust 1.95.0 (stable at the base commit; see the toolchain step for why not the 1.85 MSRV) with
#     the whole workspace fetched into the cargo registry
#   - every CMake FetchContent dependency pre-cloned at its pinned tag under /opt/cmake-deps
#   - the LiveKit prebuilt libwebrtc bundle that webrtc-sys would otherwise download at build
#   - a warm RelWithDebInfo build of the C library (remote access ON) and the C++ SDK + tests
#   - a warm `cargo test -p foxglove_c --no-run` so the C crate's unit tests rebuild incrementally
#
# The verifier image (../tests/Dockerfile) repeats this file's instructions verbatim up to the
# marker below so Docker can share layers between the two images.
FROM ubuntu:22.04@sha256:829f6df217bcbae2b371026e81711d1a787c61b2967ad09d015063663ebafbf7
# Bound cargo parallelism: cargo defaults to the host CPU count (18 here), and parallel rustc+ld
# for the test binaries exceeded the Docker VM memory (ld killed by signal 9). 4 jobs is safe on 8 GB.
ENV CARGO_BUILD_JOBS=4

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake make git ca-certificates curl unzip pkg-config rsync \
      libglib2.0-dev libva-dev libwebsockets-dev \
      python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Rust toolchain: 1.95.0, the stable release current at the base commit (2026-05-22; 1.95.0 shipped
# 2026-04-16, 1.96.0 on 2026-05-28). The workspace declares rust-version = "1.85.0" (no
# rust-toolchain.toml at this commit), but that MSRV is only verified upstream by
# `cargo +1.85.0 build -p foxglove` with default features; the remote-access build this task needs
# (`make -C cpp build` defaults to FOXGLOVE_REMOTE_ACCESS=ON) does not compile on 1.85 on any
# architecture: rust/foxglove/src/remote_access/{connection.rs,session.rs} use let chains (stable
# since 1.88), and on aarch64 the locked zune-jpeg 0.5.9 NEON module needs 1.87 (probed: 1.85 and
# 1.86 fail, 1.87 passes). Upstream's remote-access CI jobs (ubuntu-22.04, ubuntu-22.04-arm, macOS,
# Windows) all build with `stable`, so the stable of the day is the faithful pin. rustfmt/clippy are
# what AGENTS.md asks contributors to run.
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo PATH=/usr/local/cargo/bin:$PATH
ARG RUST_VERSION=1.95.0
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain ${RUST_VERSION} --no-modify-path \
    && rustup component add rustfmt clippy \
    && rustc --version && cargo --version

# CMake dependencies at the tags pinned in cpp/CMakeLists.txt (FetchContent). CMAKE_ARGS below
# points FetchContent at these clones so configure never touches the network.
RUN mkdir -p /opt/cmake-deps && cd /opt/cmake-deps \
    && git clone -q --depth 1 --branch v3.8.0 https://github.com/catchorg/Catch2.git Catch2 \
    && git clone -q --depth 1 --branch v0.5.1 https://github.com/corrosion-rs/corrosion.git corrosion \
    && git clone -q --depth 1 --branch v4.3.3 https://github.com/warmcat/libwebsockets.git libwebsockets \
    && git clone -q --depth 1 --branch v3.12.0 https://github.com/nlohmann/json.git json \
    && git clone -q https://github.com/tobiaslocker/base64.git base64 \
    && git -C base64 checkout -q 8d96a2a737ac1396304b1de289beb3a5ea0cb752 \
    && git clone -q --depth 1 --branch v3.0.4 https://github.com/HowardHinnant/date.git date \
    && git clone -q --depth 1 --branch v0.32.0 https://github.com/yhirose/cpp-httplib.git cpp-httplib \
    && git clone -q --depth 1 --branch releases/cpp/v2.1.2 https://github.com/foxglove/mcap.git mcap \
    && git clone -q --depth 1 --branch v1.10.0 https://github.com/lz4/lz4.git lz4 \
    && find /opt/cmake-deps -name .git -type d -prune -exec rm -rf {} +
ENV CMAKE_ARGS="-DFETCHCONTENT_FULLY_DISCONNECTED=ON -DFETCHCONTENT_SOURCE_DIR_CATCH2=/opt/cmake-deps/Catch2 -DFETCHCONTENT_SOURCE_DIR_CORROSION=/opt/cmake-deps/corrosion -DFETCHCONTENT_SOURCE_DIR_LIBWEBSOCKETS=/opt/cmake-deps/libwebsockets -DFETCHCONTENT_SOURCE_DIR_NLOHMANN_JSON=/opt/cmake-deps/json -DFETCHCONTENT_SOURCE_DIR_BASE64=/opt/cmake-deps/base64 -DFETCHCONTENT_SOURCE_DIR_DATE=/opt/cmake-deps/date -DFETCHCONTENT_SOURCE_DIR_HTTPLIB=/opt/cmake-deps/cpp-httplib -DFETCHCONTENT_SOURCE_DIR_MCAP=/opt/cmake-deps/mcap -DFETCHCONTENT_SOURCE_DIR_LZ4=/opt/cmake-deps/lz4"

# Prebuilt libwebrtc for the remote-access feature. webrtc-sys 0.3.32 (via webrtc-sys-build
# 0.3.18, tag webrtc-51ef663) downloads this at build time unless LK_CUSTOM_WEBRTC points at an
# extracted bundle; pre-extract it so `cargo clean` + rebuild still works offline.
ARG WEBRTC_TAG=webrtc-51ef663
RUN set -eux; arch="$(uname -m)"; \
    case "$arch" in x86_64) triple=linux-x64-release ;; aarch64) triple=linux-arm64-release ;; *) echo "unsupported arch $arch" >&2; exit 1 ;; esac; \
    mkdir -p /opt/livekit-webrtc && cd /opt/livekit-webrtc \
    && curl -fsSL -o webrtc.zip "https://github.com/livekit/rust-sdks/releases/download/${WEBRTC_TAG}/webrtc-${triple}.zip" \
    && unzip -q webrtc.zip && rm webrtc.zip && ln -s "${triple}" current && test -f current/webrtc.ninja
ENV LK_CUSTOM_WEBRTC=/opt/livekit-webrtc/current

# Source tree (clean `git archive` of the base commit; no .git, no solution, no hidden tests).
WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo \
    && git init -q && git config user.email task@example.invalid && git config user.name task \
    && git add -A && git commit -qm "foxglove-sdk base b2fb3fab2954d34fba6408fff1b4b7a7d58e6bab"

# Fetch every workspace dependency for every platform, then go offline for good. All platforms,
# not just the host: c/build.rs runs cbindgen, whose `cargo metadata --all-features` (no
# --filter-platform, the crate sets no [parse] only_target_dependencies) must read the manifest
# of every package in Cargo.lock, so a host-only fetch leaves e.g. addr2line/windows-sys missing
# and the offline build fails with "failed to download". ~590 crates, ~1 GB registry.
RUN cargo fetch --locked
ENV CARGO_NET_OFFLINE=true

# Warm build: C library via corrosion (remote access ON, release profile), C++ SDK, tests and
# examples. This is exactly `make -C cpp build` as contributors run it. The base test suite is
# executed once for a sanity log but does not fail the image build.
RUN mkdir -p /opt/build-logs \
    && make -C cpp build PARALLEL_JOBS=4 > /opt/build-logs/warm-build.log 2>&1 \
    || { tail -n 200 /opt/build-logs/warm-build.log; exit 1; }
RUN (cd cpp/build && timeout 900 ctest --output-on-failure > /opt/build-logs/ctest-base.log 2>&1) \
    || echo "WARNING: base ctest reported failures; see /opt/build-logs/ctest-base.log"

# Warm the dev-profile test build of the C crate (cargo test -p foxglove_c is part of AGENTS.md).
RUN cargo test -p foxglove_c --no-run --offline > /opt/build-logs/warm-cargo-test.log 2>&1 \
    || { tail -n 100 /opt/build-logs/warm-cargo-test.log; exit 1; }

# ---- shared prefix ends here (keep everything above identical to ../tests/Dockerfile) ----

RUN rm -f /tmp/upstream.tar.gz
ENV PYTHONUNBUFFERED=1
WORKDIR /workspace/repo
