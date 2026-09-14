# Verifier image for cpp-mcap-indexed-reader (runs offline; everything is fetched at build).
#
# Same Ubuntu 22.04 snapshot as the agent image. The C++ binaries are rebuilt at verify time with
# clang 14 against the *system* copies of the same library versions Conan pins upstream
# (catch2 2.13.8, nlohmann-json 3.10.5) plus liblz4 1.9.3 / libzstd 1.4.8 (decompression only).
# Reference readers: Go 1.26.8 (go.work asks for >= 1.22.5) and Rust 1.98.1 (crate MSRV 1.81).
FROM ubuntu:22.04@sha256:829f6df217bcbae2b371026e81711d1a787c61b2967ad09d015063663ebafbf7

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential clang make python3 git ca-certificates curl xz-utils pkg-config patch \
    catch2 nlohmann-json3-dev liblz4-dev libzstd-dev \
    && rm -rf /var/lib/apt/lists/*

# Go (pinned tarball, checksum from https://go.dev/dl/?mode=json)
ENV GO_VERSION=1.26.8
# Architecture-aware download (both checksums from https://go.dev/dl/?mode=json).
RUN arch="$(dpkg --print-architecture)" \
    && case "$arch" in \
         amd64) sum=d0f743b33e8d8945e6b1f432edd15785c70507121d6e2a723b21285eddf8b57b ;; \
         arm64) sum=211ffced9dcb9633a55eac6364816ec0ddd951389a740e88fa8b3337971bdda0 ;; \
         *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o /tmp/go.tgz "https://go.dev/dl/go${GO_VERSION}.linux-${arch}.tar.gz" \
    && echo "$sum  /tmp/go.tgz" | sha256sum -c - \
    && tar -C /usr/local -xzf /tmp/go.tgz && rm /tmp/go.tgz
ENV PATH=/usr/local/go/bin:/root/.cargo/bin:/root/go/bin:$PATH
ENV GOTOOLCHAIN=local

# Rust (pinned toolchain)
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain 1.98.1 --no-modify-path

# Pristine upstream sources at the base commit: cpp/ (headers + tests), go/, rust/, Cargo manifests.
COPY pristine.tar.gz /tmp/pristine.tar.gz
RUN mkdir -p /opt/pristine/repo && tar -xzf /tmp/pristine.tar.gz -C /opt/pristine/repo && rm /tmp/pristine.tar.gz

# Go module dependencies of the upstream indexed conformance runner (go.work workspace). The
# binary itself is built further down from harness/go_indexed_reader.go, see there.
RUN cd /opt/pristine/repo/go/conformance/test-read-conformance && go mod download \
    && rm -rf /root/go/pkg/mod/cache/download

# Rust reference indexed reader (conformance example of the mcap crate at the base commit).
RUN mkdir -p /opt/ref/bin && cd /opt/pristine/repo \
    && sed -i 's#, "rust/cli"##' Cargo.toml \
    && cargo build -p mcap --example conformance_indexed_reader \
    && cp target/debug/examples/conformance_indexed_reader /opt/ref/bin/ \
    && rm -rf target /root/.cargo/registry/src /root/.cargo/registry/cache \
    && /opt/ref/bin/conformance_indexed_reader --help >/dev/null 2>&1 || true

# Hidden harness, vendored corpus subset (the 16 variants the C++ indexed runner supports) and the
# generated fixtures with their analytic expectations.
COPY harness /tests/harness
COPY corpus /tests/corpus
COPY fixtures /tests/fixtures
COPY test.sh /tests/test.sh

# Go reference indexed reader. The upstream go/conformance/test-read-conformance tool panics
# (nil *Schema dereference) on any file with a schema-less channel (schema_id 0), which every
# generated fixture has; the TypeScript conformance harness never feeds it such inputs. The
# harness ships a copy of its readIndexed with that one guard added and builds it with
# `go build -overlay` on top of the pristine module, so it links the very same go/mcap library
# and dependency versions as the upstream tool (go.work workspace of the base commit).
RUN cd /opt/pristine/repo/go/conformance/test-read-conformance \
    && go build -overlay /tests/harness/go_overlay.json -o /opt/ref/bin/go-indexed-reader . \
    && go vet -overlay /tests/harness/go_overlay.json . \
    && rm -rf /root/go/pkg/mod/cache/download /root/.cache/go-build

# Self-check at build time: the pristine headers must earn reward 1 through the exact verify path,
# which also proves the Go and Rust readers agree with the analytic fixture expectations. A failure
# here fails the image build (and prints score.json) rather than silently mis-scoring later.
RUN { SUBMISSION=/opt/pristine/repo/cpp/mcap/include/mcap OUT=/tmp/selfcheck bash /tests/test.sh \
      && grep -qx 1 /tmp/selfcheck/reward.txt; } \
    || { echo "SELF-CHECK FAILED; score.json follows"; cat /tmp/selfcheck/score.json; exit 1; }
RUN cp /tmp/selfcheck/score.json /opt/ref/selfcheck-score.json && rm -rf /tmp/selfcheck /opt/verify
