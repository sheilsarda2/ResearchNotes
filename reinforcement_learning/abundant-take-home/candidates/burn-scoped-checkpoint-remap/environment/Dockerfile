FROM rust:1.98.1-bookworm@sha256:9a73a5088750b4c95158ab26629c854c3d6fc4b173cb7bc8079ad252d8ed7bfa
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates git && rm -rf /var/lib/apt/lists/*
ENV CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR=/opt/cargo-target CARGO_INCREMENTAL=0
COPY cargo-config.toml /usr/local/cargo/config.toml
WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz
COPY smoke /opt/burn-checks
RUN cargo build --locked --manifest-path /opt/burn-checks/Cargo.toml
ENV CARGO_NET_OFFLINE=true
