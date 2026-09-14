//
// Copyright (c) 2026 ZettaScale Technology
//
// This program and the accompanying materials are made available under the
// terms of the Eclipse Public License 2.0 which is available at
// http://www.eclipse.org/legal/epl-2.0, or the Apache License, Version 2.0
// which is available at https://www.apache.org/licenses/LICENSE-2.0.
//
// SPDX-License-Identifier: EPL-2.0 OR Apache-2.0
//
// Contributors:
//   ZettaScale Zenoh Team, <zenoh@zettascale.tech>
//

//! Timestamp instrumentation of Zenoh messages, for latency measurement.
//!
//! When a publication, a query or a reply is sent with an [`TimestampInstrumentation`]
//! configuration, the message carries a [`TimestampStack`]: an ordered list of
//! [`TimestampStackRecord`]s, each stating at which [`InterceptionPoint`] it was recorded and
//! the timestamp taken there. Every Zenoh node the message traverses (the sending application,
//! each routing layer, the receiving application) appends its record if that point is enabled
//! in the configuration.
#![cfg(feature = "unstable")]

use std::sync::Arc;

use uhlc::HLC;
use zenoh_buffers::{buffer::SplitBuffer, reader::HasReader, writer::HasWriter, ZBuf};
use zenoh_codec::{RCodec, WCodec, Zenoh080};
use zenoh_protocol::{
    core::{WhatAmI, ZenohIdProto},
    network::ext::TimestampStackRecordType,
    // `Push`, `Request` and `Response` all use the same extension id for the timestamp stack, so
    // this concrete alias is interchangeable with `request::ext::TimestampStackType` and
    // `response::ext::TimestampStackType`.
    network::push::ext::TimestampStackType,
};
use zenoh_result::{bail, ZResult};

use crate::config::ZenohId;

// Wire-level bit values (see the protocol documentation of the extension).
const SEND_BIT: u8 = 0b001;
const ROUTE_BIT: u8 = 0b010;
const RECEIVE_BIT: u8 = 0b100;
const POINT_MASK: u8 = 0b0000_0111;
const CUSTOM_FLAG: u8 = 0b1000_0000;
/// A stack never grows beyond this number of records.
const MAX_RECORDS: usize = 255;

/// The point, along the path a zenoh message travels, at which a [`TimestampStackRecord`] can be recorded.
///
#[zenoh_macros::unstable]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InterceptionPoint {
    /// The application that issues the publication/query (or the reply).
    Send,
    /// A routing layer forwarding the message (sending session, intermediate routers/peers,
    /// receiving session).
    Route,
    /// The application that delivers the message to a subscriber, a queryable or the reply
    /// handler.
    Receive,
}

#[zenoh_macros::unstable]
impl TryFrom<u8> for InterceptionPoint {
    type Error = zenoh_result::Error;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value & !CUSTOM_FLAG {
            SEND_BIT => Ok(Self::Send),
            ROUTE_BIT => Ok(Self::Route),
            RECEIVE_BIT => Ok(Self::Receive),
            _ => bail!("Invalid timestamp stack interception point: {:#04x}", value),
        }
    }
}

#[zenoh_macros::unstable]
impl From<InterceptionPoint> for u8 {
    fn from(value: InterceptionPoint) -> u8 {
        match value {
            InterceptionPoint::Send => SEND_BIT,
            InterceptionPoint::Route => ROUTE_BIT,
            InterceptionPoint::Receive => RECEIVE_BIT,
        }
    }
}

/// A builder for [`TimestampInstrumentation`].
#[zenoh_macros::unstable]
#[derive(Debug, Default, Clone, Copy)]
pub struct TimestampInstrumentationBuilder {
    conf: u8,
}

#[zenoh_macros::unstable]
impl TimestampInstrumentationBuilder {
    /// Creates a new, empty builder: no interception point is enabled.
    pub fn new() -> Self {
        Self::default()
    }

    fn set(mut self, bit: u8, enabled: bool) -> Self {
        if enabled {
            self.conf |= bit;
        } else {
            self.conf &= !bit;
        }
        self
    }

