# Independent admin-space Receive test

`timestamp_adminspace.rs` tests the Zenoh v3 instruction's contract 7 through the public API. It is authored for a separate follow-up validation task; the existing v3 task, hidden tests, campaign, and scores are unchanged.

Integration target: copy the file to `zenoh/tests/timestamp_adminspace.rs` in the separate validation checkout. Run with the workspace's existing dependencies and default features plus `unstable`:

```sh
cargo test --offline -p zenoh --features unstable --test timestamp_adminspace \
  adminspace_query_invokes_receive_timestamp_callback -- --exact --nocapture
```

The test opens a router with a dynamic loopback TCP listener, admin read permission, and the documented `with_timestamp_callback` callback. A separate client reads the existing public admin key `@/{router.zid()}/router`. The first uninstrumented read checks readiness, the absence of a stack, and zero router callback calls. A second read enables only Receive. It must return the router's admin JSON and invoke the router callback exactly once with that router's `ZenohId` and `WhatAmI::Router`. The callback returns nonempty custom timestamp bytes.

Disabling Send and Route prevents forwarding instrumentation from satisfying the assertion. Receiving the final reply on a different client keeps the client's reply-handler Receive separate from the router's admin Receive. No user queryable, private runtime API, protocol type, internal trait, internal module path, fixed port, plugin, or new dependency is required. Every asynchronous operation is inside a 60-second Tokio deadline; sessions close on the successful path.

The existing admin-space read scenario and configuration come from pristine `zenoh/tests/adminspace.rs` in the v3 `tests/image-source/upstream.tar.gz`. Its public session locator access is the same API used by the pristine `zenoh-test` locator helper. The only new timestamp items imported are the instruction's public `zenoh::timestamp_stack::{TimestampContext, TimestampInstrumentationBuilder}` exports.

The assertion observes admin Receive through its public timestamp callback. It does not inspect the admin handler's private, consumed request stack. A mutant removing the admin Receive append while retaining routing interception must fail this test with zero router callback calls. The validation coordinator owns that separate reference/nop/mutant run and shared resource admission; this authoring task launches no runtime or model calls.

There is a separate reference limitation: the v3 gold patch appends Receive in admin `send_request`, but initializes its constructed query with `query_ts_stack: None`. Consequently, a test requiring the returned admin sample to carry the inherited query stack would also test reply-stack propagation and can fail that reference implementation. This test deliberately scopes its assertion to the admin Receive callback contract. It does not establish returned-stack propagation or prove that an implementation which invokes the callback but then discards its record preserves that private request stack.

Status at authoring: source-reviewed; compilation, reference outcome, and omission-mutant outcome await the coordinator's bounded validation. No historical trial is regraded or reclassified by this file.
