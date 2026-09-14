FROM rust:1.98.1-bookworm@sha256:9a73a5088750b4c95158ab26629c854c3d6fc4b173cb7bc8079ad252d8ed7bfa AS rust
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7
RUN apt-get update && apt-get install -y --no-install-recommends bash git curl ca-certificates build-essential pkg-config libssl-dev lld && rm -rf /var/lib/apt/lists/*
# lld avoids the high peak memory of GNU ld when linking the Arrow/RRD graph.
RUN printf '#!/bin/sh\nexec /usr/bin/cc -fuse-ld=lld "$@"\n' > /usr/local/bin/cc && chmod +x /usr/local/bin/cc
COPY --from=rust /usr/local/rustup /usr/local/rustup
COPY --from=rust /usr/local/cargo /usr/local/cargo
ENV PATH=/usr/local/cargo/bin:$PATH RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo RUSTUP_TOOLCHAIN=1.98.1 CARGO_TARGET_DIR=/opt/rerun-target CARGO_BUILD_JOBS=2 CARGO_PROFILE_DEV_OPT_LEVEL=0 CARGO_PROFILE_DEV_DEBUG=0 PYTHONUNBUFFERED=1
RUN rustup component add rustfmt clippy --toolchain 1.98.1
WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz
RUN cargo test --locked -p re_entity_db --lib --test clear --no-run --config 'profile.dev.package."*".opt-level=0'
