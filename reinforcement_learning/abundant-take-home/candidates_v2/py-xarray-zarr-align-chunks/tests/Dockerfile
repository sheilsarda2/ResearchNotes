# Verifier image for py-xarray-zarr-align-chunks (separate, offline).
# Same base and hash-pinned wheels as the agent image. Holds a pristine copy of
# the base tree under /opt/pristine/repo; the agent's xarray/ package is
# re-materialised by Harbor at /workspace/repo/xarray before test.sh runs.
# zarr-python (the reference implementation for the differential checks) is
# already part of the pinned dependency set.
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=2025.6.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY image-source/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt

# Pristine tree (never modified) and the working tree the submission lands in.
COPY image-source/upstream.tar.gz /tmp/upstream.tar.gz
RUN mkdir -p /opt/pristine/repo /workspace/repo \
    && tar -xzf /tmp/upstream.tar.gz -C /opt/pristine/repo \
    && tar -xzf /tmp/upstream.tar.gz -C /workspace/repo \
    && rm /tmp/upstream.tar.gz \
    && cd /workspace/repo \
    && python -m pip install --no-cache-dir --no-deps --no-build-isolation -e . \
    && git init -q && git config user.email v@example.invalid && git config user.name Verifier \
    && git add . && git commit -qm "pristine base" \
    && python -c "import xarray, zarr; print('verifier deps ok', xarray.__version__, zarr.__version__)"

WORKDIR /workspace/repo
COPY . /tests
RUN chmod +x /tests/test.sh