    /// Enables (or disables) recording at the [`InterceptionPoint::Send`] point.
    pub fn set_send(self, enabled: bool) -> Self {
        self.set(SEND_BIT, enabled)
    }

    /// Enables (or disables) recording at the [`InterceptionPoint::Route`] point.
    pub fn set_route(self, enabled: bool) -> Self {
        self.set(ROUTE_BIT, enabled)
    }

    /// Enables (or disables) recording at the [`InterceptionPoint::Receive`] point.
    pub fn set_receive(self, enabled: bool) -> Self {
        self.set(RECEIVE_BIT, enabled)
    }

    /// Builds the [`TimestampInstrumentation`], failing if no interception point is enabled.
    pub fn build(self) -> ZResult<TimestampInstrumentation> {
        if self.conf & POINT_MASK == 0 {
            bail!("TimestampInstrumentation requires at least one interception point to be enabled");
        }
        Ok(TimestampInstrumentation { conf: self.conf })
    }
}

/// The configuration of the timestamp instrumentation of a message: the set of [`InterceptionPoint`]s at which a [`TimestampStackRecord`] must be recorded.
///
/// Built via [`TimestampInstrumentationBuilder`].
///
#[zenoh_macros::unstable]
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct TimestampInstrumentation {
    conf: u8,
}

#[zenoh_macros::unstable]
impl TimestampInstrumentation {
    /// Returns whether `point` is enabled by this configuration.
    pub fn is_instrumented(&self, point: InterceptionPoint) -> bool {
        self.conf & u8::from(point) != 0
    }
}

/// The timestamp recorded in a [`TimestampStackRecord`].
#[zenoh_macros::unstable]
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InstrumentationTimestamp {
    /// A timestamp taken from the recording node's hybrid logical clock. The timestamp's id is
    /// the recording node's [`ZenohId`](crate::session::ZenohId).
    UHLC(uhlc::Timestamp),
    /// The bytes produced by the recording node's timestamp callback (see
    /// [`crate::session::OpenBuilder::with_timestamp_callback`]).
    Custom(Vec<u8>),
}

/// A single record of a [`TimestampStack`]: the [`InterceptionPoint`] at which it was recorded, and the [`InstrumentationTimestamp`] taken there.
///
#[zenoh_macros::unstable]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStackRecord {
    point: InterceptionPoint,
    timestamp: InstrumentationTimestamp,
}

#[zenoh_macros::unstable]
impl TimestampStackRecord {
    /// The [`InterceptionPoint`] at which this record was taken.
    pub fn point(&self) -> InterceptionPoint {
        self.point
    }

    /// Whether this record's timestamp is a custom (non-UHLC) one.
    pub fn is_custom(&self) -> bool {
        matches!(self.timestamp, InstrumentationTimestamp::Custom(_))
    }

    /// The timestamp recorded for this record.
    pub fn timestamp(&self) -> &InstrumentationTimestamp {
        &self.timestamp
    }
}

/// The timestamp instrumentation stack carried by a [`Sample`](crate::sample::Sample), a [`Query`](crate::query::Query) or a [`ReplyError`](crate::query::ReplyError).
///
#[zenoh_macros::unstable]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStack {
    instrumentation: TimestampInstrumentation,
    records: Vec<TimestampStackRecord>,
}

#[zenoh_macros::unstable]
impl TimestampStack {
    /// The instrumentation configuration used by the message's sender, preserved end to end.
    pub fn instrumentation(&self) -> TimestampInstrumentation {
        self.instrumentation
    }

    /// The records, in traversal order.
    pub fn records(&self) -> &[TimestampStackRecord] {
        &self.records
    }
}

impl TimestampStack {
    /// Builds an empty stack carrying `instrumentation` as its configuration.
    pub(crate) fn empty(instrumentation: TimestampInstrumentation) -> Self {
        Self {
            instrumentation,
            records: vec![],
        }
    }
}

/// The context passed to a timestamp callback set via [`OpenBuilder::with_timestamp_callback`](crate::session::OpenBuilder::with_timestamp_callback).
///
#[zenoh_macros::unstable]
#[non_exhaustive]
#[derive(Debug, Clone)]
pub struct TimestampContext {
    pub zid: ZenohId,
    pub whatami: WhatAmI,
}

