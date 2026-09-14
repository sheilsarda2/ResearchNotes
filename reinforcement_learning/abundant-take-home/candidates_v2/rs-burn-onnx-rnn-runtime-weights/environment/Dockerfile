# Agent environment for rs-burn-onnx-rnn-runtime-weights.
#
# rust:1.98.1-slim-bookworm, pinned by manifest-list digest (Docker Hub, 2026-09-13). The
# repository ships no rust-toolchain.toml and its CI runs `stable`; 1.98.1 is stable at the
# base commit's date (edition 2024, lockfile v4). Network is available only while this image
# builds: every crates.io and git dependency is fetched and pre-built here, and the runtime
# is forced offline with CARGO_NET_OFFLINE=true.
FROM rust:1.98.1-slim-bookworm@sha256:ebd900bae66fd508b466cef82d64a83a5fb34682e4c8b2797a42908bddc95a57
# Bound cargo parallelism: cargo defaults to the host CPU count (18 here), and parallel rustc+ld
# for the test binaries exceeded the Docker VM memory (ld killed by signal 9). 4 jobs is safe on 8 GB.
ENV CARGO_BUILD_JOBS=4

ENV DEBIAN_FRONTEND=noninteractive
# aarch64 Linux does not enable fp16 by default and the gemm-f16 crate's inline asm requires it
# (amd64 is unaffected). Per-target rustflags in CARGO_HOME keep amd64 builds byte-identical.
RUN mkdir -p "${CARGO_HOME:-/usr/local/cargo}" && printf '[target.aarch64-unknown-linux-gnu]\nrustflags = ["-C", "target-feature=+fp16"]\n' >> "${CARGO_HOME:-/usr/local/cargo}/config.toml"
RUN apt-get update && apt-get install -y --no-install-recommends libssl-dev pkg-config \
        ca-certificates git curl pkg-config python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Python with the ONNX package only (the project's own ground-truth tool for hand-written
# test models: `onnx.helper` builders and `onnx.reference.ReferenceEvaluator`). No
# onnxruntime, no torch, no uv: run the repo's generator scripts with this interpreter
# directly (`python3 crates/onnx-tests/tests/<op>/<script>.py`). onnx 1.19.0 is the version
# the repo's `uv` inline-script headers pin.
RUN python3 -m venv /opt/py && /opt/py/bin/pip install --no-cache-dir onnx==1.19.0 numpy==2.4.6
ENV PATH=/opt/py/bin:$PATH

# `cargo insta` for the repo's inline snapshot tests (AGENTS.md: `cargo insta review`).
RUN cargo install cargo-insta --version 1.48.0

WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz \
    && git init -q && git config user.email task@example.invalid && git config user.name task \
    && git add -A && git commit -qm "tracel-ai/burn-onnx @ 6fc6bacec1c5264d1160efd4e2c904591082e2f8"

# All dependencies (crates.io + the pinned tracel-ai/burn git rev) into CARGO_HOME.
RUN cargo fetch --locked

# Warm build caches for the crates the task touches and the crates that test them, plus the
# onnx2burn CLI. `burn-onnx`'s dev-dependency `ort` runs a build script that downloads ONNX
# Runtime binaries for the (feature-gated, unrelated) export tests; running it here is what
# keeps `cargo test -p burn-onnx` working offline. The import path and generated code do not
# use it.
RUN cargo build -p burn-onnx --bin onnx2burn --locked \
    && cargo test --no-run --locked -p onnx-ir -p burn-onnx -p onnx-tests -p onnx-official-tests

ENV CARGO_NET_OFFLINE=true
ENV CARGO_TERM_COLOR=never
