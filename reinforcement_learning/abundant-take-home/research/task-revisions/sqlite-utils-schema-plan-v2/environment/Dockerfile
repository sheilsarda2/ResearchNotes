FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

RUN apt-get update && apt-get install -y --no-install-recommends bash git curl ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -m pip install --no-cache-dir --no-deps --no-build-isolation -e .
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN git init -q && git config user.email task@example.invalid && git config user.name Task \
    && git add . && git commit -qm "Pinned sqlite-utils baseline"
