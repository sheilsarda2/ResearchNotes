# Timestamp instrumentation for latency measurement in Zenoh

You are working in `/workspace/repo`, a checkout of the Eclipse Zenoh Rust workspace
(`eclipse-zenoh/zenoh`, version 1.9.0, Rust 1.97.1 via `rust-toolchain.toml`). The
workspace builds and tests offline: every crate in `Cargo.lock` is already in the cargo
registry cache, `CARGO_NET_OFFLINE=true` is set, and the `target/` directory is pre-warmed
for the invocations listed under **Building and testing**. Do not add dependencies or edit
`Cargo.toml`/`Cargo.lock` files: only the `src/` directories listed under **What is
collected** are taken from your work; every manifest, lockfile, test directory and helper
crate is replaced by its pristine copy before verification.

## Goal

Add **timestamp instrumentation** to Zenoh so that an application can measure where latency
accrues along the path of a message. When a publication, a query or a reply is sent with an
*instrumentation configuration*, the message carries a **timestamp stack**: an ordered list
of records, each stating at which **interception point** it was recorded and the timestamp
taken there. Every Zenoh node the message traverses (the sending application, each routing
layer, the receiving application) appends its record if that point is enabled in the
configuration. The receiving application reads the stack from the `Sample`, `Query` or
`ReplyError` it gets.

The feature is part of the `unstable` API surface of the `zenoh` crate: all new public items
are gated with `#[zenoh_macros::unstable]` / `#[cfg(feature = "unstable")]`, exactly like
`source_info` and `cancellation` are today. With the `unstable` feature disabled the crate
must still build and behave as before.

The feature spans four crates of the workspace and must interoperate on the wire with the
upstream implementation (see **Wire format** and **Verification**):

- `commons/zenoh-protocol`: message model (new extension on `Push`, `Request`, `Response`).
- `commons/zenoh-codec`: encoding and decoding of the new extension.
- `zenoh`: public API, session send/receive paths, routing layer, admin space.
- `zenoh-ext`: `AdvancedPublisher` publications accept the same configuration.

## Public API (crate `zenoh`, feature `unstable`)

Module `zenoh::timestamp_stack` re-exports the following items (the tests import them from
this path):

- `enum InterceptionPoint { Send, Route, Receive }` — `Debug, Clone, Copy, PartialEq, Eq`.
  `impl TryFrom<u8>` accepts the wire values below (bit 7, the custom-format flag, is
  ignored) and returns `zenoh_result::Error` for anything else; `impl From<InterceptionPoint> for u8`
  yields the wire value. Wire values: `Send = 0b001`, `Route = 0b010`, `Receive = 0b100`.
- `struct TimestampInstrumentationBuilder` — `Debug, Default, Clone, Copy`; `new()`,
  by-value chainable `set_send(bool)`, `set_route(bool)`, `set_receive(bool)` (setting a point
  twice or unsetting it again must work), and `build(self) -> ZResult<TimestampInstrumentation>`
  which fails when no point is enabled.
- `struct TimestampInstrumentation` — `Debug, Default, Clone, Copy, PartialEq, Eq`;
  `is_instrumented(&self, InterceptionPoint) -> bool`.
- `enum InstrumentationTimestamp { UHLC(uhlc::Timestamp), Custom(Vec<u8>) }` — `Debug, Clone, PartialEq, Eq`.
- `struct TimestampStackRecord` — `Debug, Clone, PartialEq, Eq`; `point() -> InterceptionPoint`,
  `is_custom() -> bool`, `timestamp() -> &InstrumentationTimestamp`.
- `struct TimestampStack` — `Debug, Clone, PartialEq, Eq`; `instrumentation() -> TimestampInstrumentation`
  (the configuration the sender used, preserved end to end) and `records() -> &[TimestampStackRecord]`
  (traversal order).
- `#[non_exhaustive] struct TimestampContext { pub zid: ZenohId, pub whatami: WhatAmI }`.

