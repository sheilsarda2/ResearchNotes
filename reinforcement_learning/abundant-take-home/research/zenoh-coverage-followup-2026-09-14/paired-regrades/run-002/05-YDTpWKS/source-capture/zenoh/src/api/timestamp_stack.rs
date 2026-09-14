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

//! Timestamp instrumentation primitives.
//!
//! Timestamp instrumentation allows measuring where the latency of a message accrues along
//! its path: when a message is sent with a [`TimestampInstrumentation`] configuration, every
//! zenoh node it traverses appends a [`TimestampStackRecord`] to its [`TimestampStack`].

use std::sync::Arc;

use uhlc::{HLCBuilder, HLC};
#[zenoh_macros::unstable]
use zenoh_config::wrappers::ZenohId;
#[zenoh_macros::unstable]
use zenoh_protocol::network::ext::interception_point;
use zenoh_protocol::{
    core::{WhatAmI, ZenohIdProto},
    network::ext::{
        TimestampStackRecord as TimestampStackRecordProto, TimestampStackValue,
        TS_STACK_MAX_RECORDS,
    },
};
#[zenoh_macros::unstable]
use zenoh_result::{bail, ZResult};

/// The timestamp stack as carried by the network messages.
pub(crate) type TimestampStackProto = zenoh_protocol::network::push::ext::TimestampStackType;

/// The point of the path of a message at which a timestamp is taken.
#[zenoh_macros::unstable]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InterceptionPoint {
    /// The message is sent by an application.
    Send,
    /// The message is routed by a zenoh node.
    Route,
    /// The message is received by an application.
    Receive,
}

#[zenoh_macros::unstable]
impl TryFrom<u8> for InterceptionPoint {
    type Error = zenoh_result::Error;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value & !zenoh_protocol::network::ext::TS_STACK_CUSTOM_FLAG {
            interception_point::SEND => Ok(InterceptionPoint::Send),
            interception_point::ROUTE => Ok(InterceptionPoint::Route),
            interception_point::RECEIVE => Ok(InterceptionPoint::Receive),
            _ => bail!("Invalid interception point: {}", value),
        }
    }
}

#[zenoh_macros::unstable]
impl From<InterceptionPoint> for u8 {
    fn from(value: InterceptionPoint) -> Self {
        match value {
            InterceptionPoint::Send => interception_point::SEND,
            InterceptionPoint::Route => interception_point::ROUTE,
            InterceptionPoint::Receive => interception_point::RECEIVE,
        }
    }
}

/// A builder for a [`TimestampInstrumentation`] configuration.
///
/// # Examples
/// ```
/// # fn main() {
/// use zenoh::timestamp_stack::TimestampInstrumentationBuilder;
///
/// let instrumentation = TimestampInstrumentationBuilder::new()
///     .set_send(true)
///     .set_receive(true)
///     .build()
///     .unwrap();
/// # }
/// ```
#[zenoh_macros::unstable]
#[derive(Debug, Default, Clone, Copy)]
pub struct TimestampInstrumentationBuilder {
    flags: u8,
}

#[zenoh_macros::unstable]
impl TimestampInstrumentationBuilder {
    /// Creates a new builder with no interception point enabled.
    pub fn new() -> Self {
        Self::default()
    }

    fn set(mut self, point: u8, enabled: bool) -> Self {
        if enabled {
            self.flags |= point;
        } else {
            self.flags &= !point;
        }
        self
    }

    /// Enables or disables the [`InterceptionPoint::Send`] interception point.
    pub fn set_send(self, enabled: bool) -> Self {
        self.set(interception_point::SEND, enabled)
    }

    /// Enables or disables the [`InterceptionPoint::Route`] interception point.
    pub fn set_route(self, enabled: bool) -> Self {
        self.set(interception_point::ROUTE, enabled)
    }

    /// Enables or disables the [`InterceptionPoint::Receive`] interception point.
    pub fn set_receive(self, enabled: bool) -> Self {
        self.set(interception_point::RECEIVE, enabled)
    }

