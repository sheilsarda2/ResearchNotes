//! Reference peer for the timestamp-instrumentation interop check.
//!
//! One process plays one role (sub, pub, queryable, get, router) using only the
//! public `zenoh` API of the gold tree and reports what it observes as JSON lines
//! on stdout. The Python peer (`py_peer.py`) implements the same CLI and output.
//!
//! Events:
//!   {"event":"ready","zid":"<hex>","locators":["tcp/..."]}
//!   {"event":"sample","kind":"Put|Delete","payload":"...","stack":<stack|null>}
//!   {"event":"query","stack":<stack|null>}
//!   {"event":"reply","ok":true|false,"stack":<stack|null>}
//!   {"event":"put","i":N}   {"event":"replies_done"}   {"event":"timeout"}
//! where <stack> = {"instr":{"send":b,"route":b,"receive":b},
//!                  "records":[{"point":"send|route|receive","custom":b,"ts":"<time>/<id>"}
//!                             | {"point":..,"custom":true,"bytes":"<hex>"}, ...]}
use std::{
    io::{BufRead, Write},
    time::Duration,
};

use zenoh::{
    config::WhatAmI,
    query::ConsolidationMode,
    timestamp_stack::{
        InstrumentationTimestamp, InterceptionPoint, TimestampContext, TimestampInstrumentation,
        TimestampInstrumentationBuilder, TimestampStack, TimestampStackRecord,
    },
    Config, Session,
};

struct Args {
    role: String,
    listen: Vec<String>,
    connect: Vec<String>,
    mode: String,
    ke: String,
    send: bool,
    route: bool,
    receive: bool,
    callback: Option<Vec<u8>>,
    count: usize,
    payload: String,
    timeout_secs: u64,
}

fn parse_args() -> Args {
    let mut a = Args {
        role: String::new(),
        listen: vec![],
        connect: vec![],
        mode: "peer".to_string(),
        ke: "ts/interop".to_string(),
        send: false,
        route: false,
        receive: false,
        callback: None,
        count: 1,
        payload: "payload".to_string(),
        timeout_secs: 60,
    };
    let mut it = std::env::args().skip(1);
    while let Some(k) = it.next() {
        let mut val = || it.next().unwrap_or_else(|| panic!("missing value for {k}"));
        match k.as_str() {
            "--role" => a.role = val(),
            "--listen" => a.listen.push(val()),
            "--connect" => a.connect.push(val()),
            "--mode" => a.mode = val(),
            "--ke" => a.ke = val(),
            "--send" => a.send = true,
            "--route" => a.route = true,
            "--receive" => a.receive = true,
            "--callback" => a.callback = Some(hex_decode(&val())),
            "--count" => a.count = val().parse().expect("--count"),
            "--payload" => a.payload = val(),
            "--timeout" => a.timeout_secs = val().parse().expect("--timeout"),
            other => panic!("unknown argument {other}"),
        }
    }
    a
}

fn hex_decode(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).expect("hex"))
        .collect()
}

