# rust:1.96.0-bookworm, pinned by manifest-list digest (resolved 2026-09-13 from Docker Hub;
# amd64 image digest sha256:c993d32d95cc146bd12c84d66f0b924a6a96f3988325f39c144f2f9893dea120).
# The toolchain matches the repository's `rust-toolchain` file (channel = "1.96.0").
FROM rust:1.96.0-bookworm@sha256:5e2214abe154fe26e39f64488952e5c991eeed1d6d6da7cc8381ae83927f0cfc

# System packages: C/C++ toolchain for -sys crates (zstd-sys, lz4, mimalloc), cmake/pkg-config
# for build scripts, python3 for helper scripts, rsync/git for the agent's convenience.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake pkg-config clang libssl-dev \
        python3 rsync git jq ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# `rust-toolchain` lists rustfmt, clippy and the wasm32 target; install them now so rustup never
# needs the network at runtime.
RUN rustup component add rustfmt clippy && rustup target add wasm32-unknown-unknown

ENV CARGO_INCREMENTAL=0 \
    CARGO_TERM_COLOR=never \
    RUST_BACKTRACE=1 \
    CARGO_BUILD_JOBS=4

WORKDIR /workspace/repo

# Pristine upstream tree (rerun-io/rerun @ c1ea47a3b80b3eb00f2b0e54c8887b589728bec6 with the
# three store-crate manifests and Cargo.lock pre-seeded with the dependency declarations the
# feature needs; large binary asset directories excluded). See provenance.json.
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz \
    && git init -q && git add -A \
    && git -c user.name=upstream -c user.email=upstream@localhost commit -q -m "upstream base" \
    && git config --global --add safe.directory /workspace/repo

# Fetch every locked crate for the host target so that all later cargo invocations work offline.
RUN cargo fetch --locked --target "$(rustc -vV | sed -n 's/^host: //p')"

# Warm the build cache with exactly the package selections the agent and the verifier use.
# (Compiles ~370 dependency crates at the workspace's dev opt-level; measured 33 CPU-minutes.)
RUN cargo test --locked --offline -p re_chunk_optimizer -p re_log_encoding -p re_chunk_store --no-run \
    && cargo check --locked --offline -p re_chunk_optimizer -p re_log_encoding -p re_chunk_store --tests

# Network is unavailable after the build; make cargo fail fast instead of hanging.
ENV CARGO_NET_OFFLINE=true
