# Agent environment for rs-zenoh-timestamp-instrumentation
# Upstream: eclipse-zenoh/zenoh @ a62451c0231faa3c7ecbd44499613c27706b117a (base of PR #2620)
# Toolchain: rust-toolchain.toml pins 1.97.1; the image ships exactly that toolchain.
FROM rust:1.97.1-bookworm@sha256:0e2bcaef56d041a486784e54104a81aebe0da44bd03019bd70bc0401e42e4a97
# Bound cargo parallelism: cargo defaults to the host CPU count (18 here), and parallel rustc+ld
# for the test binaries exceeded the Docker VM memory (ld killed by signal 9). 4 jobs is safe on 8 GB.
ENV CARGO_BUILD_JOBS=4

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        pkg-config cmake python3 jq rsync less \
    && rm -rf /var/lib/apt/lists/*

# Deterministic, compact debug artifacts; identical settings in the verifier image so
# the warm target directory built here is reused there.
ENV CARGO_INCREMENTAL=0 \
    CARGO_PROFILE_DEV_DEBUG=line-tables-only \
    CARGO_TERM_COLOR=never \
    RUST_BACKTRACE=1

WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz \
    && git init -q \
    && git config user.email "task@example.invalid" && git config user.name "task" \
    && git add -A && git commit -q -m "upstream eclipse-zenoh/zenoh a62451c0 (task base)"

# Every crate in Cargo.lock is fetched now; no network is needed afterwards.
RUN cargo fetch --locked

# Warm the build cache with the exact invocations the project (and the verifier) use,
# so the first incremental build takes minutes rather than a full cold build.
RUN cargo test --offline --no-run -p zenoh -p zenoh-codec \
        --features zenoh/test,zenoh/unstable,zenoh/internal \
        --lib --test codec --test session --test routing --test queryable --test attachments \
        --test source_info --test qos --test unicity --test matching --test adminspace \
    && cargo test --offline --no-run -p zenoh-ext --features unstable,internal --test advanced \
    && rm -rf target/debug/incremental \
    && du -sh target /usr/local/cargo/registry

ENV CARGO_NET_OFFLINE=true