pub(crate) type TimestampCallback =
    dyn Fn(TimestampContext) -> Vec<u8> + Send + Sync + 'static;

/// Node-local information required to produce [`TimestampStackRecord`]s: its id, its mode, its
/// dedicated instrumentation clock and, if configured, its custom timestamp callback.
#[derive(Clone)]
pub(crate) struct TimestampSource {
    zid: ZenohIdProto,
    whatami: WhatAmI,
    hlc: Arc<HLC>,
    callback: Option<Arc<TimestampCallback>>,
}

impl TimestampSource {
    pub(crate) fn new(
        zid: ZenohIdProto,
        whatami: WhatAmI,
        hlc: Arc<HLC>,
        callback: Option<Arc<TimestampCallback>>,
    ) -> Self {
        Self {
            zid,
            whatami,
            hlc,
            callback,
        }
    }

    /// Produces the bytes (and whether they are custom) for a new record, or `None` if no record
    /// should be produced (the callback returned an empty vector).
    fn record_bytes(&self) -> Option<(bool, Vec<u8>)> {
        match &self.callback {
            Some(cb) => {
                let bytes = cb(TimestampContext {
                    zid: self.zid.into(),
                    whatami: self.whatami,
                });
                if bytes.is_empty() {
                    None
                } else {
                    Some((true, bytes))
                }
            }
            None => {
                let ts = self.hlc.new_timestamp();
                Some((false, encode_uhlc_timestamp(&ts)))
            }
        }
    }
}

fn encode_uhlc_timestamp(ts: &uhlc::Timestamp) -> Vec<u8> {
    let mut buf = vec![];
    {
        let mut writer = buf.writer();
        // This is the exact same wire representation used for `ext_tstamp`.
        Zenoh080::new()
            .write(&mut writer, ts)
            .expect("writing to a Vec<u8> never fails");
    }
    buf
}

fn decode_uhlc_timestamp(bytes: &[u8]) -> Option<uhlc::Timestamp> {
    let mut reader = bytes.reader();
    Zenoh080::new().read(&mut reader).ok()
}

/// Builds the initial (empty) wire-level stack for a freshly configured instrumentation, or
/// `None` if `instrumentation` is `None`.
pub(crate) fn new_wire_stack(
    instrumentation: Option<TimestampInstrumentation>,
) -> Option<TimestampStackType> {
    let conf = instrumentation?.conf;
    (conf != 0).then_some(TimestampStackType {
        conf,
        records: vec![],
    })
}

/// Appends a `Route` record for a `Request`/`Response` unless the current node is in `Peer`
/// mode: unlike data routing, query/reply routing between two directly connected peers uses a
/// direct, interest-based dispatch that isn't considered a `Route` interception point (it is
/// only marked at `Client` or `Router` nodes, matching the "sending/receiving session + every
/// intermediate router" wording for those modes).
pub(crate) fn append_query_route_record(
    stack: &mut Option<TimestampStackType>,
    whatami: WhatAmI,
    source: &TimestampSource,
) {
    if whatami == WhatAmI::Peer {
        return;
    }
    append_record(stack, InterceptionPoint::Route, source);
}

/// Appends a record for `point` to `stack`, if instrumented, the stack isn't full and (in case a
/// custom callback is configured) the callback doesn't return an empty vector.
pub(crate) fn append_record(
    stack: &mut Option<TimestampStackType>,
    point: InterceptionPoint,
    source: &TimestampSource,
) {
    let Some(s) = stack.as_mut() else {
        return;
    };
    let bit = u8::from(point);
    if s.conf & bit == 0 || s.records.len() >= MAX_RECORDS {
        return;
    }
    if let Some((is_custom, bytes)) = source.record_bytes() {
        let flags = bit | if is_custom { CUSTOM_FLAG } else { 0 };
        s.records.push(TimestampStackRecordType {
            flags,
            timestamp: ZBuf::from(bytes),
        });
    }
}

