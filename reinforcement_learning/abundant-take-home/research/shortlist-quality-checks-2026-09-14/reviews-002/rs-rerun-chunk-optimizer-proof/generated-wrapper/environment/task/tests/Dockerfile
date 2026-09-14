# Verifier image: same pinned base and the same pristine tree + warm build cache as the agent
# image, plus a second pristine copy (/opt/pristine) used for the clean-room overlay and the
# hidden tests under /tests. Runs with network_mode = "no-network".
FROM rust:1.96.0-bookworm@sha256:5e2214abe154fe26e39f64488952e5c991eeed1d6d6da7cc8381ae83927f0cfc

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake pkg-config clang libssl-dev \
        python3 rsync git jq ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN rustup component add rustfmt clippy && rustup target add wasm32-unknown-unknown

ENV CARGO_INCREMENTAL=0 \
    CARGO_TERM_COLOR=never \
    RUST_BACKTRACE=1 \
    CARGO_BUILD_JOBS=4

WORKDIR /workspace/repo

COPY image-source/upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo \
    && mkdir -p /opt/pristine && tar -xzf /tmp/upstream.tar.gz -C /opt/pristine \
    && rm /tmp/upstream.tar.gz

RUN cargo fetch --locked --target "$(rustc -vV | sed -n 's/^host: //p')"

# Warm cache with the exact selection test.sh builds
# (`-p re_chunk_optimizer -p re_log_encoding -p re_chunk_store`).
RUN cargo test --locked --offline -p re_chunk_optimizer -p re_log_encoding -p re_chunk_store --no-run

ENV CARGO_NET_OFFLINE=true

# Hidden harness: test.sh, the PR's test files and the authored contract tests.
COPY . /tests
RUN chmod +x /tests/test.sh
