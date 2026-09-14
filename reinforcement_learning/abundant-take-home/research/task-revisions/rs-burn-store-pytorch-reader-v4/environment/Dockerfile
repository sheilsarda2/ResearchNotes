# Agent environment for rs-burn-store-pytorch-reader.
# Rust 1.95.0 is the MSRV burn's CI validates for this commit (crates/burn/Cargo.toml
# rust-version, .github/workflows/test.yml RUST_PREVIOUS_VERSION). Multi-arch index digest.
FROM rust:1.95.0-slim-bookworm@sha256:d7482085ff5b415f84dba5647ae71606650bdef00db7aeb69f4b3d170c3e4082
# Bound cargo parallelism: cargo defaults to the host CPU count (18 here), and parallel rustc+ld
# for the test binaries exceeded the Docker VM memory (ld killed by signal 9). 4 jobs is safe on 8 GB.
ENV CARGO_BUILD_JOBS=4

ENV DEBIAN_FRONTEND=noninteractive
# aarch64 Linux does not enable fp16 by default and the gemm-f16 crate's inline asm requires it
# (amd64 is unaffected). Per-target rustflags in CARGO_HOME keep amd64 builds byte-identical.
RUN mkdir -p "${CARGO_HOME:-/usr/local/cargo}" && printf '[target.aarch64-unknown-linux-gnu]\nrustflags = ["-C", "target-feature=+fp16"]\n' >> "${CARGO_HOME:-/usr/local/cargo}/config.toml"
# gcc/libc6-dev come with the rust image; the rest covers the C builds in the dependency
# graph (zstd-sys, liblzma-sys, bzip2) and gives the agent a torch-free python3 for
# hand-assembling test checkpoints with the standard library (pickle, zipfile, tarfile).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git pkg-config cmake make g++ python3 \
    && rm -rf /var/lib/apt/lists/*

ENV CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    CARGO_TERM_COLOR=never \
    CARGO_INCREMENTAL=1 \
    RUST_BACKTRACE=1

WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz

# Network is available only here. Download and compile every dependency of the graded
# targets so that `cargo test --offline` works for them afterwards. Only these packages are
# guaranteed to build offline; the rest of the workspace (GPU backends, tch, wgpu) is not
# fetched.
RUN cargo --version && rustc --version \
    && cargo test --locked --no-run -p burn-store -p burn-pack -p pytorch-tests -p safetensors-tests \
    && du -sh /workspace/repo/target /usr/local/cargo/registry

# Everything after this point runs without network.
ENV CARGO_NET_OFFLINE=true