    /// Builds the [`TimestampInstrumentation`] configuration.
    ///
    /// Fails if no interception point has been enabled.
    pub fn build(self) -> ZResult<TimestampInstrumentation> {
        if self.flags == 0 {
            bail!("At least one interception point must be enabled");
        }
        Ok(TimestampInstrumentation { flags: self.flags })
    }
}

/// The timestamp instrumentation configuration of a message.
///
/// It is built with a [`TimestampInstrumentationBuilder`] and travels unchanged
/// along the path of the message.
#[zenoh_macros::unstable]
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct TimestampInstrumentation {
    flags: u8,
}

#[zenoh_macros::unstable]
impl TimestampInstrumentation {
    /// Returns `true` if the given [`InterceptionPoint`] is instrumented.
    pub fn is_instrumented(&self, point: InterceptionPoint) -> bool {
        self.flags & u8::from(point) != 0
    }

    pub(crate) fn flags(&self) -> u8 {
        self.flags
    }

    pub(crate) fn from_flags(flags: u8) -> Self {
        Self { flags }
    }
}

/// The timestamp held by a [`TimestampStackRecord`].
#[zenoh_macros::unstable]
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InstrumentationTimestamp {
    /// A timestamp generated by the hybrid logical clock of the zenoh node.
    UHLC(uhlc::Timestamp),
    /// An opaque timestamp generated by a user provided callback.
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
    /// The [`InterceptionPoint`] at which this record has been taken.
    pub fn point(&self) -> InterceptionPoint {
        self.point
    }

    /// Returns `true` if the timestamp of this record has been generated by a user provided callback.
    pub fn is_custom(&self) -> bool {
        matches!(self.timestamp, InstrumentationTimestamp::Custom(_))
    }

    /// The timestamp of this record.
    pub fn timestamp(&self) -> &InstrumentationTimestamp {
        &self.timestamp
    }
}

/// The stack of timestamps accumulated by a message along its path.
#[zenoh_macros::unstable]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStack {
    instrumentation: TimestampInstrumentation,
    records: Vec<TimestampStackRecord>,
}

#[zenoh_macros::unstable]
impl TimestampStack {
    /// The [`TimestampInstrumentation`] configuration used by the sender of the message.
    pub fn instrumentation(&self) -> TimestampInstrumentation {
        self.instrumentation
    }

    /// The records accumulated along the path of the message, in traversal order.
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

#[zenoh_macros::unstable]
impl From<&TimestampStackProto> for TimestampStack {
    fn from(value: &TimestampStackProto) -> Self {
        let records = value
            .records
            .iter()
            .filter_map(|r| {
                Some(TimestampStackRecord {
                    point: InterceptionPoint::try_from(r.point).ok()?,
                    timestamp: match &r.timestamp {
                        TimestampStackValue::Uhlc(ts) => InstrumentationTimestamp::UHLC(*ts),
                        TimestampStackValue::Custom(bs) => {
                            InstrumentationTimestamp::Custom(bs.clone())
                        }
                    },
                })
            })
            .collect();
        Self {
            instrumentation: TimestampInstrumentation::from_flags(value.instrumentation),
            records,
        }
    }
}

#[zenoh_macros::unstable]
impl From<TimestampStackProto> for TimestampStack {
    fn from(value: TimestampStackProto) -> Self {
        let instrumentation = TimestampInstrumentation::from_flags(value.instrumentation);
        let records = value
            .records
            .into_iter()
            .filter_map(|r| {
                Some(TimestampStackRecord {
                    point: InterceptionPoint::try_from(r.point).ok()?,
                    timestamp: match r.timestamp {
                        TimestampStackValue::Uhlc(ts) => InstrumentationTimestamp::UHLC(ts),
                        TimestampStackValue::Custom(bs) => InstrumentationTimestamp::Custom(bs),
                    },
                })
            })
            .collect();
        Self {
            instrumentation,
            records,
        }
    }
}

#[zenoh_macros::unstable]
impl From<TimestampInstrumentation> for TimestampStackProto {
    fn from(value: TimestampInstrumentation) -> Self {
        TimestampStackProto::empty(value.flags())
    }
}

#[zenoh_macros::unstable]
impl From<&TimestampStack> for TimestampStackProto {
    fn from(value: &TimestampStack) -> Self {
        let records = value
            .records
            .iter()
            .map(|r| TimestampStackRecordProto {
                point: u8::from(r.point),
                timestamp: match &r.timestamp {
                    InstrumentationTimestamp::UHLC(ts) => TimestampStackValue::Uhlc(*ts),
                    InstrumentationTimestamp::Custom(bs) => TimestampStackValue::Custom(bs.clone()),
                },
            })
            .collect();
        TimestampStackProto {
            instrumentation: value.instrumentation.flags(),
            records,
        }
    }
}

/// The context passed to the timestamp callback of a zenoh node.
///
/// See [`OpenBuilder::with_timestamp_callback`](crate::session::OpenBuilder::with_timestamp_callback).
#[zenoh_macros::unstable]
#[non_exhaustive]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TimestampContext {
    /// The [`ZenohId`] of the node generating the timestamp.
    pub zid: ZenohId,
    /// The mode of the node generating the timestamp.
    pub whatami: WhatAmI,
}