Builder entry points. A method
`timestamp_instrumentation<TS: Into<Option<TimestampInstrumentation>>>(self, TS) -> Self`
must be callable **without importing any trait** (follow the existing
`SampleBuilderTrait`/`#[zenoh_macros::internal_trait]` pattern; expose the trait as
`TimestampInstrumentationBuilderTrait` from `zenoh::internal::traits` next to the other builder
traits) on:

- the put and delete builders returned by `Publisher::put`, `Publisher::delete`,
  `Session::put`, `Session::delete` (`PublicationBuilder<_, _>`),
- `Session::get` (`SessionGetBuilder`) and `Querier::get` (`QuerierGetBuilder`),
- `SampleBuilder<_>`,
- `zenoh_ext::AdvancedPublisher::put` / `delete` (`AdvancedPublicationBuilder`), forwarding to the
  inner publication.

Session-level callback: `OpenBuilder::with_timestamp_callback<F>(self, F) -> Self` where
`F: Fn(TimestampContext) -> Vec<u8> + Send + Sync + 'static`, e.g.
`zenoh::open(config).with_timestamp_callback(cb).await`.

Accessors: `Sample::timestamp_stack(&self) -> Option<&TimestampStack>`,
`Query::timestamp_stack(&self) -> Option<&TimestampStack>`,
`ReplyError::timestamp_stack(&self) -> Option<&TimestampStack>`. A `Sample` built with
`SampleBuilder::timestamp_instrumentation` carries an empty stack with that configuration.

## Behavioral contract

1. **No configuration, no stack.** A publication, query or reply issued without a configuration
   carries no extension on the wire; receivers see `None`. Replies to an uninstrumented query
   carry nothing.
2. **Where points fire.**
   - `Send`: once, in the application that issues the publication/query, and once more in the
     application that issues a reply (`Query::reply*`, `Query::reply_err`, `reply_del`).
   - `Route`: once per node whose routing layer forwards the message: the routing layer of the
     sending session, of every intermediate router or peer, and of the receiving session. Hence a
     peer-to-peer hop adds two `Route` records and a client→router→client path adds three.
   - `Receive`: once, in the application that delivers the message to a subscriber, a queryable
     or the reply handler.
   A record is appended only if its point is enabled in the configuration; the configuration
   itself travels unchanged.
3. **Ordering.** Records are appended in traversal order. Examples the tests check
   (`S`=Send, `R`=Route, `E`=Receive, all points enabled): same session `S E`; peer↔peer
   publication `S R R E` (with only Route enabled: `R R`; with Send+Receive: `S E`);
   client→router→client publication `S R R R E` (Route only: `R R R`); a delete behaves like a put.
   A query received by a queryable carries the query's records (`S E` peer-to-peer,
   `S R R R E` routed). A reply's stack is the query's stack **as received by the replier**
   followed by the reply's own records: `S E S E` peer-to-peer, ten records
   `S R R R E S R R R E` routed, `R R R R R R` routed with Route only. `reply_err` replies
   carry the stack on `ReplyError`.
4. **Local delivery records exactly one `Receive`.** When a session delivers to its own
   subscribers or queryables (publisher and subscriber in the same session, or a queryable local
   to the querier), the stack is `S E` with a single `Receive`; a remote subscriber/queryable of
   the same publication/query also sees exactly one `Receive`. This holds for
   `Locality::SessionLocal`, `Locality::Remote` and `Locality::Any` destinations, and for local
   plus remote queryables answering the same query (`ConsolidationMode::None`).
5. **Timestamps.** By default a record holds a UHLC timestamp taken from the node's hybrid
   logical clock; **its id is the node's `ZenohId`** (the same id `Session::zid()` reports),
   whether or not the node has `timestamping` enabled in its configuration. If the node's session
   was opened with `with_timestamp_callback`, every record *that node* produces (at any point)
   holds the callback's bytes instead, `is_custom()` is `true`, and the callback receives the
   node's `zid` and `whatami`. Callbacks of different nodes never mix: a node without a callback
   keeps producing UHLC records even when the message already carries custom ones. A callback
   returning an empty vector produces no record.
