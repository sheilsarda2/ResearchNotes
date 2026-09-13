//
// Hidden verifier test: differential interop for timestamp instrumentation.
//
// The session under test lives in this process and is built from the submitted
// sources. Its counterpart is an external reference peer whose command line is
// taken from the environment:
//   TS_PEER_BIN : reference peer built from the upstream gold tree   (tests `interop_*`)
//   TS_PY_PEER  : Python reference peer command, e.g.
//                 "/opt/zpy/bin/python3 /tests/py_peer.py"           (tests `pyparity_*`)
// Both peers speak the same JSON-lines protocol (see tests/ts_peer/src/main.rs).
//
#![cfg(feature = "unstable")]

use std::{
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc::{self, Receiver, RecvTimeoutError},
    time::{Duration, Instant},
};

use serde_json::Value;
use zenoh::{
    config::WhatAmI,
    sample::SampleKind,
    timestamp_stack::{
        InstrumentationTimestamp, InterceptionPoint, TimestampInstrumentation,
        TimestampInstrumentationBuilder, TimestampStack,
    },
    Config, Session,
};
use zenoh_config::{EndPoint, EndPoints, ModeDependentValue};
use zenoh_core::ztimeout;

const TIMEOUT: Duration = Duration::from_secs(60);
const SLEEP: Duration = Duration::from_secs(1);

#[derive(Clone, Copy)]
enum PeerKind {
    Gold,
    Python,
}

fn peer_cmdline(kind: PeerKind) -> Vec<String> {
    match kind {
        PeerKind::Gold => vec![std::env::var("TS_PEER_BIN")
            .expect("TS_PEER_BIN must point at the reference peer binary")],
        PeerKind::Python => std::env::var("TS_PY_PEER")
            .expect("TS_PY_PEER must hold the python peer command")
            .split_whitespace()
            .map(str::to_string)
            .collect(),
    }
}

/// An external reference peer process.
struct Peer {
    child: Child,
    stdin: Option<ChildStdin>,
    events: Receiver<String>,
    zid: String,
    locators: Vec<String>,
}

impl Peer {
    fn spawn(kind: PeerKind, args: &[&str]) -> Peer {
        let cmdline = peer_cmdline(kind);
        let mut cmd = Command::new(&cmdline[0]);
        cmd.args(&cmdline[1..])
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        let mut child = cmd
            .spawn()
            .unwrap_or_else(|e| panic!("cannot spawn reference peer {cmdline:?}: {e}"));
        let stdout = child.stdout.take().unwrap();
        let stdin = child.stdin.take();
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                match line {
                    Ok(l) => {
                        if tx.send(l).is_err() {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
        });
        let mut peer = Peer {
            child,
            stdin,
            events: rx,
            zid: String::new(),
            locators: vec![],
        };
        let ready = peer.next_event("ready");
        peer.zid = ready["zid"].as_str().expect("ready.zid").to_string();
        peer.locators = ready["locators"]
            .as_array()
            .expect("ready.locators")
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect();
        peer
    }

    fn next_event(&mut self, name: &str) -> Value {
        let deadline = Instant::now() + TIMEOUT;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            match self.events.recv_timeout(remaining) {
                Ok(line) => {
                    let Ok(v) = serde_json::from_str::<Value>(&line) else {
                        continue;
                    };
                    let ev = v["event"].as_str().unwrap_or("");
                    if ev == name {
                        return v;
                    }
                    if ev == "timeout" {
                        panic!("reference peer timed out while we waited for '{name}'");
                    }
                }
                Err(RecvTimeoutError::Timeout) => {
                    panic!("timed out waiting for reference peer event '{name}'")
                }
                Err(RecvTimeoutError::Disconnected) => {
                    panic!("reference peer exited before emitting '{name}'")
                }
            }
        }
    }

    fn go(&mut self) {
        let stdin = self.stdin.as_mut().expect("peer stdin");
        stdin.write_all(b"go\n").unwrap();
        stdin.flush().unwrap();
    }

