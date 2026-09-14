//
// Copyright (c) 2024 ZettaScale Technology
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

//! Timestamp instrumentation for latency measurement.
//!
//! See [`TimestampInstrumentationBuilder`], [`TimestampStack`] and the
//! `timestamp_instrumentation` builder methods.
#![cfg(feature = "unstable")]

use zenoh_buffers::{reader::HasReader, writer::HasWriter};
use zenoh_codec::{RCodec, WCodec, Zenoh080};
use zenoh_config::WhatAmI;
use zenoh_protocol::network::ext::{TsStackRecord as WireRecord, TsStackType};
use zenoh_result::{bail, ZResult};

const FLAG_SEND: u8 = 0b001;
const FLAG_ROUTE: u8 = 0b010;
const FLAG_RECEIVE: u8 = 0b100;
const FLAG_CUSTOM: u8 = 0b1000_0000;
const FLAG_POINT_MASK: u8 = 0b0111_1111;

/// The interception point at which a [`TimestampStackRecord`] was recorded.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InterceptionPoint {
    /// Recorded by the application that issues the publication/query/reply.
    Send,
    /// Recorded by the routing layer of a node the message traverses.
    Route,
    /// Recorded by the application that delivers the message to the user.
    Receive,
}

impl TryFrom<u8> for InterceptionPoint {
    type Error = zenoh_result::Error;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value & FLAG_POINT_MASK {
            FLAG_SEND => Ok(Self::Send),
            FLAG_ROUTE => Ok(Self::Route),
            FLAG_RECEIVE => Ok(Self::Receive),
            other => bail!("Invalid InterceptionPoint value: {}", other),
        }
    }
}

impl From<InterceptionPoint> for u8 {
    fn from(value: InterceptionPoint) -> Self {
        match value {
            InterceptionPoint::Send => FLAG_SEND,
            InterceptionPoint::Route => FLAG_ROUTE,
            InterceptionPoint::Receive => FLAG_RECEIVE,
        }
    }
}

/// A builder for [`TimestampInstrumentation`].
#[derive(Debug, Default, Clone, Copy)]
pub struct TimestampInstrumentationBuilder {
    send: bool,
    route: bool,
    receive: bool,
}

impl TimestampInstrumentationBuilder {
    /// Creates a new, empty builder.
    pub fn new() -> Self {
        Self::default()
    }

    /// Enables or disables recording at the [`InterceptionPoint::Send`] point.
    pub fn set_send(self, enabled: bool) -> Self {
        Self {
            send: enabled,
            ..self
        }
    }

    /// Enables or disables recording at the [`InterceptionPoint::Route`] point.
    pub fn set_route(self, enabled: bool) -> Self {
        Self {
            route: enabled,
            ..self
        }
    }

    /// Enables or disables recording at the [`InterceptionPoint::Receive`] point.
    pub fn set_receive(self, enabled: bool) -> Self {
        Self {
            receive: enabled,
            ..self
        }
    }

    /// Builds the [`TimestampInstrumentation`], failing if no point is enabled.
    pub fn build(self) -> ZResult<TimestampInstrumentation> {
        let conf = (self.send as u8 * FLAG_SEND)
            | (self.route as u8 * FLAG_ROUTE)
            | (self.receive as u8 * FLAG_RECEIVE);
        if conf == 0 {
            bail!("At least one interception point must be enabled");
        }
        Ok(TimestampInstrumentation { conf })
    }
}

/// The configuration of the timestamp instrumentation of a publication, query or reply.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct TimestampInstrumentation {
    conf: u8,
}

impl TimestampInstrumentation {
    /// Returns whether the given interception point is instrumented.
    pub fn is_instrumented(&self, point: InterceptionPoint) -> bool {
        self.conf & u8::from(point) != 0
    }

    pub(crate) fn conf(&self) -> u8 {
        self.conf
    }

    pub(crate) fn from_conf(conf: u8) -> Option<Self> {
        (conf != 0).then_some(Self { conf })
    }
}

/// The timestamp recorded at a given [`InterceptionPoint`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InstrumentationTimestamp {
    /// A timestamp taken from the recording node's hybrid logical clock.
    UHLC(uhlc::Timestamp),
    /// Custom bytes produced by the recording node's timestamp callback.
    Custom(Vec<u8>),
}

