# Agent image: onnx main (RNN family excised) built from source; no onnxruntime.
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
# Debian bookworm ships protobuf 3.21.12 (libprotobuf32); upstream Linux CI builds against the distro protobuf too.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash git ca-certificates build-essential libprotobuf-dev protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-build.txt requirements-runtime.txt /tmp/
RUN python -m pip install -r /tmp/requirements-build.txt -r /tmp/requirements-runtime.txt

WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz

# Editable (redirect-mode) install: Python edits under /workspace/repo take effect immediately,
# compiled onnx_cpp2py_export and generated *_pb2 modules live in site-packages.
ENV ONNX_ML=1 CMAKE_BUILD_PARALLEL_LEVEL=4
RUN CMAKE_ARGS="-DONNX_USE_PROTOBUF_SHARED_LIBS=ON -DONNX_WERROR=OFF" \
    python -m pip install -e . -v --no-build-isolation --no-deps 2>&1 | tail -n 40 \
    && python -c "import onnx, onnx.defs; from onnx.reference import ReferenceEvaluator; from onnx.reference.ops import load_op; print('onnx', onnx.__version__, 'opset', onnx.defs.onnx_opset_version())" \
    && find /workspace/repo -name __pycache__ -prune -exec rm -rf {} +

RUN git init -q && git config user.email task@example.invalid && git config user.name Task \
    && git add . && git commit -qm "Pinned onnx baseline"
