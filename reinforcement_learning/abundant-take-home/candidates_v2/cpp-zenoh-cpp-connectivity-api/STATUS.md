# cpp-zenoh-cpp-connectivity-api: status

**Stage: nop_pass (2026-09-13).** Harbor 0.15.0 on this arm64 host: oracle reward 1 (`*-oracle-20260913T124023Z`, wall 526 s), nop reward 0 (`*-nop-20260913T124023Z`, wall 284 s). Four defects fixed during validation, see the validation log at the end.

## What this task is

Form A, C++. eclipse-zenoh/zenoh-cpp PR #750 "connectivity api" (merged 2026-03-13, issue #749): wrap
zenoh-c #1171 / zenoh-pico #1183 so a `Session` exposes `get_transports()`, `get_links(optional
transport)`, callback / background / channel `TransportEventsListener` and `LinkEventsListener`
with `history` and transport filtering, six new `Owned<>` types (`Transport`, `Link`,
`TransportEvent`, `LinkEvent`, two listener templates with interop overloads), four closure
trampolines, and six pre-existing inverted `has_value()` fixes. Non-test gold patch: 27 files,
+1182/-51 (20 headers). The PR's own test (376 lines, 10 sub-tests, both backends) and
`tests/CMakeLists.txt` change are hidden.

Base commit `0dd5f5a98faffdd53ab0816eb892b521c3965f41` (first parent of merge `9b6391d5`).
Submodules pinned at the **merge commit's** SHAs (zenoh-c `8fd05be3`, zenoh-pico `b8f78562`): the
base zenoh-c pin already has the C API (two commits behind), but the base zenoh-pico pin predates
pico's connectivity API, so the bump is environment, not task. The tarball vendors both trees
(955 files, 1.27 MB gz).

## Oracle design

Separate offline verifier (`network_mode = "no-network"`), `[[artifacts]]` = `/workspace/repo/include` only.

1. **Anti-cheat / inventory**: `.hxx`-only, no symlinks, token counts not above pristine.
2. **Clean-room rebuild**: pristine tree at `/work/repo` + submitted `include/`; all 32 test targets, both backends, Debug; strict-warnings target (`-Werror`) for each backend.
3. **Router**: `zenohd` 1.8.0 (release build for the `*-unknown-linux-gnu` target, sha256-pinned per architecture; the "musl standalone" assets are dynamically linked against musl and do not run on Debian) on `tcp/127.0.0.1:27447`, multicast and gossip scouting off, readiness by TCP connect polling.
4. **Exact registration**: `ctest -N` must equal `hidden/expected_tests.txt` (32), also asserted at verifier image build.
5. **PR tests**: `connectivity_zenohc` and `connectivity_zenohpico`; ctest status plus 10 `=== test_* ===` / `PASS` markers and the final "All connectivity tests passed!" line parsed from `ctest -V`.
6. **Regression**: every pre-existing upstream test, run as router clients (`Config::create_default()` -> `test_config()` in the network tests, bodies unchanged); exact counts per backend (14 zenohc + 16 zenohpico after removing PR/authored entries).
7. **Authored**: `interop_optional_zenohc` checks the `as_moved_c_ptr(std::optional<...>)` contract for five entity types; `Subscriber`/`Queryable` come from their `FifoChannel` variants, `MatchingListener` and the two new listeners are built from their callback form with `Listener<Handler>(Listener<void>&&, Handler)` and a `FifoHandler<Sample>` handler (zenoh-c 8fd05be3 has no fifo/ring channel for `MatchingStatus`, `LinkEvent` or `TransportEvent`).
8. **Parity**: `parity_dump_zenohc` (built from the submitted headers) and `parity_dump.py` (zenoh-python 1.8.0, hash-pinned wheels, CPython 3.8.20 on x86_64 / 3.11 on aarch64 via uv) each connect to the router and dump transports, links, a filtered link query and the first history event of four listener flavours (callback link, callback transport, filtered callback link, background transport); `compare_parity.py` requires field equality with documented tolerances (`src` ephemeral port, `is_shm` C++-only).

Reward 1 iff every group passes and ctest exit code is 0; `score.json` records each group.

## Build plan and estimates (unmeasured)

| Image | Steps | Est. time (4 vCPU) | Est. size |
|---|---|---|---|
| agent | apt; extract + git baseline; zenoh-c Debug (`cargo build`, ~450 crates incl. zenoh git rev a688d70); zenoh-pico; zenohd; cmake configure both backends + build `tests` | 20-35 min | 4-6 GB |
| verifier | same + uv/CPython + zenoh-python wheel + prepare hidden tests + configure + `ctest -N` assertion + tolerant pristine `tests` build + pristine snapshots | 25-40 min | 5-7 GB |
| verifier run | build ~3-5 min; 32 serial ctest entries, upstream tests are sleep-heavy (~10-20 min); parity < 1 min | 12-25 min | |

Budgets in `task.toml`: agent 21600 s, verifier 3600 s, build 3600 s, 4 CPU, 8 GB, 30 GB. The zenoh-c
Debug build was chosen over Release because the release profile uses `lto = "fat"`,
`codegen-units = 1` (upstream CLAUDE.md also prescribes Debug).

## Validation checklist (next phase)

- [x] `docker build environment/` and `docker build tests/`; record minutes and GB in `provenance.json` (agent 3.19 GB, verifier 3.46 GB; cold builds ~2.7 min each on this host).
- [x] Verifier image build asserts `ctest -N` == expected list (32 entries registered exactly as listed).
- [x] No-op run (pristine include) -> reward 0 with `build` false (connectivity_zenohc, connectivity_zenohpico, interop_optional_zenohc, parity_dump_zenohc fail to compile); regression groups 14/14 and 15/15 still pass, ctest 29/32.
- [x] Oracle run (`solution/solve.sh`) -> reward 1; `parity_result.json` 42/42 with `interfaces == ["lo"]`, `priorities == [0, 7]`, `reliability == "reliable"`, `mtu == 65480`; no field was `None` on both sides, so `compare_parity.py` is unchanged.
- [ ] Mutants: drop `history` handling; invert transport filter; return empty string instead of `nullopt` for `group`; leave one `has_value()` inverted; forget `Transport` copy ctor. Each must yield reward 0 with a pointed group failure.
- [x] Time the verifier: test.sh 6 min 13 s end to end (ctest 6.3 min serial), well inside the 3600 s budget; no parallelism needed.
- [ ] Sonnet rollout gate.

## Risks (ordered)

1. **Router-client rewrite of upstream network tests is unvalidated.** Client-mode sessions through a shm-less router: `shm_api::run_transport_provider` waits for the session SHM provider (likely mode-independent) and `pub_sub` SHM payloads fall back to wire copies (test compares bytes). If any upstream test needs peer-mode topology it must be dropped from the regression set and the count updated.
2. **Multicast/loopback in `no-network`.** The design avoids multicast entirely (zenohd with `--no-multicast-scouting`, clients with scouting off, connectivity test uses explicit loopback endpoints). If Harbor's no-network still lacks a working `lo`, everything network-bound fails; verify first.
3. **Timing in upstream tests.** The PR test and upstream suites synchronise with 1 s sleeps (upstream design, kept verbatim). On a slow host a `sleep_for(1s)` after `close()` may precede the `DELETE` event; if flaky in validation, wrap only the connectivity binary in a retry (at most 2 attempts) and document it.
4. **zenoh-python 1.8.0 x86_64 wheel is cp38-only**; relies on uv 0.12.13 still serving CPython 3.8.20. Fallback: build the sdist (hash `1cb0b8ab…`) with maturin in the verifier image (+15 min) or move to 1.9.0 if it ships an abi3 x86_64 wheel and its field set is unchanged.
5. **Memlock / SHM in containers.** Upstream CI raises `memlock` for SHM tests on Ubuntu; if `shm_api`/`pub_sub` fail for that reason in Docker, either raise the limit in the compose config or drop the SHM-specific tests from regression.
6. **Build-time budget.** zenoh-c Debug plus the zenoh git clone should fit 3600 s on 4 vCPU, but a slow host could exceed it; mitigations: prebuilt `/opt/zenoh` tarball in the environment dir, or Release for zenoh-pico only (already).
7. **Contamination**: zenoh-c/zenoh-python/Rust siblings are public with the same vocabulary; tier A_recent by date only.

## Alternates considered

- **Form B excision** (fallback named in ASSIGNMENTS): remove `include/zenoh/api/liveliness.hxx` + `Session::liveliness_*`, or the `querier.hxx`/matching wrappers, verified by the upstream liveliness/queryable tests and a zenoh-python interop peer. Rejected for now because #750 is substantive (20 headers, six types, two backends) and has a natural cross-language oracle.
- **zenoh-c #1265 "create transport from fields"** as a follow-on task: too small (+227).
- **Second reference peer (Rust `z_info` example)**: dropped; zenoh-python is already an independent binding of the same core and the router is the shared ground truth.

## Files

```
environment/Dockerfile, environment/upstream.tar.gz (sha256 5f924299…, rewritten 2026-09-13 without AppleDouble members), environment/zenohd.sha256
solution/solve.sh, solution/changes.patch (sha256 301bdcab…)
tests/Dockerfile, tests/test.sh, tests/upstream.tar.gz (copy), tests/hidden/{connectivity.cxx, tests_CMakeLists.txt,
  zenoh_test_config.hxx, run_with_router.sh, prepare_tests.sh, interop_optional.cxx, parity_dump.cxx, parity_dump.py,
  compare_parity.py, anti_cheat.py, expected_tests.txt, requirements-zenoh-python.txt, zenohd.sha256}