/// A single record of the timestamp stack.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStackRecord {
    point: InterceptionPoint,
    timestamp: InstrumentationTimestamp,
}

impl TimestampStackRecord {
    /// The interception point at which this record was taken.
    pub fn point(&self) -> InterceptionPoint {
        self.point
    }

    /// Whether this record holds custom bytes rather than a UHLC timestamp.
    pub fn is_custom(&self) -> bool {
        matches!(self.timestamp, InstrumentationTimestamp::Custom(_))
    }

    /// The timestamp of this record.
    pub fn timestamp(&self) -> &InstrumentationTimestamp {
        &self.timestamp
    }
}

/// The timestamp stack carried by an instrumented publication, query or reply.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStack {
    instrumentation: TimestampInstrumentation,
    records: Vec<TimestampStackRecord>,
}

impl TimestampStack {
    /// The instrumentation configuration used by the sender, unchanged end to end.
    pub fn instrumentation(&self) -> TimestampInstrumentation {
        self.instrumentation
    }

    /// The records collected so far, in traversal order.
    pub fn records(&self) -> &[TimestampStackRecord] {
        &self.records
    }

    pub(crate) fn new(instrumentation: TimestampInstrumentation) -> Self {
        Self {
            instrumentation,
            records: Vec::new(),
        }
    }
}

/// Context passed to a timestamp callback registered via
/// [`OpenBuilder::with_timestamp_callback`](crate::session::OpenBuilder::with_timestamp_callback).
#[non_exhaustive]
#[derive(Debug, Clone)]
pub struct TimestampContext {
    pub zid: zenoh_protocol::core::ZenohIdProto,
    pub whatami: WhatAmI,
}

/// A source of records for the local node: either the node's HLC or a custom callback.
#[derive(Clone)]
pub(crate) enum TimestampProducer {
    Hlc {
        zid: zenoh_protocol::core::ZenohIdProto,
        hlc: Option<std::sync::Arc<uhlc::HLC>>,
    },
    Callback {
        zid: zenoh_protocol::core::ZenohIdProto,
        whatami: WhatAmI,
        callback: std::sync::Arc<dyn Fn(TimestampContext) -> Vec<u8> + Send + Sync>,
    },
}

impl TimestampProducer {
    pub(crate) fn hlc(
        zid: zenoh_protocol::core::ZenohIdProto,
        hlc: Option<std::sync::Arc<uhlc::HLC>>,
    ) -> Self {
        Self::Hlc { zid, hlc }
    }

    pub(crate) fn callback(
        zid: zenoh_protocol::core::ZenohIdProto,
        whatami: WhatAmI,
        callback: std::sync::Arc<dyn Fn(TimestampContext) -> Vec<u8> + Send + Sync>,
    ) -> Self {
        Self::Callback {
            zid,
            whatami,
            callback,
        }
    }

    /// Produces a wire record for the given point, or `None` if nothing should be recorded
    /// (e.g. a callback returning an empty vector).
    pub(crate) fn record(&self, point: InterceptionPoint) -> Option<WireRecord> {
        match self {
            TimestampProducer::Hlc { zid, hlc } => {
                let ts = match hlc {
                    Some(hlc) => hlc.new_timestamp(),
                    None => {
                        let dur = std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default();
                        let now = uhlc::NTP64::from(dur);
                        let id = uhlc::ID::try_from(zid.to_le_bytes()).ok()?;
                        uhlc::Timestamp::new(now, id)
                    }
                };
                let mut bytes = Vec::new();
                let mut writer = bytes.writer();
                Zenoh080.write(&mut writer, &ts).ok()?;
                Some(WireRecord {
                    flags: u8::from(point),
                    bytes,
                })
            }
            TimestampProducer::Callback {
                zid,
                whatami,
                callback,
            } => {
                let bytes = callback(TimestampContext {
                    zid: (*zid).into(),
                    whatami: *whatami,
                });
                if bytes.is_empty() {
                    return None;
                }
                Some(WireRecord {
                    flags: u8::from(point) | FLAG_CUSTOM,
                    bytes,
                })
            }
        }
    }
}