/// The callback generating custom timestamps for a zenoh node.
#[zenoh_macros::unstable]
pub(crate) type TimestampCallback = Arc<dyn Fn(TimestampContext) -> Vec<u8> + Send + Sync>;

/// The node local state used to instrument messages.
pub(crate) struct TimestampInstrumenter {
    zid: ZenohIdProto,
    #[cfg_attr(not(feature = "unstable"), allow(dead_code))]
    whatami: WhatAmI,
    hlc: Arc<HLC>,
    #[cfg(feature = "unstable")]
    callback: Option<TimestampCallback>,
}

impl std::fmt::Debug for TimestampInstrumenter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TimestampInstrumenter")
            .field("zid", &self.zid)
            .field("whatami", &self.whatami)
            .finish_non_exhaustive()
    }
}

impl TimestampInstrumenter {
    pub(crate) fn new(
        zid: ZenohIdProto,
        whatami: WhatAmI,
        hlc: Option<Arc<HLC>>,
        #[cfg(feature = "unstable")] callback: Option<TimestampCallback>,
    ) -> Self {
        // NOTE: the timestamps of the instrumentation records are always generated by an HLC
        // identified by the zid of the node, whether or not timestamping is enabled.
        let hlc = hlc
            .unwrap_or_else(|| Arc::new(HLCBuilder::new().with_id(uhlc::ID::from(&zid)).build()));
        Self {
            zid,
            whatami,
            hlc,
            #[cfg(feature = "unstable")]
            callback,
        }
    }

    /// Generates a new timestamp for this node.
    fn timestamp(&self) -> TimestampStackValue {
        #[cfg(feature = "unstable")]
        if let Some(callback) = self.callback.as_ref() {
            return TimestampStackValue::Custom(callback(TimestampContext {
                zid: self.zid.into(),
                whatami: self.whatami,
            }));
        }
        TimestampStackValue::Uhlc(self.hlc.new_timestamp())
    }

    /// Appends a record to the stack of a message if the given interception point is instrumented.
    pub(crate) fn intercept(&self, stack: Option<&mut TimestampStackProto>, point: u8) {
        let Some(stack) = stack else {
            return;
        };
        if !stack.is_instrumented(point) || stack.records.len() >= TS_STACK_MAX_RECORDS {
            return;
        }
        let timestamp = self.timestamp();
        if matches!(&timestamp, TimestampStackValue::Custom(bs) if bs.is_empty()) {
            // An empty custom timestamp means that no record should be added.
            return;
        }
        stack
            .records
            .push(TimestampStackRecordProto::new(point, timestamp));
    }

    /// Builds the stack of a message sent with the given instrumentation configuration.
    ///
    /// A record is appended for the given interception point.
    #[cfg(feature = "unstable")]
    pub(crate) fn new_stack(
        &self,
        instrumentation: Option<TimestampInstrumentation>,
        point: u8,
    ) -> Option<TimestampStackProto> {
        // NOTE: a configuration without any interception point is not encodable, hence such
        // messages are not instrumented at all.
        let mut stack = instrumentation
            .filter(|i| i.flags() != 0)
            .map(TimestampStackProto::from);
        self.intercept(stack.as_mut(), point);
        stack
    }
}