    fn finish(mut self) {
        drop(self.stdin.take());
        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            match self.child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(50))
                }
                _ => {
                    let _ = self.child.kill();
                    let _ = self.child.wait();
                    return;
                }
            }
        }
    }
}

impl Drop for Peer {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

// ─── Session helpers (submission side) ─────────────────────────────────

fn base_config(mode: WhatAmI) -> Config {
    let mut config = Config::default();
    config.scouting.multicast.set_enabled(Some(false)).unwrap();
    config.set_mode(Some(mode)).unwrap();
    config
}

fn listen_config(mode: WhatAmI, endpoint: &str) -> Config {
    let mut config = base_config(mode);
    config
        .listen
        .set_endpoints(ModeDependentValue::Unique(vec![endpoint
            .parse::<EndPoint>()
            .unwrap()]))
        .unwrap();
    config
}

fn connect_config(mode: WhatAmI, endpoint: &str) -> Config {
    let mut config = base_config(mode);
    let endpoints: Vec<EndPoints> = vec![endpoint.parse::<EndPoint>().unwrap().into()];
    config
        .connect
        .set_endpoints(ModeDependentValue::Unique(endpoints))
        .unwrap();
    config
}

fn free_endpoint() -> String {
    format!("tcp/127.0.0.1:{}", zenoh_test::get_free_tcp_port())
}

async fn tcp_locator(session: &Session) -> String {
    session
        .info()
        .locators()
        .await
        .iter()
        .map(|l| l.to_string())
        .find(|s| s.starts_with("tcp/"))
        .expect("session has a tcp locator")
}

fn instr(send: bool, route: bool, receive: bool) -> TimestampInstrumentation {
    TimestampInstrumentationBuilder::new()
        .set_send(send)
        .set_route(route)
        .set_receive(receive)
        .build()
        .unwrap()
}

// ─── Assertions on peer-reported stacks (JSON) ─────────────────────────

fn records(stack: &Value) -> Vec<Value> {
    stack["records"]
        .as_array()
        .unwrap_or_else(|| panic!("stack without records: {stack}"))
        .clone()
}

fn assert_instr(stack: &Value, send: bool, route: bool, receive: bool) {
    assert_eq!(stack["instr"]["send"].as_bool(), Some(send), "{stack}");
    assert_eq!(stack["instr"]["route"].as_bool(), Some(route), "{stack}");
    assert_eq!(stack["instr"]["receive"].as_bool(), Some(receive), "{stack}");
}

/// A UHLC record at `point` whose timestamp id is the ZenohId of the node that recorded it.
fn assert_uhlc_record(r: &Value, point: &str, zid: &str) {
    assert_eq!(r["point"].as_str(), Some(point), "record {r}");
    assert_eq!(r["custom"].as_bool(), Some(false), "record {r}");
    let ts = r["ts"]
        .as_str()
        .unwrap_or_else(|| panic!("UHLC record without ts: {r}"));
    let (time, id) = ts
        .split_once('/')
        .unwrap_or_else(|| panic!("timestamp text is not <time>/<id>: {ts}"));
    assert!(time.parse::<u64>().is_ok(), "bad NTP64 time in {ts}");
    assert_eq!(id, zid, "timestamp id must be the recording node's zid: {r}");
}

fn assert_custom_record(r: &Value, point: &str, hex: &str) {
    assert_eq!(r["point"].as_str(), Some(point), "record {r}");
    assert_eq!(r["custom"].as_bool(), Some(true), "record {r}");
    assert_eq!(r["bytes"].as_str(), Some(hex), "record {r}");
}

fn assert_uhlc_chain(stack: &Value, expected: &[(&str, &str)]) {
    let recs = records(stack);
    assert_eq!(
        recs.len(),
        expected.len(),
        "unexpected record count in {stack}"
    );
    for (r, (point, zid)) in recs.iter().zip(expected) {
        assert_uhlc_record(r, point, zid);
    }
}

// ─── Assertions on locally received stacks ─────────────────────────────

fn local_points(stack: &TimestampStack) -> Vec<InterceptionPoint> {
    stack.records().iter().map(|r| r.point()).collect()
}

fn local_uhlc_id(stack: &TimestampStack, i: usize) -> String {
    let record = &stack.records()[i];
    assert!(!record.is_custom(), "record {i} must be UHLC");
    match record.timestamp() {
        InstrumentationTimestamp::UHLC(ts) => ts.get_id().to_string(),
        InstrumentationTimestamp::Custom(_) => panic!("record {i} must be UHLC"),
    }
}

fn assert_local_chain(stack: &TimestampStack, expected: &[(InterceptionPoint, &str)]) {
    let points: Vec<InterceptionPoint> = expected.iter().map(|(p, _)| *p).collect();
    assert_eq!(local_points(stack), points);
    for (i, (_, zid)) in expected.iter().enumerate() {
        assert_eq!(local_uhlc_id(stack, i), *zid, "record {i} id");
    }
}

// ─── Scenarios ─────────────────────────────────────────────────────────

/// Reference subscriber decodes what the submission publishes.
async fn scenario_peer_sub_decodes_agent_send(kind: PeerKind, ke: &str) {
    zenoh_util::init_log_from_env_or("error");
    let ep = free_endpoint();
    let mut peer = Peer::spawn(
        kind,
        &[
            "--role", "sub", "--mode", "peer", "--listen", &ep, "--ke", ke, "--count", "4",
        ],
    );
    let session = ztimeout!(zenoh::open(connect_config(WhatAmI::Peer, &ep))).unwrap();
    let zid = session.zid().to_string();
    let publisher = ztimeout!(session.declare_publisher(ke)).unwrap();
    tokio::time::sleep(SLEEP).await;

    // SEND + RECEIVE across one hop.
    ztimeout!(publisher
        .put("one")
        .timestamp_instrumentation(instr(true, false, true)))
    .unwrap();
    let s1 = peer.next_event("sample");
    assert_eq!(s1["kind"], "Put");
    assert_eq!(s1["payload"], "one");
    assert_instr(&s1["stack"], true, false, true);
    assert_uhlc_chain(&s1["stack"], &[("send", &zid), ("receive", &peer.zid)]);

    // All points: our routing layer and the peer's routing layer each add one ROUTE.
    ztimeout!(publisher
        .put("two")
        .timestamp_instrumentation(instr(true, true, true)))
    .unwrap();
    let s2 = peer.next_event("sample");
    assert_instr(&s2["stack"], true, true, true);
    assert_uhlc_chain(
        &s2["stack"],
        &[
            ("send", &zid),
            ("route", &zid),
            ("route", &peer.zid),
            ("receive", &peer.zid),
        ],
    );

    // DELETE with ROUTE only.
    ztimeout!(publisher
        .delete()
        .timestamp_instrumentation(instr(false, true, false)))
    .unwrap();
    let s3 = peer.next_event("sample");
    assert_eq!(s3["kind"], "Delete");
    assert_instr(&s3["stack"], false, true, false);
    assert_uhlc_chain(&s3["stack"], &[("route", &zid), ("route", &peer.zid)]);

    // No instrumentation: no extension on the wire, no stack at the peer.
    ztimeout!(publisher.put("four")).unwrap();
    let s4 = peer.next_event("sample");
    assert!(s4["stack"].is_null(), "no instrumentation must yield no stack: {s4}");

    peer.finish();
    ztimeout!(session.close()).unwrap();
}

/// The submission decodes what the reference peer publishes (UHLC and custom records).
async fn scenario_agent_sub_decodes_peer_send(kind: PeerKind, ke: &str) {
    zenoh_util::init_log_from_env_or("error");
    let session = ztimeout!(zenoh::open(listen_config(WhatAmI::Peer, "tcp/127.0.0.1:0"))).unwrap();
    let zid = session.zid().to_string();
    let loc = tcp_locator(&session).await;
    let subscriber = ztimeout!(session.declare_subscriber(ke)).unwrap();

    // 1) Fully instrumented publication from the reference peer.
    let mut p1 = Peer::spawn(
        kind,
        &[
            "--role", "pub", "--mode", "peer", "--connect", &loc, "--ke", ke, "--send",
            "--route", "--receive", "--payload", "hello", "--count", "1",
        ],
    );
    tokio::time::sleep(SLEEP).await;
    p1.go();
    p1.next_event("put");
    let sample = ztimeout!(subscriber.recv_async()).unwrap();
    assert_eq!(sample.kind(), SampleKind::Put);
    assert_eq!(sample.payload().try_to_string().unwrap(), "hello");
    let stack = sample.timestamp_stack().expect("stack from reference peer");
    let cfg = stack.instrumentation();
    assert!(cfg.is_instrumented(InterceptionPoint::Send));
    assert!(cfg.is_instrumented(InterceptionPoint::Route));
    assert!(cfg.is_instrumented(InterceptionPoint::Receive));
    assert_local_chain(
        stack,
        &[
            (InterceptionPoint::Send, &p1.zid),
            (InterceptionPoint::Route, &p1.zid),
            (InterceptionPoint::Route, &zid),
            (InterceptionPoint::Receive, &zid),
        ],
    );
    p1.finish();

    // 2) Reference peer with a custom timestamp callback: its records are custom bytes,
    //    ours stay UHLC.
    let mut p2 = Peer::spawn(
        kind,
        &[
            "--role", "pub", "--mode", "peer", "--connect", &loc, "--ke", ke, "--send",
            "--receive", "--callback", "deadbeef", "--payload", "custom", "--count", "1",
        ],
    );
    tokio::time::sleep(SLEEP).await;
    p2.go();
    p2.next_event("put");
    let sample = ztimeout!(subscriber.recv_async()).unwrap();
    assert_eq!(sample.payload().try_to_string().unwrap(), "custom");
    let stack = sample.timestamp_stack().expect("stack with custom record");
    assert_eq!(
        local_points(stack),
        vec![InterceptionPoint::Send, InterceptionPoint::Receive]
    );
    assert!(stack.records()[0].is_custom());
    assert_eq!(
        stack.records()[0].timestamp(),
        &InstrumentationTimestamp::Custom(vec![0xde, 0xad, 0xbe, 0xef])
    );
    assert_eq!(local_uhlc_id(stack, 1), zid);
    p2.finish();

    // 3) DELETE without instrumentation.
    let mut p3 = Peer::spawn(
        kind,
        &[
            "--role", "pub", "--mode", "peer", "--connect", &loc, "--ke", ke, "--payload",
            "DELETE", "--count", "1",
        ],
    );
    tokio::time::sleep(SLEEP).await;
    p3.go();
    p3.next_event("put");
    let sample = ztimeout!(subscriber.recv_async()).unwrap();
    assert_eq!(sample.kind(), SampleKind::Delete);
    assert!(sample.timestamp_stack().is_none());
    p3.finish();

    ztimeout!(session.close()).unwrap();
}

/// Reference router in the middle, submission clients on both ends (pub/sub and query/reply).
async fn scenario_peer_router_between_agent_clients(kind: PeerKind, ke: &str) {
    zenoh_util::init_log_from_env_or("error");
    let ep = free_endpoint();
    let router = Peer::spawn(kind, &["--role", "router", "--mode", "router", "--listen", &ep]);
    let client1 = ztimeout!(zenoh::open(connect_config(WhatAmI::Client, &ep))).unwrap();
    let client2 = ztimeout!(zenoh::open(connect_config(WhatAmI::Client, &ep))).unwrap();
    let (z1, z2, zr) = (
        client1.zid().to_string(),
        client2.zid().to_string(),
        router.zid.clone(),
    );
    tokio::time::sleep(SLEEP).await;

    // pub/sub
    let publisher = ztimeout!(client1.declare_publisher(ke)).unwrap();
    let subscriber = ztimeout!(client2.declare_subscriber(ke)).unwrap();
    tokio::time::sleep(SLEEP).await;
    ztimeout!(publisher
        .put("payload")
        .timestamp_instrumentation(instr(true, true, true)))
    .unwrap();
    let sample = ztimeout!(subscriber.recv_async()).unwrap();
    let stack = sample.timestamp_stack().expect("routed stack");
    assert_local_chain(
        stack,
        &[
            (InterceptionPoint::Send, &z1),
            (InterceptionPoint::Route, &z1),
            (InterceptionPoint::Route, &zr),
            (InterceptionPoint::Route, &z2),
            (InterceptionPoint::Receive, &z2),
        ],
    );

    // query/reply
    let queryable = ztimeout!(client2.declare_queryable(ke)).unwrap();
    tokio::time::sleep(SLEEP).await;
    let replies = ztimeout!(client1
        .get(ke)
        .timestamp_instrumentation(instr(true, true, true)))
    .unwrap();
    let query = ztimeout!(queryable.recv_async()).unwrap();
    let qstack = query.timestamp_stack().expect("routed query stack");
    assert_local_chain(
        qstack,
        &[
            (InterceptionPoint::Send, &z1),
            (InterceptionPoint::Route, &z1),
            (InterceptionPoint::Route, &zr),
            (InterceptionPoint::Route, &z2),
            (InterceptionPoint::Receive, &z2),
        ],
    );
    ztimeout!(query.reply(ke, "data")).unwrap();
    drop(query);
    let reply = ztimeout!(replies.recv_async()).unwrap();
    let rstack = reply
        .result()
        .expect("ok reply")
        .timestamp_stack()
        .expect("routed reply stack");
    assert_local_chain(
        rstack,
        &[
            (InterceptionPoint::Send, &z1),
            (InterceptionPoint::Route, &z1),
            (InterceptionPoint::Route, &zr),
            (InterceptionPoint::Route, &z2),
            (InterceptionPoint::Receive, &z2),
            (InterceptionPoint::Send, &z2),
            (InterceptionPoint::Route, &z2),
            (InterceptionPoint::Route, &zr),
            (InterceptionPoint::Route, &z1),
            (InterceptionPoint::Receive, &z1),
        ],
    );

    ztimeout!(client1.close()).unwrap();
    ztimeout!(client2.close()).unwrap();
    router.finish();
}

/// Submission router in the middle, reference clients on both ends.
async fn scenario_agent_router_between_peer_clients(kind: PeerKind, ke: &str) {
    zenoh_util::init_log_from_env_or("error");
    let router = ztimeout!(zenoh::open(listen_config(WhatAmI::Router, "tcp/127.0.0.1:0"))).unwrap();
    let zr = router.zid().to_string();
    let loc = tcp_locator(&router).await;

    // pub/sub
    let mut sub = Peer::spawn(
        kind,
        &[
            "--role", "sub", "--mode", "client", "--connect", &loc, "--ke", ke, "--count", "1",
        ],
    );
    let mut publ = Peer::spawn(
        kind,
        &[
            "--role", "pub", "--mode", "client", "--connect", &loc, "--ke", ke, "--send",
            "--route", "--receive", "--count", "1", "--payload", "routed",
        ],
    );
    tokio::time::sleep(SLEEP).await;
    publ.go();
    publ.next_event("put");
    let s = sub.next_event("sample");
    assert_eq!(s["payload"], "routed");
    assert_uhlc_chain(
        &s["stack"],
        &[
            ("send", &publ.zid),
            ("route", &publ.zid),
            ("route", &zr),
            ("route", &sub.zid),
            ("receive", &sub.zid),
        ],
    );
    publ.finish();
    sub.finish();

    // query/reply
    let mut qable = Peer::spawn(
        kind,
        &[
            "--role", "queryable", "--mode", "client", "--connect", &loc, "--ke", ke, "--count",
            "1", "--payload", "data",
        ],
    );
    let mut getter = Peer::spawn(
        kind,
        &[
            "--role", "get", "--mode", "client", "--connect", &loc, "--ke", ke, "--send",
            "--route", "--receive",
        ],
    );
    tokio::time::sleep(SLEEP).await;
    getter.go();
    let q = qable.next_event("query");
    assert_uhlc_chain(
        &q["stack"],
        &[
            ("send", &getter.zid),
            ("route", &getter.zid),
            ("route", &zr),
            ("route", &qable.zid),
            ("receive", &qable.zid),
        ],
    );
    let rep = getter.next_event("reply");
    assert_eq!(rep["ok"], true);
    assert_uhlc_chain(
        &rep["stack"],
        &[
            ("send", &getter.zid),
            ("route", &getter.zid),
            ("route", &zr),
            ("route", &qable.zid),
            ("receive", &qable.zid),
            ("send", &qable.zid),
            ("route", &qable.zid),
            ("route", &zr),
            ("route", &getter.zid),
            ("receive", &getter.zid),
        ],
    );
    getter.next_event("replies_done");
    qable.finish();
    getter.finish();
    ztimeout!(router.close()).unwrap();
}

/// Reference queryable answers the submission's queries (ok replies, no instrumentation, reply_err).
async fn scenario_peer_queryable_agent_get(kind: PeerKind, ke: &str) {
    zenoh_util::init_log_from_env_or("error");
    let ep = free_endpoint();
    let mut qable = Peer::spawn(
        kind,
        &[
            "--role", "queryable", "--mode", "peer", "--listen", &ep, "--ke", ke, "--count", "2",
            "--payload", "data",
        ],
    );
    let session = ztimeout!(zenoh::open(connect_config(WhatAmI::Peer, &ep))).unwrap();
    let zid = session.zid().to_string();
    tokio::time::sleep(SLEEP).await;

    let replies = ztimeout!(session
        .get(ke)
        .timestamp_instrumentation(instr(true, false, true)))
    .unwrap();
    let q = qable.next_event("query");
    assert_instr(&q["stack"], true, false, true);
    assert_uhlc_chain(&q["stack"], &[("send", &zid), ("receive", &qable.zid)]);
    let reply = ztimeout!(replies.recv_async()).unwrap();
    let sample = reply.result().expect("ok reply");
    assert_eq!(sample.payload().try_to_string().unwrap(), "data");
    let stack = sample.timestamp_stack().expect("reply stack");
    assert!(stack.instrumentation().is_instrumented(InterceptionPoint::Send));
    assert!(!stack.instrumentation().is_instrumented(InterceptionPoint::Route));
    assert_local_chain(
        stack,
        &[
            (InterceptionPoint::Send, &zid),
            (InterceptionPoint::Receive, &qable.zid),
            (InterceptionPoint::Send, &qable.zid),
            (InterceptionPoint::Receive, &zid),
        ],
    );

    // No instrumentation on the query: nothing on the wire, nothing on the reply.
    let replies = ztimeout!(session.get(ke)).unwrap();
    let q = qable.next_event("query");
    assert!(q["stack"].is_null(), "{q}");
    let reply = ztimeout!(replies.recv_async()).unwrap();
    assert!(reply.result().expect("ok reply").timestamp_stack().is_none());
    qable.finish();
    ztimeout!(session.close()).unwrap();

    // reply_err carries the stack too.
    let ke_err = format!("{ke}/err");
    let ep2 = free_endpoint();
    let mut qerr = Peer::spawn(
        kind,
        &[
            "--role", "queryable", "--mode", "peer", "--listen", &ep2, "--ke", &ke_err,
            "--count", "1", "--payload", "ERR",
        ],
    );
    let session2 = ztimeout!(zenoh::open(connect_config(WhatAmI::Peer, &ep2))).unwrap();
    let zid2 = session2.zid().to_string();
    tokio::time::sleep(SLEEP).await;
    let replies = ztimeout!(session2
        .get(&ke_err)
        .timestamp_instrumentation(instr(true, false, true)))
    .unwrap();
    let q = qerr.next_event("query");
    assert_uhlc_chain(&q["stack"], &[("send", &zid2), ("receive", &qerr.zid)]);
    let reply = ztimeout!(replies.recv_async()).unwrap();
    let err = reply.result().err().expect("error reply");
    let stack = err.timestamp_stack().expect("reply_err stack");
    assert_local_chain(
        stack,
        &[
            (InterceptionPoint::Send, &zid2),
            (InterceptionPoint::Receive, &qerr.zid),
            (InterceptionPoint::Send, &qerr.zid),
            (InterceptionPoint::Receive, &zid2),
        ],
    );
    qerr.finish();
    ztimeout!(session2.close()).unwrap();
}

/// Submission queryable answers the reference peer's query.
async fn scenario_agent_queryable_peer_get(kind: PeerKind, ke: &str) {
    zenoh_util::init_log_from_env_or("error");
    let session = ztimeout!(zenoh::open(listen_config(WhatAmI::Peer, "tcp/127.0.0.1:0"))).unwrap();
    let zid = session.zid().to_string();
    let loc = tcp_locator(&session).await;
    let queryable = ztimeout!(session.declare_queryable(ke)).unwrap();
    let mut getter = Peer::spawn(
        kind,
        &[
            "--role", "get", "--mode", "peer", "--connect", &loc, "--ke", ke, "--send",
            "--receive",
        ],
    );
    tokio::time::sleep(SLEEP).await;
    getter.go();
    let query = ztimeout!(queryable.recv_async()).unwrap();
    let qstack = query.timestamp_stack().expect("query stack from reference peer");
    assert!(qstack.instrumentation().is_instrumented(InterceptionPoint::Send));
    assert!(qstack.instrumentation().is_instrumented(InterceptionPoint::Receive));
    assert!(!qstack.instrumentation().is_instrumented(InterceptionPoint::Route));
    assert_local_chain(
        qstack,
        &[
            (InterceptionPoint::Send, &getter.zid),
            (InterceptionPoint::Receive, &zid),
        ],
    );
    ztimeout!(query.reply(ke, "data")).unwrap();
    drop(query);
    let rep = getter.next_event("reply");
    assert_eq!(rep["ok"], true);
    assert_instr(&rep["stack"], true, false, true);
    assert_uhlc_chain(
        &rep["stack"],
        &[
            ("send", &getter.zid),
            ("receive", &zid),
            ("send", &zid),
            ("receive", &getter.zid),
        ],
    );
    getter.next_event("replies_done");
    getter.finish();
    ztimeout!(session.close()).unwrap();
}

// ─── interop_*: reference peer built from the upstream gold tree ────────

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn interop_gold_sub_decodes_agent_send() {
    scenario_peer_sub_decodes_agent_send(PeerKind::Gold, "test/ts_interop/gold/sub").await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn interop_agent_sub_decodes_gold_send() {
    scenario_agent_sub_decodes_peer_send(PeerKind::Gold, "test/ts_interop/gold/pub").await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn interop_gold_router_between_agent_clients() {
    scenario_peer_router_between_agent_clients(PeerKind::Gold, "test/ts_interop/gold/router")
        .await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn interop_agent_router_between_gold_clients() {
    scenario_agent_router_between_peer_clients(PeerKind::Gold, "test/ts_interop/gold/clients")
        .await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn interop_gold_queryable_agent_get() {
    scenario_peer_queryable_agent_get(PeerKind::Gold, "test/ts_interop/gold/queryable").await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn interop_agent_queryable_gold_get() {
    scenario_agent_queryable_peer_get(PeerKind::Gold, "test/ts_interop/gold/get").await;
}

// ─── pyparity_*: zenoh-python bindings built against the gold tree ─────

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn pyparity_python_sub_decodes_agent_send() {
    scenario_peer_sub_decodes_agent_send(PeerKind::Python, "test/ts_interop/py/sub").await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn pyparity_agent_sub_decodes_python_send() {
    scenario_agent_sub_decodes_peer_send(PeerKind::Python, "test/ts_interop/py/pub").await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn pyparity_python_queryable_agent_get() {
    scenario_peer_queryable_agent_get(PeerKind::Python, "test/ts_interop/py/queryable").await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn pyparity_agent_queryable_python_get() {
    scenario_agent_queryable_peer_get(PeerKind::Python, "test/ts_interop/py/get").await;
}
