# Agent environment for cpp-mcap-indexed-reader.
#
# Base: Ubuntu 22.04 (jammy), the distribution upstream CI uses for its C++ builds
# (cpp/ci.Dockerfile, cpp/dev.Dockerfile). Toolchain versions come from that snapshot:
#   gcc 11.4.0, clang 14.0.0, cmake 3.22.1, python 3.10.12, GNU make 4.3.
# Conan is pinned; the Conan recipes are pinned by cpp/test/conanfile.py and cpp/mcap/conanfile.py
# (catch2/2.13.8, nlohmann_json/3.10.5, lz4/1.9.4, zstd/1.5.2).
FROM ubuntu:22.04@sha256:829f6df217bcbae2b371026e81711d1a787c61b2967ad09d015063663ebafbf7

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates curl cmake gnupg make perl python3 python3-pip \
    clang clang-format git patch less xxd file \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 --no-cache-dir install conan==2.32.0

WORKDIR /workspace/repo
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN tar -xzf /tmp/upstream.tar.gz -C /workspace/repo && rm /tmp/upstream.tar.gz

# Upstream's dev image builds with clang.
ENV CC=clang
ENV CXX=clang++

# Fetch and build every Conan dependency, build the C++ test binaries once so the build directory
# (cpp/test/build/Debug) is warm, run the test suite that ships with the base commit, then cut the
# network dependency: afterwards `./build.sh --build-tests-only` resolves everything from the local
# Conan cache and works offline.
RUN cd cpp && ./build.sh --build-tests-only \
    && ./test/build/Debug/bin/unit-tests \
    && ./test/build/Debug/bin/unit-tests-nocompress \
    && conan remote disable conancenter

WORKDIR /workspace/repo
