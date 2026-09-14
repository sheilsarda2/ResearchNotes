//
// Copyright (c) 2025 ZettaScale Technology
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

//! Timestamp instrumentation primitives, used to measure where latency accrues
//! along the path of a message.

use std::{fmt, sync::Arc};

use uhlc::{HLCBuilder, HLC};
use zenoh_config::{WhatAmI, ZenohId};
use zenoh_protocol::network::ext::{
    TimestampStackRecord as TimestampStackRecordProto, TimestampStackTimestamp,
    TimestampStackType,
};
use zenoh_result::{bail, ZResult};

/// The point along the path of a message at which a timestamp is recorded.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InterceptionPoint {
    /// The application issuing the publication, the query or the reply.
    Send,
    /// The routing layer of each node the message traverses.
    Route,
    /// The application delivering the message to a subscriber, a queryable or a reply handler.
    Receive,
}

impl InterceptionPoint {
    const SEND: u8 = TimestampStackType::<0>::SEND;
    const ROUTE: u8 = TimestampStackType::<0>::ROUTE;
    const RECEIVE: u8 = TimestampStackType::<0>::RECEIVE;
}

impl TryFrom<u8> for InterceptionPoint {
    type Error = zenoh_result::Error;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        // bit 7 is the custom-format flag, ignore it
        match value & !TimestampStackRecordProto::CUSTOM_FLAG {
            Self::SEND => Ok(InterceptionPoint::Send),
            Self::ROUTE => Ok(InterceptionPoint::Route),
            Self::RECEIVE => Ok(InterceptionPoint::Receive),
            other => bail!("Unknown interception point: {other:#010b}"),
        }
    }
}

impl From<InterceptionPoint> for u8 {
    fn from(value: InterceptionPoint) -> Self {
        match value {
            InterceptionPoint::Send => InterceptionPoint::SEND,
            InterceptionPoint::Route => InterceptionPoint::ROUTE,
            InterceptionPoint::Receive => InterceptionPoint::RECEIVE,
        }
    }
}

/// A builder for [`TimestampInstrumentation`].
///
/// At least one interception point must be enabled for [`build`](Self::build) to succeed.
#[derive(Debug, Default, Clone, Copy)]
pub struct TimestampInstrumentationBuilder {
    flags: u8,
}

impl TimestampInstrumentationBuilder {
    /// Creates a builder with no interception point enabled.
    pub fn new() -> Self {
        Self::default()
    }

    fn set(mut self, point: InterceptionPoint, enabled: bool) -> Self {
        let bit = u8::from(point);
        if enabled {
            self.flags |= bit;
        } else {
            self.flags &= !bit;
        }
        self
    }

    /// Enables or disables the [`Send`](InterceptionPoint::Send) interception point.
    pub fn set_send(self, enabled: bool) -> Self {
        self.set(InterceptionPoint::Send, enabled)
    }

    /// Enables or disables the [`Route`](InterceptionPoint::Route) interception point.
    pub fn set_route(self, enabled: bool) -> Self {
        self.set(InterceptionPoint::Route, enabled)
    }

    /// Enables or disables the [`Receive`](InterceptionPoint::Receive) interception point.
    pub fn set_receive(self, enabled: bool) -> Self {
        self.set(InterceptionPoint::Receive, enabled)
    }

    /// Builds the [`TimestampInstrumentation`].
    ///
    /// Fails if no interception point is enabled.
    pub fn build(self) -> ZResult<TimestampInstrumentation> {
        if self.flags & TimestampStackType::<0>::INSTRUMENTATION_MASK == 0 {
            bail!("At least one interception point must be enabled");
        }
        Ok(TimestampInstrumentation { flags: self.flags })
    }
}

/// The configuration of the timestamp instrumentation of a message: the set of
/// [`InterceptionPoint`]s at which a timestamp is recorded.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct TimestampInstrumentation {
    flags: u8,
}

impl TimestampInstrumentation {
    pub(crate) fn from_flags(flags: u8) -> Self {
        Self {
            flags: flags & TimestampStackType::<0>::INSTRUMENTATION_MASK,
        }
    }

    pub(crate) fn flags(&self) -> u8 {
        self.flags
    }