fn hex_encode(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn json_str(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn emit(line: String) {
    let mut out = std::io::stdout().lock();
    out.write_all(line.as_bytes()).unwrap();
    out.write_all(b"\n").unwrap();
    out.flush().unwrap();
}

fn instrumentation(a: &Args) -> Option<TimestampInstrumentation> {
    if !(a.send || a.route || a.receive) {
        return None;
    }
    Some(
        TimestampInstrumentationBuilder::new()
            .set_send(a.send)
            .set_route(a.route)
            .set_receive(a.receive)
            .build()
            .expect("instrumentation"),
    )
}

fn record_json(r: &TimestampStackRecord) -> String {
    let point = match r.point() {
        InterceptionPoint::Send => "send",
        InterceptionPoint::Route => "route",
        InterceptionPoint::Receive => "receive",
    };
    match r.timestamp() {
        InstrumentationTimestamp::UHLC(ts) => format!(
            "{{\"point\":\"{}\",\"custom\":{},\"ts\":{}}}",
            point,
            r.is_custom(),
            json_str(&ts.to_string())
        ),
        InstrumentationTimestamp::Custom(bytes) => format!(
            "{{\"point\":\"{}\",\"custom\":{},\"bytes\":\"{}\"}}",
            point,
            r.is_custom(),
            hex_encode(bytes)
        ),
    }
}

fn stack_json(stack: Option<&TimestampStack>) -> String {
    match stack {
        None => "null".to_string(),
        Some(s) => {
            let i = s.instrumentation();
            let records: Vec<String> = s.records().iter().map(record_json).collect();
            format!(
                "{{\"instr\":{{\"send\":{},\"route\":{},\"receive\":{}}},\"records\":[{}]}}",
                i.is_instrumented(InterceptionPoint::Send),
                i.is_instrumented(InterceptionPoint::Route),
                i.is_instrumented(InterceptionPoint::Receive),
                records.join(",")
            )
        }
    }
}

fn build_config(a: &Args) -> Config {
    let mut config = Config::default();
    config.scouting.multicast.set_enabled(Some(false)).unwrap();
    let mode = match a.mode.as_str() {
        "peer" => WhatAmI::Peer,
        "client" => WhatAmI::Client,
        "router" => WhatAmI::Router,
        m => panic!("unknown mode {m}"),
    };
    config.set_mode(Some(mode)).unwrap();
    if !a.listen.is_empty() {
        let eps: Vec<zenoh_config::EndPoint> =
            a.listen.iter().map(|s| s.parse().expect("listen endpoint")).collect();
        config
            .listen
            .set_endpoints(zenoh_config::ModeDependentValue::Unique(eps))
            .unwrap();
    }
    if !a.connect.is_empty() {
        let eps: Vec<zenoh_config::EndPoints> = a
            .connect
            .iter()
            .map(|s| {
                s.parse::<zenoh_config::EndPoint>()
                    .expect("connect endpoint")
                    .into()
            })
            .collect();
        config
            .connect
            .set_endpoints(zenoh_config::ModeDependentValue::Unique(eps))
            .unwrap();
    }
    config
}

async fn open(a: &Args) -> Session {
    let config = build_config(a);
    match a.callback.clone() {
        Some(bytes) => zenoh::open(config)
            .with_timestamp_callback(move |_ctx: TimestampContext| bytes.clone())
            .await
            .expect("open with callback"),
        None => zenoh::open(config).await.expect("open"),
    }
}

async fn emit_ready(session: &Session) {
    let locators: Vec<String> = session
        .info()
        .locators()
        .await
        .into_iter()
        .map(|l| json_str(&l.to_string()))
        .collect();
    emit(format!(
        "{{\"event\":\"ready\",\"zid\":\"{}\",\"locators\":[{}]}}",
        session.zid(),
        locators.join(",")
    ));
}

async fn wait_go() {
    tokio::task::spawn_blocking(|| {
        let stdin = std::io::stdin();
        for line in stdin.lock().lines() {
            let line = line.unwrap_or_default();
            if line.trim() == "go" {
                return;
            }
        }
        // stdin closed without "go": proceed anyway so the test can observe output.
    })
    .await
    .unwrap();
}

fn timeout_exit() -> ! {
    emit("{\"event\":\"timeout\"}".to_string());
    std::process::exit(3)
}

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    let a = parse_args();
    let timeout = Duration::from_secs(a.timeout_secs);
    let session = open(&a).await;
    match a.role.as_str() {
        "sub" => {
            let sub = session.declare_subscriber(a.ke.as_str()).await.unwrap();
            emit_ready(&session).await;
            for _ in 0..a.count {
                match tokio::time::timeout(timeout, sub.recv_async()).await {
                    Ok(Ok(sample)) => {
                        let payload = sample
                            .payload()
                            .try_to_string()
                            .map(|c| c.to_string())
                            .unwrap_or_default();
                        emit(format!(
                            "{{\"event\":\"sample\",\"kind\":\"{:?}\",\"payload\":{},\"stack\":{}}}",
                            sample.kind(),
                            json_str(&payload),
                            stack_json(sample.timestamp_stack())
                        ));
                    }
                    _ => timeout_exit(),
                }
            }
        }
        "pub" => {
            let publisher = session.declare_publisher(a.ke.as_str()).await.unwrap();
            emit_ready(&session).await;
            wait_go().await;
            for i in 0..a.count {
                let instr = instrumentation(&a);
                if a.payload == "DELETE" {
                    publisher
                        .delete()
                        .timestamp_instrumentation(instr)
                        .await
                        .unwrap();
                } else {
                    publisher
                        .put(a.payload.clone())
                        .timestamp_instrumentation(instr)
                        .await
                        .unwrap();
                }
                emit(format!("{{\"event\":\"put\",\"i\":{i}}}"));
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        }
        "queryable" => {
            let queryable = session.declare_queryable(a.ke.as_str()).await.unwrap();
            emit_ready(&session).await;
            for _ in 0..a.count {
                match tokio::time::timeout(timeout, queryable.recv_async()).await {
                    Ok(Ok(query)) => {
                        emit(format!(
                            "{{\"event\":\"query\",\"stack\":{}}}",
                            stack_json(query.timestamp_stack())
                        ));
                        if a.payload == "ERR" {
                            query.reply_err("error payload").await.unwrap();
                        } else {
                            query.reply(a.ke.as_str(), a.payload.clone()).await.unwrap();
                        }
                        drop(query);
                    }
                    _ => timeout_exit(),
                }
            }
            // Give the reply time to leave before closing.
            tokio::time::sleep(Duration::from_millis(300)).await;
        }
        "get" => {
            emit_ready(&session).await;
            wait_go().await;
            let instr = instrumentation(&a);
            let replies = session
                .get(a.ke.as_str())
                .consolidation(ConsolidationMode::None)
                .timestamp_instrumentation(instr)
                .await
                .unwrap();
            loop {
                match tokio::time::timeout(timeout, replies.recv_async()).await {
                    Ok(Ok(reply)) => match reply.result() {
                        Ok(sample) => emit(format!(
                            "{{\"event\":\"reply\",\"ok\":true,\"stack\":{}}}",
                            stack_json(sample.timestamp_stack())
                        )),
                        Err(err) => emit(format!(
                            "{{\"event\":\"reply\",\"ok\":false,\"stack\":{}}}",
                            stack_json(err.timestamp_stack())
                        )),
                    },
                    Ok(Err(_)) => break,
                    Err(_) => timeout_exit(),
                }
            }
            emit("{\"event\":\"replies_done\"}".to_string());
        }
        "router" => {
            emit_ready(&session).await;
            // Stay up until the test closes our stdin.
            tokio::task::spawn_blocking(|| {
                let stdin = std::io::stdin();
                for _ in stdin.lock().lines() {}
            })
            .await
            .unwrap();
        }
        r => panic!("unknown role {r}"),
    }
    session.close().await.unwrap();
}