/// Appends a record to the wire-level stack (if instrumentation for `point` is enabled),
/// respecting the 255-record limit.
pub(crate) fn append_record<const ID: u8>(
    stack: &mut Option<TsStackType<ID>>,
    point: InterceptionPoint,
    producer: &TimestampProducer,
) {
    let Some(s) = stack.as_mut() else { return };
    if s.conf & u8::from(point) == 0 {
        return;
    }
    if s.records.len() >= 255 {
        return;
    }
    if let Some(rec) = producer.record(point) {
        s.records.push(rec);
    }
}

/// Creates a new wire-level stack for the given instrumentation configuration, if any.
pub(crate) fn new_wire_stack<const ID: u8>(
    instrumentation: Option<TimestampInstrumentation>,
) -> Option<TsStackType<ID>> {
    instrumentation.map(|i| TsStackType {
        conf: i.conf(),
        records: Vec::new(),
    })
}

fn decode_uhlc(bytes: &[u8]) -> Option<uhlc::Timestamp> {
    let mut reader = bytes.reader();
    Zenoh080.read(&mut reader).ok()
}

/// Converts a wire-level stack into the business-level [`TimestampStack`].
pub(crate) fn wire_to_stack<const ID: u8>(
    wire: &Option<TsStackType<ID>>,
) -> Option<TimestampStack> {
    let wire = wire.as_ref()?;
    let instrumentation = TimestampInstrumentation::from_conf(wire.conf)?;
    let mut records = Vec::new();
    for r in wire.records.iter() {
        let Ok(point) = InterceptionPoint::try_from(r.flags) else {
            continue;
        };
        let timestamp = if r.flags & FLAG_CUSTOM != 0 {
            InstrumentationTimestamp::Custom(r.bytes.clone())
        } else {
            match decode_uhlc(&r.bytes) {
                Some(ts) => InstrumentationTimestamp::UHLC(ts),
                None => continue,
            }
        };
        records.push(TimestampStackRecord { point, timestamp });
    }
    Some(TimestampStack {
        instrumentation,
        records,
    })
}

/// Clones a wire-level stack transmuting it to a different extension id.
pub(crate) fn transmute_wire<const FROM: u8, const TO: u8>(
    wire: Option<TsStackType<FROM>>,
) -> Option<TsStackType<TO>> {
    wire.map(|w| w.transmute())
}


/// Appends a business-level record, respecting the 255-record limit.
pub(crate) fn append_business_record(
    stack: &mut Option<TimestampStack>,
    point: InterceptionPoint,
    producer: &TimestampProducer,
) {
    let Some(s) = stack.as_mut() else { return };
    if !s.instrumentation.is_instrumented(point) {
        return;
    }
    if s.records.len() >= 255 {
        return;
    }
    if let Some(rec) = producer.record(point) {
        let Ok(point) = InterceptionPoint::try_from(rec.flags) else {
            return;
        };
        let timestamp = if rec.flags & FLAG_CUSTOM != 0 {
            InstrumentationTimestamp::Custom(rec.bytes)
        } else {
            match decode_uhlc(&rec.bytes) {
                Some(ts) => InstrumentationTimestamp::UHLC(ts),
                None => return,
            }
        };
        s.records.push(TimestampStackRecord { point, timestamp });
    }
}


/// Converts a business-level stack into a wire-level one for a specific message type.
pub(crate) fn stack_to_wire<const ID: u8>(
    stack: &Option<TimestampStack>,
) -> Option<TsStackType<ID>> {
    let stack = stack.as_ref()?;
    let mut records = Vec::with_capacity(stack.records.len());
    for r in stack.records.iter() {
        let flags = u8::from(r.point)
            | match &r.timestamp {
                InstrumentationTimestamp::Custom(_) => FLAG_CUSTOM,
                InstrumentationTimestamp::UHLC(_) => 0,
            };
        let bytes = match &r.timestamp {
            InstrumentationTimestamp::Custom(b) => b.clone(),
            InstrumentationTimestamp::UHLC(ts) => {
                let mut bytes = Vec::new();
                let mut writer = bytes.writer();
                if Zenoh080.write(&mut writer, ts).is_err() {
                    continue;
                }
                bytes
            }
        };
        records.push(WireRecord { flags, bytes });
    }
    Some(TsStackType {
        conf: stack.instrumentation.conf,
        records,
    })
}
