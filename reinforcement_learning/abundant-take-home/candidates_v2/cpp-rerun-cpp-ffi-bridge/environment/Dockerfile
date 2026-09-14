# syntax=docker/dockerfile:1
#
# Agent image for cpp-rerun-cpp-ffi-bridge.
#
# Layout (all fetched at image build; the container runs fully offline):
#   base        Ubuntu 24.04 (digest-pinned) + gcc 13 / cmake 3.28 / ninja 1.11 / python 3.12 from the noble
#               archive, pinned through Ubuntu's apt snapshot service.
#   rust-build  rustup 1.96.0 (the repo's rust-toolchain); builds `librerun_c.a` (the Rust static library the C++
#               SDK links), the `rerun` CLI (no viewer) and the Rust `snippets` binary in release mode.
#   agent       the excised repo tree at /workspace/repo, prebuilt rerun_c, a warm Debug CMake/Ninja build dir with
#               Arrow 18.0.0 (upstream's ExternalProject recipe), loguru and Catch2 populated, the `rerun` CLI on PATH.
#
# The verifier image (tests/Dockerfile) repeats `base`, `rust-build` and `agent` verbatim so BuildKit can share layers.

ARG APT_SNAPSHOT=20260912T000000Z

FROM ubuntu:24.04@sha256:224a1869083a311ef3f13648a154ba79832fbef6364d31493642ca03082da254 AS base
ARG APT_SNAPSHOT
ENV DEBIAN_FRONTEND=noninteractive
# Pin the archive state via snapshot.ubuntu.com; fall back to the live archive if the snapshot service is unreachable.
RUN set -eux; \
    sed -i 's/^Suites:/Snapshot: yes\nSuites:/' /etc/apt/sources.list.d/ubuntu.sources; \
    (apt-get -o Acquire::Snapshot=${APT_SNAPSHOT} update || apt-get update); \
    apt-get install -y --no-install-recommends \
        build-essential g++ cmake ninja-build pkg-config git curl ca-certificates \
        python3 libssl-dev rsync xz-utils unzip util-linux; \
    rm -rf /var/lib/apt/lists/*; \
    gcc --version | head -1; cmake --version | head -1; ninja --version; python3 --version

# ------------------------------------------------------------------------------------------------------------------
FROM base AS rust-build
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo PATH=/usr/local/cargo/bin:$PATH
RUN set -eux; \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/rustup-init.sh; \
    sh /tmp/rustup-init.sh -y --no-modify-path --profile minimal --default-toolchain 1.96.0; \
    rm /tmp/rustup-init.sh; rustup --version; cargo --version
WORKDIR /src
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /src && rm /tmp/upstream.tar.gz
# rust-toolchain pins channel 1.96.0 (+ rustfmt, clippy, wasm32 target); rustup installs them on first cargo use.
# CARGO_BUILD_JOBS=3 keeps peak rustc memory under the 8 GB container limit on 4 cpus.
ENV CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=3 CARGO_NET_RETRY=10 CARGO_PROFILE_RELEASE_DEBUG=0 \
    CARGO_PROFILE_RELEASE_LTO=off CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
RUN cargo fetch --locked
# rerun_c: the C ABI static library the C++ SDK links.  snippets: Rust reference implementations of every snippet
# (verifier only).  rerun-cli without default features: no viewer, provides `rerun rrd compare` / `rerun rrd print`.
RUN cargo build --release --locked -p rerun_c -p snippets -p rerun-cli --no-default-features \
    && ls -la target/release/librerun_c.a target/release/snippets target/release/rerun \
    && target/release/rerun --version

# ------------------------------------------------------------------------------------------------------------------
FROM base AS agent
WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz
# Prebuilt rerun_c where crates/top/rerun_c/CMakeLists.txt expects cargo's output for either build type.
COPY --from=rust-build /src/target/release/librerun_c.a /opt/rerun_c/release/librerun_c.a
COPY --from=rust-build /src/target/release/librerun_c.a /opt/rerun_c/debug/librerun_c.a
COPY --from=rust-build /src/target/release/rerun /usr/local/bin/rerun
ENV CARGO_TARGET_DIR=/opt/rerun_c
# `cpus = 4` in task.toml is a CPU quota, not a cpuset: nproc inside the container still reports every host core, so
# ninja would default to nproc+2 parallel g++ jobs on Arrow and the generated SDK sources and exhaust the 8 GB memory
# limit. CMAKE_BUILD_PARALLEL_LEVEL bounds every `cmake --build` (incl. Arrow's ExternalProject) here and at runtime.
ENV CMAKE_BUILD_PARALLEL_LEVEL=4
# The root CMakeLists wires `cargo build -p rerun_c` into the build graph; rerun_c is prebuilt, so provide a stub
# that succeeds for exactly that invocation and refuses anything else (there is no Rust toolchain in this image).
RUN printf '%s\n' '#!/bin/sh' \
    'case " $* " in' \
    '  *" -p rerun_c "*|*" --package rerun_c "*) echo "cargo stub: rerun_c is prebuilt at ${CARGO_TARGET_DIR:-/opt/rerun_c}" >&2; exit 0;;' \
    'esac' \
    'echo "cargo is not available in this environment: rerun_c is prebuilt and the Rust sources are read-only reference material." >&2' \
    'exit 1' > /usr/local/bin/cargo && chmod +x /usr/local/bin/cargo
# Configure and warm the Debug build (upstream's `pixi run -e cpp cpp-prepare` settings + a serialized link pool):
#  * Arrow 18.0.0 is downloaded (md5-pinned) and built by rerun_cpp/download_and_build_arrow.cmake;
#  * loguru and Catch2 v3.4.0 are populated by FetchContent;
#  * every rerun_sdk / snippets / rerun_sdk_tests object is compiled.  Linking `snippets` and `rerun_sdk_tests`
#    fails here by design: the FFI bridge implementation is absent from this tree.
RUN set -eux; cd /workspace/repo; \
    cmake -G Ninja -B build/debug -S . \
        -DCMAKE_BUILD_TYPE=Debug -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
        -DCMAKE_JOB_POOLS=link_pool=1 -DCMAKE_JOB_POOL_LINK=link_pool \
        -DFETCHCONTENT_UPDATES_DISCONNECTED=ON; \
    cmake --build build/debug --target rerun_sdk; \
    (cmake --build build/debug --target snippets rerun_sdk_tests -- -k 0 || true); \
    test -f build/debug/rerun_cpp/librerun_sdk.a; \
    test "$(find build/debug/docs/snippets -name '*.o' | wc -l)" -ge 121; \
    test "$(find build/debug/rerun_cpp/tests -name '*.o' | wc -l)" -ge 40; \
    test -f build/debug/rerun_cpp/arrow/lib/libarrow.a; \
    cmake -B build/debug -DFETCHCONTENT_FULLY_DISCONNECTED=ON > /dev/null; \
    touch /opt/rerun_c/debug/librerun_c.a /opt/rerun_c/release/librerun_c.a; \
    du -sh build/debug | tail -1
ENV PYTHONUNBUFFERED=1