6. **Limits and robustness.** A stack never grows beyond 255 records (further interceptions are
   dropped, not encoded). A decoder rejects a stack whose configuration byte is zero or whose
   record count exceeds 255. Records with an unknown interception point or an undecodable UHLC
   timestamp are skipped (forward compatibility) without dropping the message.
7. **Routers and the admin space** participate like any node: a router in `Router` mode adds
   `Route` records, and the admin space records `Receive` for what it delivers.
8. **Everything else is unchanged**: QoS, attachments, source info, timestamps (`ext_tstamp`),
   consolidation, liveliness, interceptors and the existing test suites keep working.

## Wire format (must match upstream byte for byte)

The stack travels as a **non-mandatory ZBuf-kind extension with id `0x7`** on the network
messages `Push`, `Request` and `Response` (declare it with `zextzbuf!(0x7, false)` next to
the existing extensions of each message; the id is free in all three). Give the three message
structs a public field `ext_ts_stack: Option<...>` for it, count it in the `Z` flag and the
extension count like the other extensions, encode it after the existing extensions, and make
`rand()` (feature `test`) populate it randomly with 0 to 3 records. Extension body:

```
u8    conf_flags        bit0 Send, bit1 Route, bit2 Receive; must be non-zero
zint  count             number of records (0..=255)
count x record:
  u8    flags           bits 0-2 interception point (exactly one of 0b001/0b010/0b100),
                        bit 7 set when the timestamp is custom bytes, other bits 0
  zbuf  timestamp       length-prefixed bytes: for UHLC records the standard codec
                        encoding of `uhlc::Timestamp` (as used by `ext_tstamp`);
                        for custom records the callback's bytes verbatim
```

## What is collected

Only these directories are taken from your work and overlaid onto a pristine tree:
`commons/zenoh-protocol/src`, `commons/zenoh-codec/src`, `zenoh/src`, `zenoh-ext/src`.
The in-crate test modules under `zenoh/src/tests/` and `zenoh/src/net/tests/` are part of
`zenoh/src`: keep every existing test function (the verifier checks that each pristine test
name still exists and passes) and adapt their struct literals to the new field. Do not
reference verifier paths, spawn processes, or read files from `zenoh/src`; the collected
sources are scanned for such patterns.

## Building and testing

Pre-warmed invocations (use these to stay incremental):

```
cargo test --offline -p zenoh -p zenoh-codec --features zenoh/test,zenoh/unstable,zenoh/internal \
  --lib --test codec --test session --test routing --test queryable --test attachments \
  --test source_info --test qos --test unicity --test matching --test adminspace
cargo test --offline -p zenoh-ext --features unstable,internal --test advanced
```

Write your own integration tests under `zenoh/tests/` while developing (use
`zenoh_test::TestSessions`, `tcp/127.0.0.1:0` endpoints and multicast scouting disabled; the
container has no external network). They are not collected.

## Verification

The verifier rebuilds from your collected sources in a clean tree, offline, then runs:

- the upstream integration test for this feature (47 tests exercising every item above:
  builder semantics, `TryFrom<u8>`, same-session, peer-to-peer and routed pub/sub and
  query/reply, deletes, `reply_err`, locality, custom callbacks and their context, querier);
- **differential interop**: your build exchanges instrumented publications, queries and
  replies over TCP with a reference peer compiled from the upstream implementation, in peer,
  client and router roles on both sides, and with the upstream **zenoh-python** bindings.
  Record order, points, custom bytes and UHLC ids (equal to the recording node's zid) must
  match on both ends; the reference implementations exist only in the verifier image;
- an `AdvancedPublisher` test in `zenoh-ext`;
- the pristine suites listed above, the zenoh-codec roundtrip tests and all in-crate tests,
  with exact pristine counts.

Reward is 1 only if every group passes.
