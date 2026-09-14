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

//! Timestamp stack instrumentation, allowing to measure the latency of a message
//! (publication, query, reply) as it traverses the different interception points of Zenoh.
use std::sync::Arc;

use uhlc::HLC;
use zenoh_buffers::{reader::HasReader, writer::HasWriter};
use zenoh_codec::{RCodec, WCodec, Zenoh080};
use zenoh_config::wrappers::ZenohId;
use zenoh_protocol::core::WhatAmI;
use zenoh_result::{bail, ZResult};

/// The interception point at which a [`TimestampStackRecord`] has been recorded.
#[zenoh_macros::unstable]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InterceptionPoint {
    /// The application issuing the publication/query/reply.
    Send,
    /// A routing layer forwarding the message.
    Route,
    /// The application receiving the publication/query/reply.
    Receive,
}

#[zenoh_macros::unstable]
impl InterceptionPoint {
    const SEND: u8 = 0b001;
    const ROUTE: u8 = 0b010;
    const RECEIVE: u8 = 0b100;
}

#[zenoh_macros::unstable]
impl TryFrom<u8> for InterceptionPoint {
    type Error = zenoh_result::Error;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        // bit 7 is the custom-format flag and is ignored here
        match value & 0b0111_1111 {
            Self::SEND => Ok(InterceptionPoint::Send),
            Self::ROUTE => Ok(InterceptionPoint::Route),
            Self::RECEIVE => Ok(InterceptionPoint::Receive),
            _ => bail!("Invalid interception point value: {}", value),
        }
    }
}

#[zenoh_macros::unstable]
impl From<InterceptionPoint> for u8 {
    fn from(value: InterceptionPoint) -> Self {
        match value {
            InterceptionPoint::Send => InterceptionPoint::SEND,
            InterceptionPoint::Route => InterceptionPoint::ROUTE,
            InterceptionPoint::Receive => InterceptionPoint::RECEIVE,
        }
    }
}

/// The configuration of the timestamp stack instrumentation: at which [`InterceptionPoint`]s
///
/// a [`TimestampStackRecord`] should be recorded.
#[zenoh_macros::unstable]
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct TimestampInstrumentation {
    conf: u8,
}

#[zenoh_macros::unstable]
impl TimestampInstrumentation {
    /// Returns `true` if the given [`InterceptionPoint`] is enabled by this configuration.
    pub fn is_instrumented(&self, point: InterceptionPoint) -> bool {
        self.conf & u8::from(point) != 0
    }

    pub(crate) fn conf(&self) -> u8 {
        self.conf
    }

    pub(crate) fn from_conf(conf: u8) -> Self {
        Self { conf }
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
    /// Creates a new, empty builder.
    pub fn new() -> Self {
        Self::default()
    }

    fn set(mut self, point: InterceptionPoint, enabled: bool) -> Self {
        let bit = u8::from(point);
        if enabled {
            self.conf |= bit;
        } else {
            self.conf &= !bit;
        }
        self
    }

    /// Enables/disables recording at the [`InterceptionPoint::Send`] point.
    pub fn set_send(self, enabled: bool) -> Self {
        self.set(InterceptionPoint::Send, enabled)
    }

    /// Enables/disables recording at the [`InterceptionPoint::Route`] point.
    pub fn set_route(self, enabled: bool) -> Self {
        self.set(InterceptionPoint::Route, enabled)
    }

    /// Enables/disables recording at the [`InterceptionPoint::Receive`] point.
    pub fn set_receive(self, enabled: bool) -> Self {
        self.set(InterceptionPoint::Receive, enabled)
    }

    /// Builds the [`TimestampInstrumentation`]. Fails if no interception point is enabled.
    pub fn build(self) -> ZResult<TimestampInstrumentation> {
        if self.conf == 0 {
            bail!("At least one interception point must be enabled");
        }
        Ok(TimestampInstrumentation { conf: self.conf })
    }
}

/// A timestamp recorded in a [`TimestampStackRecord`].
///
/// Either a UHLC timestamp, or application-defined bytes returned by a timestamp callback (see
/// [`OpenBuilder::with_timestamp_callback`](crate::session::OpenBuilder::with_timestamp_callback)).
#[zenoh_macros::unstable]
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InstrumentationTimestamp {
    UHLC(uhlc::Timestamp),
    Custom(Vec<u8>),
}

/// A single record of a [`TimestampStack`].
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

    /// `true` if the timestamp is application-defined (custom) bytes.
    pub fn is_custom(&self) -> bool {
        matches!(self.timestamp, InstrumentationTimestamp::Custom(_))
    }

    /// The recorded timestamp.
    pub fn timestamp(&self) -> &InstrumentationTimestamp {
        &self.timestamp
    }
}

/// The list of [`TimestampStackRecord`]s carried by an instrumented publication, query or reply.
#[zenoh_macros::unstable]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStack {
    instrumentation: TimestampInstrumentation,
    records: Vec<TimestampStackRecord>,
}

