# Verifier image for rs-zenoh-timestamp-instrumentation (separate, offline at runtime).
# Contents:
#   /workspace/build   pristine base tree (a62451c0) + warm target dir  -> clean-room build root
#   /opt/pristine      untouched copy of the base tree (reference for restores / test lists)
#   /opt/gold          base + upstream merge 89ab32cb (PR #2620 incl. its tests); reference only
#   /opt/gold/bin/ts_peer   reference interop peer built from the gold tree
#   /opt/zpy           venv with zenoh-python (PR #739, f4bbce9) compiled against the gold tree
#   /opt/expected      `cargo test -- --list` output of the pristine tree (exact-count oracle)
#   /tests             this directory (test.sh, score.py, hidden tests, peers)
FROM rust:1.97.1-bookworm@sha256:0e2bcaef56d041a486784e54104a81aebe0da44bd03019bd70bc0401e42e4a97
# Bound cargo parallelism: cargo defaults to the host CPU count (18 here), and parallel rustc+ld
# for the test binaries exceeded the Docker VM memory (ld killed by signal 9). 4 jobs is safe on 8 GB.
ENV CARGO_BUILD_JOBS=4

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        pkg-config cmake python3 python3-venv python3-pip jq rsync \
    && rm -rf /var/lib/apt/lists/*

# Same cargo settings as the agent image so fingerprints line up.
ENV CARGO_INCREMENTAL=0 \
    CARGO_PROFILE_DEV_DEBUG=line-tables-only \
    CARGO_TERM_COLOR=never \
    RUST_BACKTRACE=1 \
    CARGO_TARGET_DIR=/workspace/build/target

COPY image-source/upstream.tar.gz /tmp/upstream.tar.gz
COPY image-source/gold.patch /tmp/gold.patch
RUN mkdir -p /workspace/build /opt/pristine /opt/gold \
    && tar -xzf /tmp/upstream.tar.gz -C /workspace/build \
    && tar -xzf /tmp/upstream.tar.gz -C /opt/pristine \
    && tar -xzf /tmp/upstream.tar.gz -C /opt/gold \
    && cd /opt/gold && git apply /tmp/gold.patch \
    && rm /tmp/upstream.tar.gz /tmp/gold.patch

WORKDIR /workspace/build
RUN cargo fetch --locked

# --- Reference peer from the gold tree (shares the target dir; gold crates have their own
#     package ids, so nothing built here is reused for the submission's crates).
COPY ts_peer /opt/gold/tools/ts_peer
RUN cd /opt/gold \
    && sed -i 's|^members = \[|members = [\n  "tools/ts_peer",|' Cargo.toml \
    && cargo build --offline -p ts_peer \
    && install -D -m 0755 /workspace/build/target/debug/ts_peer /opt/gold/bin/ts_peer \
    && /opt/gold/bin/ts_peer --role router --listen tcp/127.0.0.1:0 </dev/null | head -1

# --- zenoh-python (PR #739 merge) compiled against the gold tree: cross-language oracle.
ARG ZENOH_PYTHON_REV=f4bbce9dbbba3d41ba8847849056d4e6a17168b5
ARG MATURIN_VERSION=1.15.0
COPY image-source/patch_zenoh_python.py /tmp/patch_zenoh_python.py
RUN git clone --quiet --filter=blob:none https://github.com/eclipse-zenoh/zenoh-python.git /tmp/zenoh-python \
    && cd /tmp/zenoh-python && git checkout --quiet "$ZENOH_PYTHON_REV" \
    && python3 /tmp/patch_zenoh_python.py /tmp/zenoh-python/Cargo.toml /opt/gold \
    && rm /tmp/patch_zenoh_python.py
RUN python3 -m venv /opt/zpy \
    && /opt/zpy/bin/pip install --no-cache-dir "maturin==${MATURIN_VERSION}" \
    && cd /tmp/zenoh-python \
    && CARGO_TARGET_DIR=/tmp/zpy-target \
       CARGO_PROFILE_RELEASE_LTO=off CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 \
       CARGO_PROFILE_RELEASE_OPT_LEVEL=1 CARGO_PROFILE_RELEASE_DEBUG=0 \
       /opt/zpy/bin/maturin build --release --out /opt/wheels --interpreter /opt/zpy/bin/python3 \
    && /opt/zpy/bin/pip install --no-cache-dir /opt/wheels/*.whl \
    && rm -rf /tmp/zpy-target /tmp/zenoh-python /root/.cache \
    && /opt/zpy/bin/python3 -c "import zenoh; from zenoh import TimestampInstrumentationBuilder, InterceptionPoint; print('zenoh-python ok', zenoh.__version__ if hasattr(zenoh,'__version__') else '')"

# --- Warm the pristine build in the exact configurations test.sh uses, and record the
#     pristine test lists (exact-count oracle for the upstream regression groups).
#     cargo prints the per-binary "Running ... (<bin>)" headers on stderr and libtest prints the
#     names on stdout; the scorer needs both streams to attribute names to binaries (2>&1).
#     `--list --ignored` records the tests upstream itself marks #[ignore]: they are not demanded
#     from the submission, and only those may report as ignored at run time.
RUN cargo test --offline --no-run -p zenoh -p zenoh-codec \
        --features zenoh/test,zenoh/unstable,zenoh/internal \
        --lib --test codec --test session --test routing --test queryable --test attachments \
        --test source_info --test qos --test unicity --test matching --test adminspace \
    && cargo test --offline --no-run -p zenoh-ext --features unstable,internal --test advanced \
    && mkdir -p /opt/expected \
    && cargo test --offline -p zenoh -p zenoh-codec \
        --features zenoh/test,zenoh/unstable,zenoh/internal \
        --lib --test codec --test session --test routing --test queryable --test attachments \
        --test source_info --test qos --test unicity --test matching --test adminspace \
        -- --list > /opt/expected/group_a.list 2>&1 \
    && cargo test --offline -p zenoh -p zenoh-codec \
        --features zenoh/test,zenoh/unstable,zenoh/internal \
        --lib --test codec --test session --test routing --test queryable --test attachments \
        --test source_info --test qos --test unicity --test matching --test adminspace \
        -- --list --ignored > /opt/expected/group_a.ignored.list 2>&1 \
    && cargo test --offline -p zenoh-ext --features unstable,internal --test advanced \
        -- --list > /opt/expected/group_b.list 2>&1 \
    && cargo test --offline -p zenoh-ext --features unstable,internal --test advanced \
        -- --list --ignored > /opt/expected/group_b.ignored.list 2>&1 \
    && rm -rf target/debug/incremental \
    && grep -c '^ *Running ' /opt/expected/group_a.list /opt/expected/group_b.list \
    && grep -c ': test$' /opt/expected/group_a.list /opt/expected/group_b.list \
    && du -sh target

ENV CARGO_NET_OFFLINE=true
COPY . /tests
RUN chmod +x /tests/test.sh /tests/score.py /tests/py_peer.py