    /// Returns `true` if a timestamp is recorded at the given interception point.
    pub fn is_instrumented(&self, point: InterceptionPoint) -> bool {
        self.flags & u8::from(point) != 0
    }
}

/// A timestamp recorded in a [`TimestampStack`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InstrumentationTimestamp {
    /// A timestamp taken from the hybrid logical clock of the node, whose id is the node's `ZenohId`.
    UHLC(uhlc::Timestamp),
    /// Custom bytes produced by the timestamp callback of the node
    /// (see [`OpenBuilder::with_timestamp_callback`](crate::session::OpenBuilder::with_timestamp_callback)).
    Custom(Vec<u8>),
}

impl From<TimestampStackTimestamp> for InstrumentationTimestamp {
    fn from(value: TimestampStackTimestamp) -> Self {
        match value {
            TimestampStackTimestamp::UHLC(ts) => InstrumentationTimestamp::UHLC(ts),
            TimestampStackTimestamp::Custom(bytes) => InstrumentationTimestamp::Custom(bytes),
        }
    }
}

impl From<InstrumentationTimestamp> for TimestampStackTimestamp {
    fn from(value: InstrumentationTimestamp) -> Self {
        match value {
            InstrumentationTimestamp::UHLC(ts) => TimestampStackTimestamp::UHLC(ts),
            InstrumentationTimestamp::Custom(bytes) => TimestampStackTimestamp::Custom(bytes),
        }
    }
}

/// A record of a [`TimestampStack`]: the [`InterceptionPoint`] at which it was recorded
/// and the timestamp taken there.
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

    /// Returns `true` if the timestamp holds custom bytes produced by a timestamp callback.
    pub fn is_custom(&self) -> bool {
        matches!(self.timestamp, InstrumentationTimestamp::Custom(_))
    }

    /// The timestamp of this record.
    pub fn timestamp(&self) -> &InstrumentationTimestamp {
        &self.timestamp
    }
}

impl TryFrom<TimestampStackRecordProto> for TimestampStackRecord {
    type Error = zenoh_result::Error;

    fn try_from(value: TimestampStackRecordProto) -> Result<Self, Self::Error> {
        Ok(TimestampStackRecord {
            point: InterceptionPoint::try_from(value.point)?,
            timestamp: value.timestamp.into(),
        })
    }
}

impl From<TimestampStackRecord> for TimestampStackRecordProto {
    fn from(value: TimestampStackRecord) -> Self {
        TimestampStackRecordProto {
            point: value.point.into(),
            timestamp: value.timestamp.into(),
        }
    }
}

/// The ordered list of timestamps recorded along the path of a message.
///
/// Every node the message traverses appends a [`TimestampStackRecord`] for each enabled
/// [`InterceptionPoint`] of the [`TimestampInstrumentation`] the sender configured.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStack {
    instrumentation: TimestampInstrumentation,
    records: Vec<TimestampStackRecord>,
}

impl TimestampStack {
    /// Creates an empty stack with the given configuration.
    pub(crate) fn new(instrumentation: TimestampInstrumentation) -> Self {
        Self {
            instrumentation,
            records: Vec::new(),
        }
    }

    /// The instrumentation configuration the sender used.
    pub fn instrumentation(&self) -> TimestampInstrumentation {
        self.instrumentation
    }

    /// The records, in traversal order.
    pub fn records(&self) -> &[TimestampStackRecord] {
        &self.records
    }
}

impl<const ID: u8> From<TimestampStackType<{ ID }>> for TimestampStack {
    fn from(value: TimestampStackType<{ ID }>) -> Self {
        TimestampStack {
            instrumentation: TimestampInstrumentation::from_flags(value.instrumentation),
            records: value
                .records
                .into_iter()
                .filter_map(|r| TimestampStackRecord::try_from(r).ok())
                .collect(),
        }
    }
}

impl<const ID: u8> From<&TimestampStackType<{ ID }>> for TimestampStack {
    fn from(value: &TimestampStackType<{ ID }>) -> Self {
        value.clone().into()
    }
}

impl<const ID: u8> From<TimestampStack> for TimestampStackType<{ ID }> {
    fn from(value: TimestampStack) -> Self {
        TimestampStackType {
            instrumentation: value.instrumentation.flags(),
            records: value.records.into_iter().map(Into::into).collect(),
        }
    }
}

