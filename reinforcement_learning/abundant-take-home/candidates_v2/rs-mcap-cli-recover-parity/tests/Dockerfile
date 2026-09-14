# Verifier image for rs-mcap-cli-recover-parity.
# Stage 1: build the Go reference CLI and the Go conformance reader from the SAME upstream
# commit (go/ tree of the base archive). Stage 2: the agent image recipe (warm Rust target)
# plus a pristine copy of the tree, the reference binaries, and the generated fixture set.
# Build context is this tests/ directory; Harbor copies it to /tests and runs /tests/test.sh.

FROM golang:1.22.12-bookworm@sha256:3d699e4d15d0f8f13c9195c0632a16702b8cbdece2955af1c23b37ae5d55a253 AS goref
WORKDIR /src
COPY image-source/upstream.tar.gz /tmp/upstream.tar.gz
RUN mkdir -p /src/repo && tar -xzf /tmp/upstream.tar.gz -C /src/repo go/ \
 && rm /tmp/upstream.tar.gz
ENV CGO_ENABLED=1
WORKDIR /src/repo/go
# go.work pins the workspace to the local modules (go/mcap, go/cli/mcap, go/conformance/*).
RUN cd cli/mcap && go build -tags sqlite_omit_load_extension -ldflags "-s -w -X github.com/foxglove/mcap/go/cli/mcap/cmd.Version=cb59283-ref" -o /out/mcap-go . \
 && cd ../../conformance/test-read-conformance && go build -o /out/mcap-go-dump . \
 && /out/mcap-go version && ls -la /out

FROM rust:1.95.0-bookworm@sha256:6258907abe69656e41cd992e0b705cdcfabcbbe3db374f92ed2d47121282d4a1
RUN apt-get update \
 && apt-get install -y --no-install-recommends git python3 pkg-config cmake xxd bc procps \
 && rm -rf /var/lib/apt/lists/*

# Pristine tree (never touched by artifacts) and the warm build tree at the agent path.
COPY image-source/upstream.tar.gz /tmp/upstream.tar.gz
RUN mkdir -p /opt/pristine/repo /workspace/repo \
 && tar -xzf /tmp/upstream.tar.gz -C /opt/pristine/repo \
 && tar -xzf /tmp/upstream.tar.gz -C /workspace/repo \
 && rm /tmp/upstream.tar.gz
WORKDIR /workspace/repo/rust
RUN cargo fetch --locked \
 && cargo build --locked -p mcap-cli \
 && cargo test --locked --no-run -p mcap-cli \
 && cargo test --locked --no-run -p mcap --all-features \
 && ./target/debug/mcap --version
ENV CARGO_NET_OFFLINE=true CARGO_TERM_COLOR=never NO_COLOR=1

# Reference binaries live off PATH under a name the Rust build must not reference.
COPY --from=goref /out/mcap-go /opt/reference/bin/mcap-go
COPY --from=goref /out/mcap-go-dump /opt/reference/bin/mcap-go-dump
RUN /opt/reference/bin/mcap-go version && chmod 0755 /opt/reference/bin/*

# Hidden harness and generated fixtures (deterministic; includes the 512 MiB streaming input).
COPY . /tests
RUN chmod +x /tests/test.sh \
 && python3 /tests/harness/gen_fixtures.py --repo /opt/pristine/repo --out /tests/fixtures \
      --go-bin /opt/reference/bin/mcap-go --big \
 && python3 /tests/harness/selfcheck.py --fixtures /tests/fixtures --repo /opt/pristine/repo \
      --go-bin /opt/reference/bin/mcap-go --dump-bin /opt/reference/bin/mcap-go-dump

WORKDIR /workspace/repo
