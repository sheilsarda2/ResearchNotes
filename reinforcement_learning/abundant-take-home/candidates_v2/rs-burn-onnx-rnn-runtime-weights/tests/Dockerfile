# Separate offline verifier image for rs-burn-onnx-rnn-runtime-weights.
#
# Same pinned toolchain as environment/Dockerfile. Adds, verifier-only: the onnxruntime
# reference (Python), the PR's hidden tests, and the onnxruntime differential corpus, all
# produced at image build. Nothing is fetched or generated at verify time.
FROM rust:1.98.1-slim-bookworm@sha256:ebd900bae66fd508b466cef82d64a83a5fb34682e4c8b2797a42908bddc95a57
# Bound cargo parallelism: cargo defaults to the host CPU count (18 here), and parallel rustc+ld
# for the test binaries exceeded the Docker VM memory (ld killed by signal 9). 4 jobs is safe on 8 GB.
ENV CARGO_BUILD_JOBS=4

ENV DEBIAN_FRONTEND=noninteractive
# aarch64 Linux does not enable fp16 by default and the gemm-f16 crate's inline asm requires it
# (amd64 is unaffected). Per-target rustflags in CARGO_HOME keep amd64 builds byte-identical.
RUN mkdir -p "${CARGO_HOME:-/usr/local/cargo}" && printf '[target.aarch64-unknown-linux-gnu]\nrustflags = ["-C", "target-feature=+fp16"]\n' >> "${CARGO_HOME:-/usr/local/cargo}/config.toml"
RUN apt-get update && apt-get install -y --no-install-recommends libssl-dev pkg-config \
        ca-certificates git python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Reference implementation (verifier image only): onnxruntime CPU + onnx.
RUN python3 -m venv /opt/ref && /opt/ref/bin/pip install --no-cache-dir \
        onnxruntime==1.30.0 onnx==1.22.0 numpy==2.4.6

WORKDIR /workspace/repo
COPY image-source/upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz

# Hidden tests from the upstream PR (merge 438d2cdb): crates/onnx-tests/tests/{gru,lstm,rnn}
# (Rust tests, generator scripts, committed .onnx fixtures), the build.rs that registers the
# fixtures, and expectations.toml with the 11 promoted corpus rows.
RUN mkdir -p /opt/task
COPY hidden-overlay.tar.gz /opt/task/hidden-overlay.tar.gz
RUN tar -xzf /opt/task/hidden-overlay.tar.gz -C /workspace/repo

RUN cargo fetch --locked

# Warm build against the pristine base sources so the verifier's rebuild after overlaying
# the submission is incremental. Done before the differential rows are added: at base, a
# pass-listed row that panics in codegen would fail this build.
RUN cargo build -p burn-onnx --bin onnx2burn --locked \
    && cargo test --no-run --locked -p onnx-ir -p onnx-tests -p onnx-official-tests

# onnxruntime differential corpus, generated at IMAGE BUILD: 24 authored RNN-family cases
# in upstream node-test layout (model.onnx + input_*.pb + onnxruntime output_*.pb), the
# expectations rows for them, and the models the importer must reject.
COPY gen_rnn_cases.py /opt/task/gen_rnn_cases.py
RUN /opt/ref/bin/python /opt/task/gen_rnn_cases.py /opt/task/extra \
        /workspace/repo/crates/onnx-official-tests/vendor/node \
    && cp -r /opt/task/extra/node/test_rnnrt_* /workspace/repo/crates/onnx-official-tests/vendor/node/ \
    && cat /opt/task/extra/expectations_rnnrt.toml >> /workspace/repo/crates/onnx-official-tests/expectations.toml

# Pristine copy of the full tree (without target/) that test.sh restores before overlaying
# only the submitted src/ directories.
RUN tar -czf /opt/task/pristine.tar.gz --exclude=./target -C /workspace/repo .

ENV CARGO_NET_OFFLINE=true
ENV CARGO_TERM_COLOR=never

COPY . /tests
RUN chmod +x /tests/test.sh
