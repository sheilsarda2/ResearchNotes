# Verifier image: same Python base as the agent image + pristine tree + full conformance corpus
# + Rust and Go reference readers built from the same upstream commit. Runs with no network.
FROM rust:1.98.1-slim-bookworm@sha256:ebd900bae66fd508b466cef82d64a83a5fb34682e4c8b2797a42908bddc95a57 AS rust-builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY image-source/reference-src.tar.gz /tmp/ref.tar.gz
RUN tar -xzf /tmp/ref.tar.gz -C /src && rm /tmp/ref.tar.gz
RUN cargo build --release --locked -p mcap --example conformance_reader --example conformance_indexed_reader \
    && install -D target/release/examples/conformance_reader /out/conformance_reader \
    && install -D target/release/examples/conformance_indexed_reader /out/conformance_indexed_reader

FROM golang:1.22.12-bookworm@sha256:3d699e4d15d0f8f13c9195c0632a16702b8cbdece2955af1c23b37ae5d55a253 AS go-builder
WORKDIR /src
COPY image-source/reference-src.tar.gz /tmp/ref.tar.gz
RUN tar -xzf /tmp/ref.tar.gz -C /src && rm /tmp/ref.tar.gz
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
RUN cd go && go build -o /out/test-read-conformance ./conformance/test-read-conformance

FROM python:3.10.21-slim-bookworm@sha256:68d914ec641a0b69267ce65184d000a2bc3a9ee2590ab702b82250ab2385735a
RUN apt-get update && apt-get install -y --no-install-recommends bash git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
COPY image-source/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt
# Pristine tree twice: /workspace/repo receives the submitted python/mcap/mcap from Harbor's
# artifact upload (same path as in the agent container); /opt/pristine is the untouched copy
# used to restore tests and compare.
COPY image-source/pristine.tar.gz /tmp/pristine.tar.gz
RUN mkdir -p /opt/pristine /workspace/repo \
    && tar -xzf /tmp/pristine.tar.gz -C /opt/pristine \
    && tar -xzf /tmp/pristine.tar.gz -C /workspace/repo && rm /tmp/pristine.tar.gz
WORKDIR /workspace/repo
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation -e python/mcap
COPY --from=rust-builder /out/ /opt/ref/
COPY --from=go-builder /out/ /opt/ref/
# Smoke-test the reference readers against one corpus file at build time.
RUN /opt/ref/conformance_reader tests/conformance/data/OneMessage/OneMessage.mcap > /dev/null \
    && /opt/ref/conformance_indexed_reader tests/conformance/data/TenMessages/TenMessages-ch-chx-mx-rch-rsh-st-sum.mcap > /dev/null \
    && /opt/ref/test-read-conformance tests/conformance/data/OneMessage/OneMessage.mcap streamed > /dev/null \
    && /opt/ref/test-read-conformance tests/conformance/data/TenMessages/TenMessages-ch-chx-mx-rch-rsh-st-sum.mcap indexed > /dev/null \
    && test "$(find tests/conformance/data -name '*.mcap' | wc -l)" = 416
COPY run_conformance.py authored_checks.py test.sh /tests/
RUN chmod +x /tests/test.sh
