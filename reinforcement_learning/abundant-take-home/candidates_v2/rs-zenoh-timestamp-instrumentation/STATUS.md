# rs-zenoh-timestamp-instrumentation — status

**Stage:** nop_pass — Harbor 0.15.0 oracle reward 1 (`rs-zenoh-timestamp-instrumentation-oracle-20260913T164927Z`,
1042 s wall) and nop reward 0 (`...nop-20260913T170741Z`, 75 s) on Docker Desktop arm64, 2026-09-13. Authored 2026-09-13.
See the validation log at the end for the verifier fixes and the one documented relaxation (nine network-dependent
upstream tests skipped in the `no-network` verifier).

## Choice

- **Chosen:** eclipse-zenoh/zenoh#2620 "Add timestamp instrumentation for latency measurement"
  (merged 2026-07-21, squash commit `89ab32cb`, base = its single parent `a62451c0`).
  Non-test gold diff: 35 files, +1588/-30 across `zenoh-protocol`, `zenoh-codec`, `zenoh`,
  `zenoh-ext` (3 layers: wire model+codec, session/routing/runtime, builders). Its own 47-test
  integration file (`zenoh/tests/timestamp_instrumentation.rs`, +1614) uses only public API and
  the centralized `zenoh_test::TestSessions` helper (#2573, dynamic ports), so it is hidden
  verbatim. Sister PR zenoh-python#739 (merged the same day) pins **exactly** the gold commit in
  its `Cargo.lock`, which makes the cross-language oracle a faithful build rather than a guess.
- **Rejected alternates:**
  - #2320 "Support multiple connections in the client mode" (May 20, +613/-154): labeled `bug`,
    smaller, its tests are edits spread across ten existing suites (hard to hide cleanly), and it
    touches `Cargo.lock`/`DEFAULT_CONFIG.json5` which we keep pristine. Viable fallback.
  - #2397 "wait until callback drop in undeclare" (Feb 11, +1657/-345): older (weaker recency
    tier), API-heavy with two new test files; good second alternate if #2620 fails validation.

## Layout

```
environment/Dockerfile            rust:1.97.1-bookworm@sha256:0e2bcaef…, apt deps, git-init base,
                                  cargo fetch --locked, warm `cargo test --no-run` (2 invocations)
environment/upstream.tar.gz       git archive of a62451c0 (sha256 3e4d52aa…), no .git
solution/changes.patch, solve.sh  gold non-test diff (git apply --check OK against base)
tests/Dockerfile                  pristine tree + warm target, /opt/pristine, /opt/gold (base+gold.patch),
                                  /opt/gold/bin/ts_peer, /opt/zpy venv with zenoh-python@f4bbce9 built
                                  against /opt/gold, /opt/expected/*.list (pristine `--list` output)
tests/test.sh, tests/score.py     clean-room overlay, anti-cheat, builds, 9 scored groups, reward/score.json
tests/hidden/…                    timestamp_instrumentation.rs (47, upstream), ts_interop.rs (6+4, authored),
                                  zenoh-ext ts_advanced.rs (2, authored)
tests/ts_peer/, tests/py_peer.py  reference peers (identical CLI + JSON-lines protocol)
tests/image-source/               upstream.tar.gz copy, gold.patch (full PR), patch_zenoh_python.py
```

## Oracle design

1. **Hidden PR tests** (47): builder, TryFrom, same-session / p2p / routed pub-sub and query-reply,
   deletes, reply_err, locality, custom callbacks, querier. Run single-threaded; one retry of
   at most five failed names (upstream tests are sleep-based).
2. **Differential interop** (`interop_*`, 6): the submission's session in-process exchanges
   instrumented puts/deletes/queries/replies over TCP with `ts_peer` built from the gold tree,
   in every role split (agent pub ↔ gold sub, gold pub ↔ agent sub, gold router between agent
   clients, agent router between gold clients, gold queryable, agent queryable). Checks record
   order and points, custom bytes, and that every UHLC record id equals the zid of the node that
   produced it (so agent-side records are attributable and decodable by upstream).