/// Converts the wire-level representation into the domain [`TimestampStack`], skipping records
/// with an unknown interception point or an undecodable UHLC timestamp.
pub(crate) fn from_wire(wire: &TimestampStackType) -> TimestampStack {
    let instrumentation = TimestampInstrumentation { conf: wire.conf };
    let records = wire
        .records
        .iter()
        .filter_map(|r| {
            let point = InterceptionPoint::try_from(r.flags).ok()?;
            let is_custom = r.flags & CUSTOM_FLAG != 0;
            let bytes = r.timestamp.contiguous().into_owned();
            let timestamp = if is_custom {
                InstrumentationTimestamp::Custom(bytes)
            } else {
                InstrumentationTimestamp::UHLC(decode_uhlc_timestamp(&bytes)?)
            };
            Some(TimestampStackRecord { point, timestamp })
        })
        .collect();
    TimestampStack {
        instrumentation,
        records,
    }
}

pub(crate) fn from_wire_opt(wire: Option<&TimestampStackType>) -> Option<TimestampStack> {
    wire.map(from_wire)
}

/// Converts a single domain record back to its wire-level representation.
fn record_to_wire(record: &TimestampStackRecord) -> TimestampStackRecordType {
    let bit = u8::from(record.point());
    match record.timestamp() {
        InstrumentationTimestamp::UHLC(ts) => TimestampStackRecordType {
            flags: bit,
            timestamp: ZBuf::from(encode_uhlc_timestamp(ts)),
        },
        InstrumentationTimestamp::Custom(bytes) => TimestampStackRecordType {
            flags: bit | CUSTOM_FLAG,
            timestamp: ZBuf::from(bytes.clone()),
        },
    }
}

/// Converts a domain [`TimestampStack`] back to its wire-level representation, e.g. to build the
/// stack of a reply from the stack of the query that is being replied to.
pub(crate) fn to_wire(stack: &TimestampStack) -> TimestampStackType {
    TimestampStackType {
        conf: stack.instrumentation.conf,
        records: stack.records.iter().map(record_to_wire).collect(),
    }
}

#[cfg(test)]
mod tests {
    use zenoh_protocol::core::WhatAmI;

    use super::*;

    fn source(callback: Option<Arc<TimestampCallback>>) -> TimestampSource {
        TimestampSource::new(
            ZenohIdProto::default(),
            WhatAmI::Peer,
            Arc::new(uhlc::HLCBuilder::new().build()),
            callback,
        )
    }

    #[test]
    fn interception_point_roundtrip() {
        assert_eq!(u8::from(InterceptionPoint::Send), 0b001);
        assert_eq!(u8::from(InterceptionPoint::Route), 0b010);
        assert_eq!(u8::from(InterceptionPoint::Receive), 0b100);
        assert_eq!(InterceptionPoint::try_from(0b001).unwrap(), InterceptionPoint::Send);
        assert_eq!(InterceptionPoint::try_from(0b010).unwrap(), InterceptionPoint::Route);
        assert_eq!(InterceptionPoint::try_from(0b100).unwrap(), InterceptionPoint::Receive);
        // bit 7 (custom flag) is ignored
        assert_eq!(
            InterceptionPoint::try_from(0b1000_0100).unwrap(),
            InterceptionPoint::Receive
        );
        assert!(InterceptionPoint::try_from(0).is_err());
        assert!(InterceptionPoint::try_from(0b011).is_err());
        assert!(InterceptionPoint::try_from(0b1000_0000).is_err());
    }

    #[test]
    fn builder_requires_at_least_one_point() {
        assert!(TimestampInstrumentationBuilder::new().build().is_err());
        let inst = TimestampInstrumentationBuilder::new()
            .set_send(true)
            .build()
            .unwrap();
        assert!(inst.is_instrumented(InterceptionPoint::Send));
        assert!(!inst.is_instrumented(InterceptionPoint::Route));
    }