impl<const ID: u8> From<&TimestampStack> for TimestampStackType<{ ID }> {
    fn from(value: &TimestampStack) -> Self {
        value.clone().into()
    }
}

/// The context passed to the timestamp callback of a node
/// (see [`OpenBuilder::with_timestamp_callback`](crate::session::OpenBuilder::with_timestamp_callback)).
#[non_exhaustive]
#[derive(Debug, Clone, Copy)]
pub struct TimestampContext {
    /// The `ZenohId` of the node recording the timestamp.
    pub zid: ZenohId,
    /// The mode of the node recording the timestamp.
    pub whatami: WhatAmI,
}

/// The timestamp callback type: produces the custom bytes of a record.
pub(crate) type TimestampCallback = Arc<dyn Fn(TimestampContext) -> Vec<u8> + Send + Sync>;

/// The component producing the timestamp records of a node.
pub(crate) struct TimestampRecorder {
    zid: ZenohId,
    whatami: WhatAmI,
    hlc: Arc<HLC>,
    callback: Option<TimestampCallback>,
}

impl fmt::Debug for TimestampRecorder {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("TimestampRecorder")
            .field("zid", &self.zid)
            .field("whatami", &self.whatami)
            .field("has_callback", &self.callback.is_some())
            .finish()
    }
}

impl TimestampRecorder {
    pub(crate) fn new(
        zid: ZenohId,
        whatami: WhatAmI,
        hlc: Option<Arc<HLC>>,
        callback: Option<TimestampCallback>,
    ) -> Self {
        let hlc = hlc
            .unwrap_or_else(|| Arc::new(HLCBuilder::new().with_id(uhlc::ID::from(zid)).build()));
        Self {
            zid,
            whatami,
            hlc,
            callback,
        }
    }

    /// Creates a new record for the given interception point, or `None` if the
    /// callback of the node produced no bytes.
    fn new_record(&self, point: u8) -> Option<TimestampStackRecordProto> {
        let timestamp = match &self.callback {
            Some(callback) => {
                let bytes = callback(TimestampContext {
                    zid: self.zid,
                    whatami: self.whatami,
                });
                if bytes.is_empty() {
                    return None;
                }
                TimestampStackTimestamp::Custom(bytes)
            }
            None => TimestampStackTimestamp::UHLC(self.hlc.new_timestamp()),
        };
        Some(TimestampStackRecordProto { point, timestamp })
    }

    /// Appends a record to the stack, if any, if the given interception point is enabled.
    #[inline]
    pub(crate) fn record<const ID: u8>(
        &self,
        stack: &mut Option<TimestampStackType<{ ID }>>,
        point: InterceptionPoint,
    ) {
        if let Some(stack) = stack.as_mut() {
            self.record_inner(stack, point);
        }
    }

    #[cold]
    fn record_inner<const ID: u8>(
        &self,
        stack: &mut TimestampStackType<{ ID }>,
        point: InterceptionPoint,
    ) {
        let point = u8::from(point);
        if !stack.is_instrumented(point)
            || stack.records.len() >= TimestampStackType::<{ ID }>::MAX_RECORDS
        {
            return;
        }
        if let Some(record) = self.new_record(point) {
            stack.push(record);
        }
    }

    /// Creates a new stack for the given instrumentation, if any, with a `Send` record.
    pub(crate) fn new_stack<const ID: u8>(
        &self,
        instrumentation: Option<TimestampInstrumentation>,
    ) -> Option<TimestampStackType<{ ID }>> {
        let mut stack = instrumentation.map(|i| TimestampStackType::new(i.flags()));
        self.record(&mut stack, InterceptionPoint::Send);
        stack
    }
}

/// Builder trait for setting the timestamp instrumentation of a message.
pub trait TimestampInstrumentationBuilderTrait {
    /// Sets an optional [`TimestampInstrumentation`] for the message.
    ///
    /// When set, the message carries a [`TimestampStack`] recorded at the enabled interception points.
    #[zenoh_macros::unstable]
    fn timestamp_instrumentation<TS: Into<Option<TimestampInstrumentation>>>(
        self,
        timestamp_instrumentation: TS,
    ) -> Self;
}
