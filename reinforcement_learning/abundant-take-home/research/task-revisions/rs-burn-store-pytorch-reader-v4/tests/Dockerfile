# Verifier image for rs-burn-store-pytorch-reader.
# The first stages are byte-identical to environment/Dockerfile so the two builds share
# layers; on top it keeps a pristine source copy, a pinned CPU-only PyTorch, the generated
# fixture matrix and the hidden tests. Runs with network_mode = "no-network".
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
COPY image-source/upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz

# Network is available only here. Download and compile every dependency of the graded
# targets so that `cargo test --offline` works for them afterwards. Only these packages are
# guaranteed to build offline; the rest of the workspace (GPU backends, tch, wgpu) is not
# fetched.
RUN cargo --version && rustc --version \
    && cargo test --locked --no-run -p burn-store -p burn-pack -p pytorch-tests -p safetensors-tests \
    && du -sh /workspace/repo/target /usr/local/cargo/registry

# ---- verifier-only layers -------------------------------------------------------------

# Pristine source tree (no target/) for restoring test directories over the submission.
RUN mkdir -p /opt/pristine \
    && tar -C /workspace/repo --exclude=./target -cf - . | tar -C /opt/pristine -xf - \
    && test -f /opt/pristine/crates/burn-store/src/pytorch/mod.rs

# Pinned CPU-only PyTorch: the independent reference that writes the fixture matrix.
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/torch \
    && /opt/torch/bin/pip install --no-cache-dir --disable-pip-version-check \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        "torch==2.13.0+cpu" "numpy==2.4.6" \
    && /opt/torch/bin/python -c "import torch, numpy; print(torch.__version__, numpy.__version__)"

# Fixture matrix and reference manifest, produced once at image build.
COPY fixtures/gen_fixtures.py /opt/gen_fixtures.py
RUN /opt/torch/bin/python /opt/gen_fixtures.py /opt/fixtures \
    && cat /opt/fixtures/summary.txt \
    && grep -q '^accept_files=28$' /opt/fixtures/summary.txt \
    && grep -q '^reject_files=32$' /opt/fixtures/summary.txt \
    && grep -q '^tensor_rows=428$' /opt/fixtures/summary.txt

# Pre-compile the harness against the pristine crate so the verifier's rebuild is incremental.
COPY harness/fixture_matrix.rs /workspace/repo/crates/burn-store/tests/fixture_matrix.rs
RUN cargo test --locked --no-run -p burn-store --test fixture_matrix

ENV CARGO_NET_OFFLINE=true

COPY . /tests
RUN chmod +x /tests/test.sh
