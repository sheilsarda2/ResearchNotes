# Verifier image: same toolchain and source build as the agent image, plus onnxruntime,
# the pristine upstream tests for the family, and the verifier harness. Runs with no network.
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash git ca-certificates build-essential libprotobuf-dev protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

COPY image-source/requirements-build.txt image-source/requirements-runtime.txt image-source/requirements-verifier.txt /tmp/
RUN python -m pip install -r /tmp/requirements-build.txt -r /tmp/requirements-runtime.txt -r /tmp/requirements-verifier.txt

WORKDIR /workspace/repo
COPY image-source/upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz

ENV ONNX_ML=1 CMAKE_BUILD_PARALLEL_LEVEL=4
RUN CMAKE_ARGS="-DONNX_USE_PROTOBUF_SHARED_LIBS=ON -DONNX_WERROR=OFF" \
    python -m pip install -e . -v --no-build-isolation --no-deps 2>&1 | tail -n 40 \
    && python -c "import onnx, onnxruntime; from onnx.reference import ReferenceEvaluator; print('onnx', onnx.__version__, 'onnxruntime', onnxruntime.__version__)" \
    && find /workspace/repo -name __pycache__ -prune -exec rm -rf {} +

# The agent's starting point for onnx/reference; test.sh overlays the submission on this copy.
RUN mkdir -p /opt/excised && cp -a /workspace/repo/onnx/reference /opt/excised/reference

COPY . /tests
RUN chmod +x /tests/test.sh /tests/harness/*.py
