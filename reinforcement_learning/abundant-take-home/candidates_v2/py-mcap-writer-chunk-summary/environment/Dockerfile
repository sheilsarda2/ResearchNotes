# Agent environment: Python 3.10 (the version pinned by python/pyproject.toml upstream),
# runtime deps pinned from upstream python/uv.lock, editable install of the excised package.
# The tree is foxglove/mcap@aebd536b pruned to python/mcap + the format specification, with the
# writer's chunk/index/summary subsystem excised (see instruction.md). No conformance corpus
# except the single OneMessage case used by an upstream unit test.
FROM python:3.10.21-slim-bookworm@sha256:68d914ec641a0b69267ce65184d000a2bc3a9ee2590ab702b82250ab2385735a

RUN apt-get update && apt-get install -y --no-install-recommends bash git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz \
    && python -m pip install --no-cache-dir --no-deps --no-build-isolation -e python/mcap \
    && python -c "import mcap.writer, mcap.reader, lz4.frame, zstandard; print('mcap', mcap.__version__)"
RUN git init -q && git config user.email task@example.invalid && git config user.name Task \
    && git add . && git commit -qm "foxglove/mcap aebd536b, python package with writer summary/index subsystem excised"
