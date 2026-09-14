# Agent environment for py-xarray-zarr-align-chunks.
# pydata/xarray at d21d79e91646c883a2daff8faeb7f948080e73ed (2025-06-04), the first
# parent of the PR #10336 merge. Every wheel predates 2025-06-05 (uv --exclude-newer)
# and is hash-pinned in requirements.txt. Network is used only during this build.
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=2025.6.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so the layer is shared with the verifier image.
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt

WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz \
    && python -m pip install --no-cache-dir --no-deps --no-build-isolation -e . \
    && python -c "import xarray, zarr, dask, numpy, pandas, netCDF4, h5netcdf, scipy; print('xarray', xarray.__version__, 'zarr', zarr.__version__, 'dask', dask.__version__, 'numpy', numpy.__version__)" \
    && python -m compileall -q xarray > /dev/null

# A git baseline so the agent can diff its work; not used by the verifier.
RUN git init -q \
    && git config user.email task@example.invalid \
    && git config user.name Task \
    && git add . \
    && git commit -qm "pydata/xarray d21d79e (2025-06-04) pinned baseline"