instruction.md, task.toml, provenance.json, STATUS.md
```

## Validation log addendum (2026-09-13)
- Build parallelism was already bounded via `CARGO_BUILD_JOBS` in both Dockerfiles; kept as is. (Context: rs-burn-store OOM-killed the linker when cargo used all 18 host cores inside an 8 GB Docker VM.)

## Validation log (2026-09-13, Docker Desktop 29.5.3 on Apple Silicon, arm64 VM, 18 vCPU / 8.3 GB, Harbor 0.15.0)

Run 1 (`*-oracle-20260913T115504Z`, `*-nop-20260913T115504Z`): both failed at the agent image build,
`cmake -S zenoh-c` -> `tests/CMakeLists.txt:48 add_dependencies called with incorrect number of arguments`.

- **Defect 1, `environment/upstream.tar.gz` / `tests/upstream.tar.gz` (identical).** The archive had been
  re-packed with macOS bsdtar and carried 1910 members: the 955 real files plus 955 AppleDouble `._*`
  companions (163 bytes each, `com.apple.provenance`). macOS `tar -t` and macOS extraction hide them
  (they are folded back into xattrs), but on Linux they extract as regular files, so every
  `file(GLOB)` + `get_filename_component(NAME_WE)` loop (zenoh-c `tests/`, `examples/`, zenoh-cpp
  `tests/`) produced an empty target name for `._z_api_foo.c`, and the git baseline commit would have
  contained 955 junk files. Fix: rewrote the tarball with Python `tarfile`, dropping only the `._*`
  members; the remaining 955 members are byte-identical (name, mode, mtime, uid/gid, content), and
  the 186 zenoh-cpp blobs were checked equal to `git ls-tree -r 0dd5f5a9` in the shared clone.
  New sha256 `5f924299…` (was `83182a0b…`). Confirmed by a throwaway configure + zenoh-pico build.

Run 2 (`*-oracle-20260913T120334Z`): images built (env 2.7 min, verifier 3.7 min incl. run; the
zenoh-c Debug cargo build is fast on this host), oracle applied, verifier reward 0 with
`build=false`, `router=false`.

- **Defect 2, zenohd asset.** The pinned `zenoh-1.8.0-<arch>-unknown-linux-musl-standalone.zip`
  binaries are not static: `PT_INTERP /lib/ld-musl-<arch>.so.1`, `NEEDED libc.so libgcc_s.so.1`
  (musl flavour), so `bash` reports `cannot execute: required file not found` on Debian. Fix: both
  Dockerfiles and both `zenohd.sha256` files now pin the `*-unknown-linux-gnu-standalone.zip` assets of
  the same 1.8.0 release (aarch64 `78d88406…`, needs glibc >= 2.30; x86_64 `bc5a816d…`, needs
  glibc >= 2.34; bookworm has 2.36 and libgcc_s). Same router version and configuration.
- **Defect 3, verifier harness assumed channels for the listeners.** `interop_optional.cxx` obtained
  `MatchingListener`, `LinkEventsListener` and `TransportEventsListener` through
  `declare_*(channels::FifoChannel)`, and `parity_dump.cxx` used a channel link listener. zenoh-c
  `8fd05be3` has no `z_fifo_channel_{matching_status,link_event,transport_event}_new` (nor ring), so
  `FifoHandlerData<LinkEvent>` etc. cannot exist, upstream PR #750's channel-variant templates are
  uninstantiable with the built-in channels, and upstream's own `Publisher::declare_matching_listener(Channel)`
  does not even compile at the base commit (`into_cb_handler_pair<Query>` for a matching-status
  closure). The PR's own test uses no channels. Fixes: the authored test now builds the three
  handler-bearing listeners from their callback form with the public
  `Listener<Handler>(Listener<void>&&, Handler)` constructor and a `channels::FifoHandler<Sample>`
  handler (exactly the `as_moved_c_ptr(std::optional<...>)` contract is still asserted, empty and
  populated, for all five types; `Subscriber`/`Queryable` still come from `FifoChannel`); the parity
  probe's link-event history step uses the callback listener with a condition variable and a 10 s
  deadline (same JSON). `instruction.md` was amended in three places because it promised what no
  implementation can deliver with the vendored zenoh-c: the sentence "events must work with
  `channels::FifoChannel` / `RingChannel` exactly like `Sample`" now states that the channel
  primitives do not exist for these event types and that the channel-variant templates must still be
  declared with the shown signatures; grading items 3 and 4 describe the callback-built listeners.
  The oracle (`solution/changes.patch`) is unchanged and still equals the PR's non-test diff. This is a
  relaxation only in the sense that the channel variants are declared but not exercised; the
  callback, background, history, filtering, undeclare and interop contracts are all still graded.

Local verifier dry run (verifier image built from `tests/` with the fixes above, oracle headers, `--network none`,
`--cpus 4 --memory 8g`): `build`, `router`, `registered_tests`, `pr_tests` (10/10 markers, both backends),
`regression_zenohc` 14/14, `regression_zenohpico` 15/15, `authored` all passed; `ctest` 32/32 in 6.3 min
(`connectivity_zenohpico` 60 s, `pub_sub_zenohc` 36 s, `cancellation_zenohpico` 37 s are the slowest; no
suite needed an extra timeout). Only `parity` failed:

- **Defect 4, `parity_dump.py` vs the zenoh-python 1.8.0 runtime.** The wheel's `.pyi` stub declares
  `Session.info()` as a method and `WhatAmI` / `Reliability` / `SampleKind` as `enum.Enum`, but the pyo3
  runtime exposes `info` as a getter (`'SessionInfo' object is not callable`) and the enums as pyo3
  classes without `.name` (`str(WhatAmI.ROUTER) == "router"`, `str(SampleKind.PUT) == "SampleKind.PUT"`).
  Fix: `attr_or_call()` accepts either shape for `info` / `zid` / `transports` / `links`, and
  `enum_name()` lower-cases the member name from `.name` or the textual form. Re-run of the parity
  step: 42/42 checks equal (router zid, `whatami=router`, `is_qos=true`, `is_multicast=false`,
  `mtu=65480`, `interfaces=["lo"]`, `priorities=[0,7]`, `reliability=reliable`, `group`/`auth_identifier`
  null, PUT history events on both sides). `compare_parity.py` is unchanged.

Run 3 (`*-oracle-20260913T124023Z`, `*-nop-20260913T124023Z`, Harbor 0.15.0, other Harbor builds running on
the host): **oracle reward 1** (environment_setup 136.5 s, verifier 385.6 s incl. image build, wall 526 s; all
nine groups ok, ctest 32/32, 10/10 connectivity markers per backend, parity 42/42) and **nop reward 0**
(environment_setup 5.6 s, verifier 275.5 s, wall 284 s; `build` false because connectivity_zenohc,
connectivity_zenohpico, interop_optional_zenohc and parity_dump_zenohc do not compile against the pristine
headers, hence `pr_tests`, `authored`, `parity` false; `regression_zenohc` 14/14 and `regression_zenohpico`
15/15 still pass, ctest rc 8, 29/32). Image sizes (local rebuilds of the same Dockerfiles): agent 3.19 GB,
verifier 3.46 GB. No check was weakened beyond the channel-variant relaxation documented under Defect 3;
no upstream suite needed an extra timeout. Files changed during validation: both `upstream.tar.gz`, both
Dockerfiles, both `zenohd.sha256`, `tests/hidden/{interop_optional.cxx,parity_dump.cxx,parity_dump.py}`,
`instruction.md` (three sentences), `provenance.json`, this file. `solution/changes.patch` untouched.
- Hygiene note (post-validation): the regenerated tarball still contains `zenoh-c/.DS_Store` (harmless to the build). Remove it in a final packaging pass and re-record the archive sha256 in provenance.json; that changes the Harbor task checksum, so re-run oracle/nop afterwards.
- Hygiene pass: removed `zenoh-c/.DS_Store` from `environment/upstream.tar.gz` (deterministic repack, member list otherwise identical); archive sha256 updated in provenance.json. Re-validation queued.
