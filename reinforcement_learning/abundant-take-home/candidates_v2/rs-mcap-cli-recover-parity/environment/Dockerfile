# Agent environment for rs-mcap-cli-recover-parity.
# Rust stable at the time of the upstream PR (CI used `stable`; 1.95.0 was current on 2026-06-01).
# All crates are fetched and pre-built at image build; the container runs offline.
FROM rust:1.95.0-bookworm@sha256:6258907abe69656e41cd992e0b705cdcfabcbbe3db374f92ed2d47121282d4a1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git python3 pkg-config cmake xxd less jq \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz \
 && git init -q && git config user.email task@example.invalid && git config user.name task \
 && git add -A && git commit -q -m "base: foxglove/mcap cb59283" && git tag base

# Warm the dependency cache and the debug target directory so the agent's first build is
# incremental. The workspace lives at rust/ (members: mcap, cli).
WORKDIR /workspace/repo/rust
RUN cargo fetch --locked \
 && cargo build --locked -p mcap-cli \
 && cargo test --locked --no-run -p mcap-cli \
 && cargo test --locked --no-run -p mcap --all-features \
 && cargo build --locked --offline -p mcap-cli \
 && ./target/debug/mcap --version

# Network is unavailable at runtime; make cargo fail fast instead of hanging on the registry.
ENV CARGO_NET_OFFLINE=true \
    CARGO_TERM_COLOR=never \
    RUST_BACKTRACE=1

WORKDIR /workspace/repo