3. **zenoh-python parity** (`pyparity_*`, 4): same scenarios with `py_peer.py` on the
   zenoh-python wheel compiled against the gold tree. Feasible: zenoh-python@f4bbce9 pins zenoh
   `89ab32cb` and only needs its `zenoh`/`zenoh-ext` git deps rewritten to `path = /opt/gold/…`.
4. **zenoh-ext**: authored `ts_advanced.rs` (2) + pristine `advanced.rs` (12).
5. **Upstream regressions with exact pristine counts**: `--lib` (zenoh 63 in-crate tests incl.
   `src/tests`, `src/net/tests`; zenoh-codec), `codec` (54), `session`, `routing`, `queryable`,
   `attachments`, `source_info`, `qos`, `unicity`, `matching`, `adminspace`. Expected names come
   from `cargo test -- --list` recorded on the pristine tree at image build; every pristine test
   must run and pass; in-crate test names must all still exist (the agent owns those files).
6. **Anti-cheat**: transferred `src/` scanned for `process::Command`, `/logs`, `/tests/`,
   `/opt/gold`, peer names, env names, `include_bytes!`, symlinks; `#[ignore`, `atexit`, `env::var`,
   `include_str!` may not grow versus pristine. All manifests, lockfile, test dirs and
   `commons/zenoh-test` are restored from `/opt/pristine`.

Reward 1 requires all nine groups; `score.json` carries per-binary pass/fail/ignored counts,
failed test names, retries and anti-cheat counts for attribution.

## Build plan and estimates (4 vCPU, 8 GB)

| Step | Est. minutes | Est. size |
|---|---|---|
| rust:1.97.1-bookworm base + apt | 2 | 1.9 GB |
| `cargo fetch --locked` (≈500 crates) | 2–3 | 1.0 GB |
| warm A: `-p zenoh -p zenoh-codec --features zenoh/test,zenoh/unstable,zenoh/internal --lib --test codec …9 suites` | 12–15 | 5–6 GB (incremental off, line-tables-only) |
| warm B: `-p zenoh-ext --features unstable,internal --test advanced` | 3–4 | +0.8 GB |
| **agent image total** | **~22–25** | **~10 GB** |
| verifier: gold `ts_peer` (shares target dir; gold crates rebuilt, external deps reused) | 6–8 | +1.5 GB |
| verifier: zenoh-python wheel (maturin 1.15.0, opt-level 1, no LTO, own target dir deleted) | 12–18 | +0.1 GB kept |
| **verifier image total** | **~45–55** | **~13 GB** |
| verifier run: rebuild changed crates + link 14 test binaries | 6–10 | |
| verifier run: hidden 47 (single-threaded, ≈1 s sleeps) + interop 10 + ext 14 + regressions | 8–14 | |
| **verifier run total** | **~15–25** | within 3600 s |

Storage 30 GB fits both images; memory 8 GB is enough for `rustc` at 4 jobs (zenoh's biggest
crate peaks ≈2 GB).

## Risks (ordered)

1. **Verifier image build time** (~50 min est.) against the 3600 s build timeout. Mitigations
   ready: build zenoh-python with `--profile dev`, or move the wheel build into a pre-built base
   image; last resort drop `parity_python` (keeps `interop_gold` as the differential oracle).
2. **Timing-dependent upstream tests.** The PR's 47 tests and the regression suites use 1 s
   sleeps for declaration propagation. Run single-threaded with one retry of failed names;
   regressions run with default parallelism as in CI. Validate flake rate over 3 runs.
3. **Unvalidated authored harness.** `ts_peer`, `ts_interop.rs`, `ts_advanced.rs`, `py_peer.py`
   are uncompiled/unrun (no builds in this phase). Assumptions to confirm in the build phase:
   `cargo test --test NAME` with two `-p` packages accepts a name present in one of them;
   `ZenohId` Display equals `uhlc::ID` Display (wrapper delegates; used for id comparison);
   `zenoh::Config::default()` opens under `--network none` (multicast join on loopback-only logs
   warnings, does not fail); zenoh-python stubs expose `timestamp_stack`/`records`/`point`/
   `is_custom` as properties (peer uses a call-if-callable helper); pyo3 0.25 + Python 3.11.
