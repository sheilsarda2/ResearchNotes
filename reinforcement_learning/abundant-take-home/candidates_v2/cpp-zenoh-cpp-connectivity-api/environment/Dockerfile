# Agent image for cpp-zenoh-cpp-connectivity-api.
#
# Base: rust:1.93.0-slim-bookworm. Rust 1.93.0 is exactly what zenoh-c's rust-toolchain.toml pins,
# so no toolchain download happens at build; Debian 12 supplies cmake 3.25 and gcc 12 (C++17).
# Everything the task needs is fetched here; the container is expected to run offline.
FROM rust:1.93.0-slim-bookworm@sha256:776861219cd851131c1cec3bbd7cbeb16b99a794048097eb69ad9682a8ed0d57

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git pkg-config ca-certificates curl unzip python3 gdb less procps \
    && rm -rf /var/lib/apt/lists/*

ENV CARGO_BUILD_JOBS=4

# Task tree: zenoh-cpp @ 0dd5f5a98faffdd53ab0816eb892b521c3965f41 with the submodule sources
# zenoh-c @ 8fd05be3584673db10d5c8cfd0510bf550bbe53c and zenoh-pico @ b8f785621a0d0bf5e3ab461843d3303045fab9bf
# vendored as plain directories (no .git inside). A single baseline commit lets the agent use git diff.
WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz \
    && git init -q -b main . && echo 'build/' >> .git/info/exclude \
    && git -c user.name=task -c user.email=task@invalid add -A \
    && git -c user.name=task -c user.email=task@invalid commit -q -m "zenoh-cpp 0dd5f5a9 + zenoh-c 8fd05be3 + zenoh-pico b8f78562 (task baseline)"

# zenoh-c: Rust core + C API with the unstable API and shared memory enabled, Debug configuration
# (the recipe in upstream CLAUDE.md; the release profile uses fat LTO and is several times slower).
# Installs headers, libzenohc and the CMake package to /opt/zenoh. The cargo registry and the
# zenoh git checkout stay in CARGO_HOME so `cargo build --offline` remains possible.
RUN cmake -S /workspace/repo/zenoh-c -B /tmp/build-zenoh-c -DCMAKE_BUILD_TYPE=Debug \
        -DZENOHC_BUILD_WITH_UNSTABLE_API=TRUE -DZENOHC_BUILD_WITH_SHARED_MEMORY=TRUE \
        -DCMAKE_INSTALL_PREFIX=/opt/zenoh \
    && cmake --build /tmp/build-zenoh-c --target install --config Debug -j4 \
    && rm -rf /tmp/build-zenoh-c

# zenoh-pico: the C implementation, with the feature set scripts/install_from_git.sh uses for
# unstable builds (Z_FEATURE_CONNECTIVITY defaults to 1 at this revision).
RUN cmake -S /workspace/repo/zenoh-pico -B /tmp/build-zenoh-pico -DCMAKE_BUILD_TYPE=Release \
        -DZ_FEATURE_UNSTABLE_API=1 -DZ_FEATURE_BATCHING=1 -DZ_FEATURE_ADVANCED_SUBSCRIPTION=1 \
        -DZ_FEATURE_ADVANCED_PUBLICATION=1 -DZ_FEATURE_MATCHING=1 -DZ_FEATURE_LOCAL_SUBSCRIBER=1 \
        -DZ_FEATURE_LOCAL_QUERYABLE=1 -DCMAKE_INSTALL_PREFIX=/opt/zenoh \
    && cmake --build /tmp/build-zenoh-pico --target install -j4 \
    && rm -rf /tmp/build-zenoh-pico

# zenohd 1.8.0 (release binary for the glibc target, sha256-pinned per architecture) so router-backed
# test runs can be reproduced offline exactly the way the verifier runs them. The "musl standalone"
# assets are not static: they need /lib/ld-musl-*.so.1 and a musl libgcc_s, absent on Debian.
COPY zenohd.sha256 /tmp/zenohd.sha256
RUN set -eux; cd /tmp; arch="$(uname -m)"; \
    case "$arch" in x86_64) t=x86_64;; aarch64|arm64) t=aarch64;; *) echo "unsupported arch: $arch" >&2; exit 1;; esac; \
    f="zenoh-1.8.0-${t}-unknown-linux-gnu-standalone.zip"; \
    curl -fsSL -o "$f" "https://github.com/eclipse-zenoh/zenoh/releases/download/1.8.0/$f"; \
    grep " ${f}\$" zenohd.sha256 | sha256sum -c -; \
    unzip -q "$f" zenohd; install -D -m 0755 zenohd /opt/zenoh/bin/zenohd; rm -f "$f" zenohd zenohd.sha256

# zenoh-cpp: configure once for both backends and build the upstream test binaries, so the
# agent's first build only recompiles what its header edits touch.
RUN cmake -S /workspace/repo -B /workspace/repo/build -DCMAKE_BUILD_TYPE=Debug \
        -DZENOHCXX_ZENOHC=ON -DZENOHCXX_ZENOHPICO=ON -DZENOHCXX_EXAMPLES_PROTOBUF=OFF \
        -DCMAKE_PREFIX_PATH=/opt/zenoh -DCMAKE_INSTALL_PREFIX=/opt/zenoh \
    && cmake --build /workspace/repo/build --target tests -j4

ENV LD_LIBRARY_PATH=/opt/zenoh/lib:/opt/zenoh/lib64 \
    PATH=/opt/zenoh/bin:/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    CARGO_NET_OFFLINE=true \
    ZENOH_PREFIX=/opt/zenoh
WORKDIR /workspace/repo
