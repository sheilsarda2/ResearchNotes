# Verifier image for py-zarr-python-cast-value-scale-offset.
#
# Stage 1 builds the independent Rust reference (zarrs, pinned git commit) used as the
# cross-implementation oracle for the cast_value codec. Stage 2 is the same Python base as the
# agent image with the same pinned dependencies, the pristine upstream tree, the oracle binary,
# the hidden PR tests and the differential harnesses. The verifier runs with no network.
FROM rust:1.92-slim-bookworm@sha256:f1f73538ebe623fd3673a35aff3df358ae1084c64c55646516e5b17b321b6c9b AS oracle-build
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git pkg-config \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /oracle
COPY oracle/Cargo.toml oracle/Cargo.lock ./
COPY oracle/src ./src
# --locked: the committed Cargo.lock pins every crate and the zarrs git revision.
RUN cargo build --release --locked && strip target/release/zarrs_oracle \
    && ./target/release/zarrs_oracle 2>&1 | head -1

FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7
RUN apt-get update && apt-get install -y --no-install-recommends bash git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Pristine upstream tree (identical archive to the agent image).
WORKDIR /workspace/repo
COPY image-source/upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz
COPY image-source/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -m pip install --no-cache-dir --no-deps --no-build-isolation -e . \
    && python -c "import zarr, cast_value_rs"

# A second pristine copy: test.sh restores upstream tests from here after overlaying the
# submission, so nothing the agent transferred can influence what the verifier executes.
RUN mkdir -p /pristine && tar -C /workspace/repo -cf - . | tar -C /pristine -xf -

COPY --from=oracle-build /oracle/target/release/zarrs_oracle /usr/local/bin/zarrs_oracle
RUN /usr/local/bin/zarrs_oracle 2>&1 | head -1

COPY . /tests
RUN chmod +x /tests/test.sh
