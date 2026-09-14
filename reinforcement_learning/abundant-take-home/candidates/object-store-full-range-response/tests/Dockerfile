FROM rust:1.98.1-bookworm@sha256:9a73a5088750b4c95158ab26629c854c3d6fc4b173cb7bc8079ad252d8ed7bfa
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV RUSTUP_TOOLCHAIN=1.98.1 CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR=/opt/object-store-target
RUN rustup component add rustfmt --toolchain 1.98.1
WORKDIR /workspace/repo
COPY image-source/upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz
COPY image-source/Cargo.lock /workspace/repo/Cargo.lock
RUN cargo test --locked --features http --lib --test get_range_file --no-run
ENV CARGO_NET_OFFLINE=true
RUN git init -q && git config user.email task@example.invalid && git config user.name Task \
    && git add . && git add -f Cargo.lock && git commit -qm "Pinned object_store baseline"

COPY . /tests