#[zenoh_macros::unstable]
impl TimestampStack {
    pub(crate) fn new(instrumentation: TimestampInstrumentation) -> Self {
        Self {
            instrumentation,
            records: Vec::new(),
        }
    }

    /// The instrumentation configuration used by the sender of the message, preserved end to end.
    pub fn instrumentation(&self) -> TimestampInstrumentation {
        self.instrumentation
    }

    /// The records, in traversal order.
    pub fn records(&self) -> &[TimestampStackRecord] {
        &self.records
    }

    /// Appends a record for `point`, if that point is enabled by the instrumentation
    /// configuration, the stack is not full (255 records max), and the recorder actually
    /// produces a timestamp (a custom callback may produce none by returning an empty vector).
    pub(crate) fn append(
        &mut self,
        point: InterceptionPoint,
        zid: ZenohId,
        whatami: WhatAmI,
        hlc: &HLC,
        callback: Option<&Arc<dyn Fn(TimestampContext) -> Vec<u8> + Send + Sync>>,
    ) {
        if !self.instrumentation.is_instrumented(point) || self.records.len() >= 255 {
            return;
        }
        if let Some(timestamp) = record_timestamp(zid, whatami, hlc, callback) {
            self.records.push(TimestampStackRecord { point, timestamp });
        }
    }
}

/// The context passed to a timestamp callback.
///
/// See [`OpenBuilder::with_timestamp_callback`](crate::session::OpenBuilder::with_timestamp_callback).
#[zenoh_macros::unstable]
#[non_exhaustive]
#[derive(Debug, Clone, Copy)]
pub struct TimestampContext {
    pub zid: ZenohId,
    pub whatami: WhatAmI,
}

fn record_timestamp(
    zid: ZenohId,
    whatami: WhatAmI,
    hlc: &HLC,
    callback: Option<&Arc<dyn Fn(TimestampContext) -> Vec<u8> + Send + Sync>>,
) -> Option<InstrumentationTimestamp> {
    if let Some(cb) = callback {
        let bytes = cb(TimestampContext { zid, whatami });
        if bytes.is_empty() {
            None
        } else {
            Some(InstrumentationTimestamp::Custom(bytes))
        }
    } else {
        Some(InstrumentationTimestamp::UHLC(hlc.new_timestamp()))
    }
}

fn encode_uhlc_timestamp(ts: &uhlc::Timestamp) -> Vec<u8> {
    let mut buf: Vec<u8> = Vec::new();
    let mut writer = buf.writer();
    // Encoding into a `Vec<u8>` writer never fails.
    let _ = Zenoh080::new().write(&mut writer, ts);
    buf
}

fn decode_uhlc_timestamp(bytes: &[u8]) -> Option<uhlc::Timestamp> {
    let mut reader = bytes.reader();
    Zenoh080::new().read(&mut reader).ok()
}

/// Converts a [`TimestampStack`] into its wire representation.
pub(crate) fn ts_stack_to_wire<const ID: u8>(
    stack: &TimestampStack,
) -> zenoh_protocol::network::ext::TsStackType<ID> {
    let conf = stack.instrumentation.conf();
    let records = stack
        .records
        .iter()
        .map(|r| {
            let point: u8 = r.point.into();
            let (custom_bit, timestamp) = match &r.timestamp {
                InstrumentationTimestamp::UHLC(ts) => (0u8, encode_uhlc_timestamp(ts)),
                InstrumentationTimestamp::Custom(bytes) => (0x80u8, bytes.clone()),
            };
            zenoh_protocol::network::ext::TsStackRecordType {
                flags: point | custom_bit,
                timestamp,
            }
        })
        .collect();
    zenoh_protocol::network::ext::TsStackType { conf, records }
}

/// Converts a wire timestamp stack into a [`TimestampStack`], silently skipping any record
/// carrying an unknown interception point or an undecodable UHLC timestamp.
pub(crate) fn ts_stack_from_wire<const ID: u8>(
    wire: zenoh_protocol::network::ext::TsStackType<ID>,
) -> Option<TimestampStack> {
    if wire.conf == 0 {
        return None;
    }
    let instrumentation = TimestampInstrumentation::from_conf(wire.conf);
    let mut records = Vec::with_capacity(wire.records.len());
    for r in wire.records {
        let Ok(point) = InterceptionPoint::try_from(r.flags) else {
            continue;
        };
        let is_custom = r.flags & 0x80 != 0;
        let timestamp = if is_custom {
            InstrumentationTimestamp::Custom(r.timestamp)
        } else {
            match decode_uhlc_timestamp(&r.timestamp) {
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

/// Given an optional configuration, builds an (empty) [`TimestampStack`], if any point is
/// actually enabled.
pub(crate) fn new_stack(
    instrumentation: Option<TimestampInstrumentation>,
) -> Option<TimestampStack> {
    match instrumentation {
        Some(instrumentation) if instrumentation.conf() != 0 => Some(TimestampStack::new(instrumentation)),
        _ => None,
    }
}