    #[test]
    fn append_record_respects_configuration() {
        let inst = TimestampInstrumentationBuilder::new()
            .set_send(true)
            .build()
            .unwrap();
        let mut stack = new_wire_stack(Some(inst));
        let src = source(None);
        // Route is not enabled: no-op.
        append_record(&mut stack, InterceptionPoint::Route, &src);
        assert_eq!(stack.as_ref().unwrap().records.len(), 0);
        // Send is enabled.
        append_record(&mut stack, InterceptionPoint::Send, &src);
        assert_eq!(stack.as_ref().unwrap().records.len(), 1);
        let domain = from_wire(stack.as_ref().unwrap());
        assert_eq!(domain.records().len(), 1);
        assert_eq!(domain.records()[0].point(), InterceptionPoint::Send);
        assert!(!domain.records()[0].is_custom());
    }

    #[test]
    fn append_record_caps_at_255() {
        let inst = TimestampInstrumentationBuilder::new()
            .set_send(true)
            .build()
            .unwrap();
        let mut stack = new_wire_stack(Some(inst));
        let src = source(None);
        for _ in 0..300 {
            append_record(&mut stack, InterceptionPoint::Send, &src);
        }
        assert_eq!(stack.as_ref().unwrap().records.len(), 255);
    }

    #[test]
    fn custom_callback_returning_empty_produces_no_record() {
        let inst = TimestampInstrumentationBuilder::new()
            .set_send(true)
            .build()
            .unwrap();
        let mut stack = new_wire_stack(Some(inst));
        let src = source(Some(Arc::new(|_ctx| Vec::new())));
        append_record(&mut stack, InterceptionPoint::Send, &src);
        assert_eq!(stack.as_ref().unwrap().records.len(), 0);
    }

    #[test]
    fn custom_callback_marks_records_as_custom() {
        let inst = TimestampInstrumentationBuilder::new()
            .set_send(true)
            .build()
            .unwrap();
        let mut stack = new_wire_stack(Some(inst));
        let src = source(Some(Arc::new(|ctx| format!("{}", ctx.whatami).into_bytes())));
        append_record(&mut stack, InterceptionPoint::Send, &src);
        let domain = from_wire(stack.as_ref().unwrap());
        assert!(domain.records()[0].is_custom());
        match domain.records()[0].timestamp() {
            InstrumentationTimestamp::Custom(bytes) => {
                assert_eq!(bytes, b"peer");
            }
            InstrumentationTimestamp::UHLC(_) => panic!("expected custom timestamp"),
        }
    }

    #[test]
    fn unknown_point_and_bad_uhlc_are_skipped() {
        let wire = TimestampStackType {
            conf: 0b111,
            records: vec![
                // unknown interception point (0b111 low bits)
                TimestampStackRecordType {
                    flags: 0b111,
                    timestamp: ZBuf::from(vec![0u8; 4]),
                },
                // valid Send point, but undecodable UHLC bytes (empty)
                TimestampStackRecordType {
                    flags: u8::from(InterceptionPoint::Send),
                    timestamp: ZBuf::from(Vec::<u8>::new()),
                },
                // valid Route point with proper UHLC encoding
                {
                    let ts = uhlc::Timestamp::new(uhlc::NTP64(42), uhlc::ID::try_from([1u8]).unwrap());
                    TimestampStackRecordType {
                        flags: u8::from(InterceptionPoint::Route),
                        timestamp: ZBuf::from(encode_uhlc_timestamp(&ts)),
                    }
                },
            ],
        };
        let domain = from_wire(&wire);
        // Only the well-formed Route record should survive.
        assert_eq!(domain.records().len(), 1);
        assert_eq!(domain.records()[0].point(), InterceptionPoint::Route);
    }

    #[test]
    fn domain_to_wire_roundtrip() {
        let inst = TimestampInstrumentationBuilder::new()
            .set_send(true)
            .set_receive(true)
            .build()
            .unwrap();
        let mut stack = new_wire_stack(Some(inst));
        append_record(&mut stack, InterceptionPoint::Send, &source(None));
        append_record(
            &mut stack,
            InterceptionPoint::Receive,
            &source(Some(Arc::new(|_ctx| vec![9, 9, 9]))),
        );
        let domain = from_wire(stack.as_ref().unwrap());
        let wire2 = to_wire(&domain);
        assert_eq!(&wire2, stack.as_ref().unwrap());
    }
}
