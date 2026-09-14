# Agent environment for py-zarr-python-cast-value-scale-offset.
# zarr-python at 279d400a19d510df6384bb1e93c4817dc867072b (parent of the #3874 merge), all
# runtime + test dependencies pre-installed from pinned wheels (resolved as of 2026-05-01), the
# optional cast-value-rs kernel package included. No network is needed after the build.
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

RUN apt-get update && apt-get install -y --no-install-recommends bash git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz

COPY requirements.txt /tmp/requirements.txt
# Build backend (hatchling, hatch-vcs, editables) is part of the pinned set so the editable
# install below runs without build isolation and without touching the network again.
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -m pip install --no-cache-dir --no-deps --no-build-isolation -e . \
    && python -c "import zarr, cast_value_rs, numcodecs, numpy; print(zarr.__version__, numpy.__version__)"

# A git baseline so the agent (and reviewers) can diff their work.
RUN git init -q && git config user.email task@example.invalid && git config user.name Task \
    && git add -A && git commit -qm "zarr-python baseline 279d400a (pre-#3874)"
