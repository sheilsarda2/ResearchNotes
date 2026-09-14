//! Contract 6, exercised through the documented wire format and public interfaces.
//! No type or module introduced inside zenoh-protocol by a solution is imported.

#![cfg(feature = "unstable")]

use std::{sync::Arc, time::Duration};

use zenoh::{
    config::WhatAmI,
    timestamp_stack::{InstrumentationTimestamp, InterceptionPoint, TimestampInstrumentationBuilder},
};
use zenoh_buffers::{reader::{HasReader, Reader}, writer::HasWriter};
use zenoh_codec::{RCodec, WCodec, Zenoh080};
use zenoh_protocol::{
    core::WireExpr,
    network::{id, NetworkMessage, Push, Request, Response},
    zenoh::{PushBody, Put, Query, Reply, RequestBody, ResponseBody},
};
use zenoh_transport::{DummyTransportEventHandler, TransportManager};

const KEY: &str = "followup/timestamp/robustness";
const PAYLOAD: &[u8] = b"payload-survives-timestamp-decoding";
const RECEIVER_TIMESTAMP: &[u8] = b"recorded-at-subscriber";

#[derive(Clone, Copy, Debug)]
enum MessageKind { Push, Request, Response }

fn put_body() -> PushBody {
    PushBody::Put(Put { payload: PAYLOAD.to_vec().into(), ..Put::default() })
}

/// Each pair is precisely the instruction's (flags, length-prefixed timestamp).
fn stack_body(conf: u8, records: &[(u8, Vec<u8>)]) -> Vec<u8> {
    let mut bytes = Vec::new();
    let mut writer = bytes.writer();
    Zenoh080.write(&mut writer, conf).unwrap();
    Zenoh080.write(&mut writer, records.len()).unwrap();
    for (flags, timestamp) in records {
        Zenoh080.write(&mut writer, *flags).unwrap();
        Zenoh080.write(&mut writer, timestamp.as_slice()).unwrap();
    }
    bytes
}

/// Construct the old public network envelope and the normative optional id-7
/// ZBuf extension directly. The solution is free to choose its extension type.
fn message_bytes(kind: MessageKind, conf: u8, records: &[(u8, Vec<u8>)]) -> Vec<u8> {
    let mut bytes = Vec::new();
    let mut writer = bytes.writer();
    let message_id = match kind {
        MessageKind::Push => id::PUSH,
        MessageKind::Request => id::REQUEST,
        MessageKind::Response => id::RESPONSE,
    };
    // N: unscoped key suffix, M: sender mapping, Z: one extension.
    Zenoh080.write(&mut writer, message_id | 0b1110_0000u8).unwrap();
    if !matches!(kind, MessageKind::Push) {
        Zenoh080.write(&mut writer, 1u32).unwrap();
    }
    Zenoh080.write(&mut writer, &WireExpr::from(KEY)).unwrap();
    // ZBuf kind 0x40, non-mandatory id 7, final extension (no continuation bit).
    Zenoh080.write(&mut writer, 0x47u8).unwrap();
    Zenoh080.write(&mut writer, stack_body(conf, records).as_slice()).unwrap();
    match kind {
        MessageKind::Push => Zenoh080.write(&mut writer, &put_body()).unwrap(),
        MessageKind::Request => Zenoh080.write(&mut writer, &RequestBody::Query(Query {
            parameters: "probe=timestamp-robustness".into(), ..Query::default()
        })).unwrap(),
        MessageKind::Response => Zenoh080.write(&mut writer, &ResponseBody::Reply(Reply {
            consolidation: Default::default(), ext_unknown: Vec::new(), payload: put_body(),
        })).unwrap(),
    }
    bytes
}

fn decodes(kind: MessageKind, conf: u8, records: &[(u8, Vec<u8>)]) -> bool {
    let bytes = message_bytes(kind, conf, records);
    let mut reader = bytes.reader();
    let accepted = match kind {
        MessageKind::Push => {
            let result: Result<Push, _> = Zenoh080.read(&mut reader);
            result.map(|message| {
                assert_eq!(message.wire_expr, WireExpr::from(KEY));
                assert_eq!(message.payload, put_body());
            }).is_ok()
        }
        MessageKind::Request => {
            let result: Result<Request, _> = Zenoh080.read(&mut reader);
            result.map(|message| {
                assert_eq!(message.wire_expr, WireExpr::from(KEY));
                assert_eq!(message.payload, RequestBody::Query(Query {
                    parameters: "probe=timestamp-robustness".into(), ..Query::default()
                }));
            }).is_ok()
        }
        MessageKind::Response => {
            let result: Result<Response, _> = Zenoh080.read(&mut reader);
            result.map(|message| {
                assert_eq!(message.wire_expr, WireExpr::from(KEY));
                assert_eq!(message.payload, ResponseBody::Reply(Reply {
                    consolidation: Default::default(), ext_unknown: Vec::new(), payload: put_body(),
                }));
            }).is_ok()
        }
    };
    if accepted { assert!(!reader.can_read(), "decoder must consume the whole message"); }
    accepted
}

fn valid_records(count: usize) -> Vec<(u8, Vec<u8>)> {
    (0..count).map(|i| (0x84, format!("incoming-receive-{i}").into_bytes())).collect()
}

