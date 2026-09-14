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
//! When a publication, a query or a reply is sent with a [`TimestampInstrumentation`]
//! configuration, the message carries a [`TimestampStack`]: an ordered list of
//! [`TimestampStackRecord`]s, each stating at which [`InterceptionPoint`] it was recorded
//! and the timestamp taken there. Every Zenoh node the message traverses appends its record
//! if the corresponding point is enabled in the configuration.

use std::{fmt, sync::Arc};

use uhlc::{HLCBuilder, HLC};
use zenoh_config::{wrappers::ZenohId, WhatAmI};
#[cfg(feature = "unstable")]
pub use zenoh_protocol::network::ext::TimestampInstrumentationBuilder;
pub use zenoh_protocol::network::ext::{
    InstrumentationTimestamp, InterceptionPoint, TimestampInstrumentation, TimestampStackRecord,
};
use zenoh_protocol::{
    core::ZenohIdProto,
    network::ext::{TimestampStackType, TIMESTAMP_STACK_MAX_RECORDS},
};

/// The stack of timestamps recorded along the path of a message.
///
/// The stack carries the [`TimestampInstrumentation`] configuration set by the sender of the
/// message and the [`TimestampStackRecord`]s appended by the nodes the message traversed,
/// in traversal order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStack {
    instrumentation: TimestampInstrumentation,
    records: Vec<TimestampStackRecord>,
}

impl TimestampStack {
    /// Creates an empty stack with the given instrumentation configuration.
    #[allow(dead_code)] // only used with the `unstable` or `internal` features
    pub(crate) fn new(instrumentation: TimestampInstrumentation) -> Self {
        Self {
            instrumentation,
            records: Vec::new(),
        }
    }

    /// The instrumentation configuration set by the sender of the message.
    pub fn instrumentation(&self) -> TimestampInstrumentation {
        self.instrumentation
    }

    /// The records taken along the path of the message, in traversal order.
    pub fn records(&self) -> &[TimestampStackRecord] {
        &self.records
    }
}

impl<const ID: u8> From<TimestampStackType<{ ID }>> for TimestampStack {
    fn from(value: TimestampStackType<{ ID }>) -> Self {
        TimestampStack {
            instrumentation: value.instrumentation,
            records: value.records,
        }
    }
}

impl<const ID: u8> From<TimestampStack> for TimestampStackType<{ ID }> {
    fn from(value: TimestampStack) -> Self {
        TimestampStackType {
            instrumentation: value.instrumentation,
            records: value.records,
        }
    }
}

/// The context provided to the timestamp callback of a session
/// (see [`OpenBuilder::with_timestamp_callback`](crate::session::OpenBuilder::with_timestamp_callback)).
#[non_exhaustive]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TimestampContext {
    /// The [`ZenohId`] of the node recording the timestamp.
    pub zid: ZenohId,
    /// The mode of the node recording the timestamp.
    pub whatami: WhatAmI,
}

/// A user provided function producing custom timestamps.
pub(crate) type TimestampCallback = Arc<dyn Fn(TimestampContext) -> Vec<u8> + Send + Sync>;

/// Records timestamps into the timestamp stacks of the messages traversing a node.
pub(crate) struct TimestampStackRecorder {
    zid: ZenohIdProto,
    whatami: WhatAmI,
    hlc: Arc<HLC>,
    callback: Option<TimestampCallback>,
}

impl fmt::Debug for TimestampStackRecorder {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("TimestampStackRecorder")
            .field("zid", &self.zid)
            .field("whatami", &self.whatami)
            .field("has_callback", &self.callback.is_some())
            .finish()
    }
}

impl TimestampStackRecorder {
    /// Creates a recorder for the node identified by `zid`.
    ///
    /// If the node has no hybrid logical clock configured, a dedicated one is created so that
    /// records always hold a timestamp whose id is the [`ZenohId`] of the node.
    pub(crate) fn new(
        zid: ZenohIdProto,
        whatami: WhatAmI,
        hlc: Option<Arc<HLC>>,
        callback: Option<TimestampCallback>,
    ) -> Self {
        let hlc = hlc
            .unwrap_or_else(|| Arc::new(HLCBuilder::new().with_id(uhlc::ID::from(&zid)).build()));
        Self {
            zid,
            whatami,
            hlc,
            callback,
        }
    }

    /// Takes a new timestamp for this node.
    ///
    /// Returns `None` if the node has a timestamp callback which returned no bytes.
    pub(crate) fn timestamp(&self) -> Option<InstrumentationTimestamp> {
        match &self.callback {
            Some(callback) => {
                let bytes = callback(TimestampContext {
                    zid: self.zid.into(),
                    whatami: self.whatami,
                });
                (!bytes.is_empty()).then_some(InstrumentationTimestamp::Custom(bytes))
            }
            None => Some(InstrumentationTimestamp::UHLC(self.hlc.new_timestamp())),
        }
    }

    /// Takes a new record for the given interception point.
    #[inline]
    fn new_record(&self, point: InterceptionPoint) -> Option<TimestampStackRecord> {
        self.timestamp()
            .map(|timestamp| TimestampStackRecord::new(point, timestamp))
    }

    /// Appends a record for the given interception point to the stack carried by a message,
    /// if any, and if the point is enabled in its instrumentation configuration.
    #[inline]
    pub(crate) fn record<const ID: u8>(
        &self,
        stack: &mut Option<TimestampStackType<{ ID }>>,
        point: InterceptionPoint,
    ) {
        if let Some(stack) = stack.as_mut() {
            if stack.instrumentation.is_instrumented(point) && !stack.is_full() {
                if let Some(record) = self.new_record(point) {
                    stack.push(record);
                }
            }
        }
    }

    /// Same as [`Self::record`] but for an API-level [`TimestampStack`].
    #[inline]
    pub(crate) fn record_stack(
        &self,
        stack: &mut Option<TimestampStack>,
        point: InterceptionPoint,
    ) {
        if let Some(stack) = stack.as_mut() {
            if stack.instrumentation.is_instrumented(point)
                && stack.records.len() < TIMESTAMP_STACK_MAX_RECORDS
            {
                if let Some(record) = self.new_record(point) {
                    stack.records.push(record);
                }
            }
        }
    }
}
