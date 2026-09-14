FROM rust:1.97.1-slim-bookworm@sha256:2775a09d208ff0d7c1f50490c45b62db929e87ba1dcbc3f2132ac71a704bcdd3
RUN apt-get update && apt-get install -y --no-install-recommends bash git curl ca-certificates python3 build-essential pkg-config && rm -rf /var/lib/apt/lists/*
ENV CARGO_BUILD_JOBS=2 CARGO_PROFILE_DEV_DEBUG=0 CARGO_INCREMENTAL=0 RUSTUP_TOOLCHAIN=1.97.1
WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz
COPY vendor.tar.gz /tmp/vendor.tar.gz
RUN mkdir -p /opt/vendor && tar -xzf /tmp/vendor.tar.gz -C /opt/vendor && rm /tmp/vendor.tar.gz
COPY cargo-config.toml /usr/local/cargo/config.toml
COPY check /opt/check
RUN cargo test --manifest-path /opt/check/Cargo.toml --locked --offline --test upstream_queryable --no-run
