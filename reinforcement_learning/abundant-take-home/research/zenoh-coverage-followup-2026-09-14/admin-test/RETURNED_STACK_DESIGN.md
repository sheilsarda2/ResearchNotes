# Exploratory admin reply-stack contract check

The written specification supports requiring returned admin replies to preserve the query stack. Contract 3 requires a reply to inherit the query's records as received by the replier, then append its own records. Contract 7 makes admin space participate like other nodes and requires Receive on admin delivery; it gives no exception to reply inheritance. Contract 2 supplies the reply-handler Receive, and contract 5 requires each node's configured custom timestamp bytes. The existing public admin read replies through `Query::reply`, so these requirements meet in an observable public response.

`timestamp_adminspace_reply_stack.rs` is a separate exploratory test. The original `timestamp_adminspace.rs` callback probe and its README remain unchanged. The callback probe detects an omitted admin interception hook, but it cannot prove the produced record survives into a reply and does not close the full contract by itself.

The new scenario opens a Router with admin read enabled and a separate Client on a dynamic loopback TCP connection. Each session has a different nonempty timestamp callback marker. After an uninstrumented readiness read, the client reads `@/{router.zid()}/router` with only Receive enabled. The returned sample must contain the actual router's admin JSON and a stack with the original instrumentation configuration and exactly two ordered custom records:

1. `Receive`, router marker: the query delivered to the router's admin handler.
2. `Receive`, client marker: the reply delivered to the querying client.

Send and Route are explicitly disabled. This avoids relying on incidental routing counts and prevents a routing timestamp, a reply-only stack, a callback-only implementation, or an unobservable discarded request record from satisfying the assertion. It uses the instruction's public `zenoh::timestamp_stack` exports, ordinary public session/config/admin APIs, and existing Tokio/serde_json dependencies. It imports no internal runtime or protocol paths and reads no implementation source at execution time.

Copy only into a separate validation checkout as `zenoh/tests/timestamp_adminspace_reply_stack.rs`:

```sh
cargo test --offline -p zenoh --features unstable --test timestamp_adminspace_reply_stack \
  adminspace_reply_preserves_received_query_stack -- --exact --nocapture
```

The authored test has a 60-second asynchronous bound. It is not part of the existing v3 grader or frozen 99-cell campaign. No runtime/model calls were made while authoring it. The coordinator owns separately labelled compilation and execution, including preserving a gold failure if observed.

## Source findings and potential correction

The exact v3 gold source and oracle solution contain the same relevant production hunks. In `zenoh/src/net/runtime/adminspace.rs`, `AdminSpace::send_request` appends Receive to the incoming extension, then creates `QueryInner` with `query_ts_stack: None` (both patch files, line 2664). The pristine `local_data` handler calls `query.reply(prefix, payload)`. In gold `zenoh/src/api/queryable.rs`, reply construction initializes `Response.ext_ts_stack` to `None` and only fills it from `self.inner.query_ts_stack` (both patches, line 1328). The request stack therefore appears to be lost when constructing the admin Query, even though the callback fires.

Expected gold outcome by source inspection: the new public `sample.timestamp_stack().expect(...)` assertion fails. This is a prediction, not an execution result. It should be investigated as a reference implementation/specification inconsistency; the test must not be narrowed merely to preserve a passing reference.

The smallest potential reference correction is to populate the admin `QueryInner.query_ts_stack` from the incoming extension after the existing Receive append, using the same fallible conversion as normal session query delivery:

```rust
#[cfg(feature = "unstable")]
query_ts_stack: msg.ext_ts_stack.as_ref().and_then(|ts| {
    crate::api::timestamp_stack::TimestampStack::try_from(&ts.ts_stack).ok()
}),
```

The admin Query already stores its runtime weak handle, so the existing reply code can append an enabled Send record. This proposed field-initialization change was not applied. Any correction needs a separate reference version and validation; it does not justify rewriting historical rewards or task files.

The saved Sonnet/max pass `gSYDMEG` takes the stronger path already: its admin handler appends Receive, converts `msg.ext_ts_stack` with `from_wire_opt`, and assigns it to `QueryInner.timestamp_stack` (`adminspace.rs`, lines 516–536). Its `Query::reply_ts_stack` copies that stack into the reply and appends Send only when configured (`api/queryable.rs`, lines 595–612). This source supports the new test's feasibility; it is not a claim that the saved submission has passed the new test.

## Evidence identities

- v3 `instruction.md`: `31ea9c624ba8ab89828aaa90c7e4a63efa945c65ad5fe64e6e6ccb26668fb85b`.
- v3 `solution/changes.patch`: `6e7b6e78d8c194ed9ece8a87a715878e1fb466e8823eb9f0a723e2280e49af0d`.
- v3 `tests/image-source/gold.patch`: `0b54739d6bd3a421a10fdbccaedcc22e560143048c5e673a17f05aa7308da7d9`.
- v3 pristine source archive: `3e4d52aa66d566a42426b8177c01eaab827421a9485f83d8f12493cb88073a2e`.
- Saved trial root: `jobs/candidates-zenoh-v3-efforts-20-20260913T221317Z/rs-zenoh-timestamp-instrumentati__gSYDMEG/`.
- Its unchanged `result.json`: `0033ab0093b784d675cba94cbcd0e472d07a84ebe5a9d35e80fe9d5736fc9423`; existing reward 1, Sonnet/max, finished `2026-09-14T04:49:45.535059Z`.
- Saved `artifacts/submission/zenoh/src/net/runtime/adminspace.rs`: `ac1f2c774c3a5f89396e7d1018b297f36a043bfdcbc74eaf9a8bf4e3918fca29`.
- Saved `artifacts/submission/zenoh/src/api/queryable.rs`: `a13fc66f70c0f4011144c77dbc3f4f99641c034d27a2b1757c5dca66d2207636`.