4. **Under-specification vs. gold.** The instruction fixes the wire format, field name
   `ext_ts_stack`, the UHLC-id-equals-zid rule and the exact record-count tables; anything the
   hidden tests assert beyond that is listed in `provenance.json:derivability`. Reviewer pass
   still recommended for the local-delivery rules (contract 4).
5. **Agent-owned in-crate tests.** `zenoh/src/tests` and `zenoh/src/net/tests` travel with the
   submission (they must compile against the agent's internals). Name preservation + pass is
   enforced; weakening an assertion inside an existing test is not detectable. Low value target.

## Next steps

1. Build both images on a ≥16 GB host; record minutes/GB in `provenance.json:build`.
2. Oracle run (`solve.sh`) must give reward 1; no-op run must give 0 with `build_a` or
   `hidden_pr_tests` failing (the base tree lacks the API, so the hidden test does not compile).
3. Mutants: drop the `Route` push in `route_send_response` (breaks routed reply counts and
   interop), skip the 255 cap, use a fresh HLC id instead of zid (interop id check), encode the
   extension with id 0x8 (interop decode), forget `AdvancedPublicationBuilder` forwarding.
4. Rollout gate: one Sonnet-5 attempt; reject if solved under 40 steps.

## Validation log addendum (2026-09-13)
- Build parallelism bounded with `CARGO_BUILD_JOBS=4` in both Dockerfiles after rs-burn-store OOM-killed the linker (`ld terminated with signal 9`) when cargo used all 18 host cores inside an 8 GB Docker VM.

## Validation log (2026-09-13)
- Harbor oracle run 1: images built; verifier build A failed with `zenoh_protocol::network::Request has no field ext_ts_stack` although the transferred `commons/zenoh-protocol/src` contains the field: cargo treated the pristine `zenoh-protocol` in the warm target dir as fresh (copied sources with older mtimes). Fix: `touch` every overlaid source file after the copy. Rerun queued after rosbag2.

## Validation log (2026-09-13, fix loop)
- Harbor oracle run 2 (`rs-zenoh-timestamp-instrumentation-oracle-20260913T123155Z`): reward 0. anti_cheat,
  build_a (31 s), build_b, hidden_pr_tests (47/47), interop_gold (6/6) PASS. Defects found and fixed:
  1. **`lib_test_names` compared two empty lists** (`expected_total: 0`). cargo prints the per-binary
     `Running ... (<bin>)` headers on stderr; the Dockerfile recorded `/opt/expected/*.list` with `> file`
     and test.sh listed with `2>/dev/null`, so `parse_list` could attribute no names to any binary. The same
     empty expected list made **`upstream_regressions` a false PASS** (8 failed tests, cargo exit 101:
     `--expect-list` iterated over zero binaries). Fix: both `--list` captures use `2>&1`; the Dockerfile also
     records `--list --ignored` (upstream's own `#[ignore]`s: `static_failover_brokering`,
     `three_node_combination_multicast`); `score.py parse-run` fails closed on an empty expected list, flags
     every binary with failures or a non-`ok` status whether expected or not, requires cargo exit 0
     (`--exit-code`), and only tolerates ignores that upstream itself has (`--expect-ignored`).
  2. **`parity_python` 0/4**: `py_peer.py` died at import — `InterceptionPoint` (pyo3 class) defines `__eq__`
     but no `__hash__`, so the dict keyed by it raised `TypeError: unhashable type`. Fix: equality-based
     lookup (`point_name`). The zenoh-python API was probed live in the image (property access for
     `records`/`point`/`is_custom`, `timestamp()` returns `Timestamp` printed `<ntp64>/<id>` or `bytes` for
     custom records) and the fixed peer cross-checked against the gold `ts_peer` in every role split
     (sub/pub both ways, router in the middle, queryable/get both ways, callback bytes, delete, uninstrumented,
     reply_err): all field-for-field equal (`scratch peer_e2e.py`, 25 s).
  3. **`zenoh_ext` ts_advanced 0/2**: the authored test created an `AdvancedPublisher` with a cache
     (`Sequencing::Timestamp`) without enabling `timestamping`, so construction bailed
     (`advanced_publisher.rs:407`). Fix: `local_config()` enables
     `timestamping = ModeDependentValue::Unique(true)` exactly like upstream `zenoh-ext/tests/advanced.rs`.
  4. **Nine upstream tests cannot pass in the verifier's network namespace** (Harbor `no-network` =
     loopback only). Verified on the PRISTINE tree in the verifier image: they fail under `--network none`
     (also single-threaded) and pass on the same image with a bridge network (`test_advanced_late_joiner`
     50 s, `gossip` 8 s, `three_node_combination` 84 s). Two mechanisms: (a) IP multicast — default-config
     peers discover each other only through UDP multicast scouting on 224.0.0.224, or the test binds a
     `udp/224.0.0.x` link (`No such device`): `zenoh_session_multicast`, `test_adminspace_read`, `qos_pubsub`,
     `qos_pubsub_overwrite_config`, `test_accept_replies`, `test_queryable_different_sessions`; (b) gossip-
     discovered peer links — peers listening on the default `tcp/[::]:0` advertise no locator when loopback is
     the only interface: `gossip`, `three_node_combination`, `test_advanced_late_joiner`. Harbor 0.15 offers
     only `public|no-network|allowlist`, and CONVENTIONS.md requires `no-network` for the verifier, so these
     nine are skipped (`--exact --skip`, exact match so `gossip_regression_*` still run) and removed from the
     expected names (`--exclude`, recorded per run in `score.json:excluded_tests`). Everything else in their
     binaries still runs with exact names. None of them asserts anything about the timestamp feature.
     **Relaxation, documented here and in test.sh.**
  5. **Verdict interleaving** (found in run 3): with `--test-threads=1` libtest prints `test NAME ... ` before
     the test runs and the verdict after; a tracing `ERROR` line from the test landed in between, so
     `test_callback_drop_on_undeclare_advanced_sample_miss_listener` (passed, binary 11/11) parsed as
     "not passed". Fix: `parse_run` keeps the pending name until a verdict token starts a later line.
  Policy changes: every group runs `--test-threads=1` (upstream suites synchronize with 1 s sleeps and were
  load-sensitive: another heavy image build shared the host); one bounded retry (≤5 failed names, each rerun
  alone once, all must pass, names recorded in `score.json:retried`) applies to every test group; cargo exit
  codes are enforced. `finish` still requires all nine groups.