#[test]
fn decoder_accepts_valid_boundary_counts_without_changing_payload() {
    for kind in [MessageKind::Push, MessageKind::Request, MessageKind::Response] {
        for count in [0, 1, 254, 255] {
            assert!(decodes(kind, 0b100, &valid_records(count)), "{kind:?}: valid count {count}");
        }
    }
}

#[test]
fn decoder_rejects_zero_configuration() {
    for kind in [MessageKind::Push, MessageKind::Request, MessageKind::Response] {
        for count in [0, 1] {
            assert!(!decodes(kind, 0, &valid_records(count)), "{kind:?}: zero configuration");
        }
    }
}

#[test]
fn decoder_rejects_record_count_above_255() {
    for kind in [MessageKind::Push, MessageKind::Request, MessageKind::Response] {
        // Complete record bodies distinguish count validation from a short-read error.
        for count in [256, 257] {
            assert!(!decodes(kind, 0b100, &valid_records(count)), "{kind:?}: count {count}");
        }
    }
}

/// Public transport injection carries an otherwise normal Push to a real
/// public subscriber. Filtering in the wire decoder or at public conversion
/// is equally acceptable; the raw extension representation is never inspected.
async fn delivered_stack(records: Vec<(u8, Vec<u8>)>) -> zenoh::timestamp_stack::TimestampStack {
    let mut config = zenoh::Config::default();
    config.set_mode(Some(WhatAmI::Router)).unwrap();
    config.listen.endpoints.set(vec!["tcp/127.0.0.1:0".parse().unwrap()]).unwrap();
    config.scouting.multicast.set_enabled(Some(false)).unwrap();
    config.adminspace.set_enabled(false).unwrap();
    let session = zenoh::open(config)
        .with_timestamp_callback(|_| RECEIVER_TIMESTAMP.to_vec()).await.unwrap();
    let subscriber = session.declare_subscriber(KEY).await.unwrap();
    let endpoint = session.info().locators().await.into_iter()
        .find(|locator| locator.to_string().starts_with("tcp/"))
        .expect("listener must have a TCP locator").to_endpoint();
    let manager = TransportManager::builder()
        .whatami(zenoh_protocol::core::WhatAmI::Client)
        .build_test(Arc::new(DummyTransportEventHandler)).unwrap();
    let transport = manager.open_transport_unicast(endpoint).await.unwrap();
    let bytes = message_bytes(MessageKind::Push, 0b100, &records);
    let push: Push = Zenoh080.read(&mut bytes.reader()).expect("valid message must decode");
    let mut message = NetworkMessage::from(push);
    assert!(transport.schedule(message.as_mut()).unwrap(), "transport must accept the probe");
    let sample = tokio::time::timeout(Duration::from_secs(15), subscriber.recv_async())
        .await.expect("timestamp robustness must not drop the publication").unwrap();
    assert_eq!(sample.key_expr().as_str(), KEY);
    assert_eq!(sample.payload().to_bytes().as_ref(), PAYLOAD);
    let stack = sample.timestamp_stack().expect("instrumentation must survive delivery").clone();
    let expected_configuration = TimestampInstrumentationBuilder::new()
        .set_send(false).set_route(false).set_receive(true).build().unwrap();
    assert_eq!(stack.instrumentation(), expected_configuration);
    manager.close().await;
    session.close().await.unwrap();
    stack
}

fn assert_custom_receive(record: &zenoh::timestamp_stack::TimestampStackRecord, expected: &[u8]) {
    assert_eq!(record.point(), InterceptionPoint::Receive);
    assert!(record.is_custom());
    assert_eq!(record.timestamp(), &InstrumentationTimestamp::Custom(expected.to_vec()));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn unknown_point_and_bad_uhlc_are_skipped_without_dropping_publication() {
    tokio::time::timeout(Duration::from_secs(60), async {
        let stack = delivered_stack(vec![
            (0x84, b"before-invalid-records".to_vec()),
            (0x83, b"unknown-interception-point".to_vec()),
            (0x04, Vec::new()), // empty bytes cannot decode as a UHLC timestamp
            (0x84, b"after-invalid-records".to_vec()),
        ]).await;
        assert_eq!(stack.records().len(), 3, "skip both bad records and append one Receive");
        for (record, expected) in stack.records().iter().zip([
            b"before-invalid-records".as_slice(), b"after-invalid-records".as_slice(), RECEIVER_TIMESTAMP,
        ]) { assert_custom_receive(record, expected); }
    }).await.expect("malformed-record delivery test exceeded 60 seconds");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn receive_appends_at_254_and_drops_further_records_at_255() {
    tokio::time::timeout(Duration::from_secs(60), async {
        for count in [254, 255] {
            let incoming = valid_records(count);
            let stack = delivered_stack(incoming.clone()).await;
            assert_eq!(stack.records().len(), 255, "incoming count {count}");
            for (record, (_, expected)) in stack.records().iter().zip(&incoming) {
                assert_custom_receive(record, expected);
            }
            if count == 254 { assert_custom_receive(&stack.records()[254], RECEIVER_TIMESTAMP); }
        }
    }).await.expect("maximum-stack delivery test exceeded 60 seconds");
}