- Harbor oracle run 3 (`rs-zenoh-timestamp-instrumentation-oracle-20260913T161604Z`): reward 0 only because of
  defect 5 (zenoh_ext parsed one passing test as missing); hidden 47/47, interop 6/6, parity 4/4, ts_advanced
  2/2, advanced 11/11 (late_joiner excluded); upstream_regressions parsed 8 passing lib/adminspace/routing/codec tests as missing for the same
  reason although every binary passed (exit 0). Run 4 (`...oracle-20260913T163543Z`, 16:35Z) had all eight groups
  through zenoh_ext PASS with the fixed parser when the host's memory watchdog killed the Harbor wrapper at
  497 s (another OOM-prone image build shared the 8 GB VM); orphaned verifier container removed, run repeated.
- Harbor oracle run 5 (`rs-zenoh-timestamp-instrumentation-oracle-20260913T164927Z`): **reward 1**, wall 1042 s (verifier 16.6 min:
  build A 56 s, hidden 100 s, interop+parity 23 s, zenoh_ext 298 s,
  regressions 496 s); no retries were needed (score.json has no `retried` keys); regressions: zenoh lib 101, adminspace 5,
  attachments 2, matching 24, qos 0 (2 excluded), queryable 1, routing 6 (+2 upstream-ignored), session 8, source_info 4,
  unicity 2, codec 53, all exit 0.
- Harbor nop run (`rs-zenoh-timestamp-instrumentation-nop-20260913T170741Z`): **reward 0**, wall 75 s — build_a fails (cargo exit 101, 78 rustc errors: the hidden tests use `zenoh::timestamp_stack`,
  `timestamp_instrumentation`, `with_timestamp_callback`, absent from the base tree); all later groups record
  "not run: earlier group failed". The earlier nop record (`...nop-20260913T123155Z`, 66 s) predates the verifier
  fixes and was superseded.
