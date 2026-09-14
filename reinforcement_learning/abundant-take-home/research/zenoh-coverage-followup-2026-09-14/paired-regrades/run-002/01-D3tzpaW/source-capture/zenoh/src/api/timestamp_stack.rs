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

//! Timestamp instrumentation for latency measurement.

use std::{fmt, sync::Arc};

use uhlc::HLC;
use zenoh_config::{wrappers::ZenohId, WhatAmI};
use zenoh_protocol::core::ZenohIdProto;
pub use zenoh_protocol::network::ext::{
    InstrumentationTimestamp, InterceptionPoint, TimestampInstrumentation, TimestampStackRecord,
};
use zenoh_protocol::network::ext::TimestampStackType;
use zenoh_result::{bail, ZResult};

/// A builder for a [`TimestampInstrumentation`] configuration.
///
/// The configuration selects the [`InterceptionPoint`]s at which the nodes traversed by a
/// message record a timestamp in its [`TimestampStack`].
///
/// # Examples
/// ```
/// # #[cfg(feature = "unstable")]
/// # {
/// use zenoh::timestamp_stack::TimestampInstrumentationBuilder;
///
/// let instrumentation = TimestampInstrumentationBuilder::new()
///     .set_send(true)
///     .set_route(true)
///     .set_receive(true)
///     .build()
///     .unwrap();
/// # }
/// ```
#[derive(Debug, Default, Clone, Copy)]
pub struct TimestampInstrumentationBuilder {
    inner: TimestampInstrumentation,
}

impl TimestampInstrumentationBuilder {
    /// Creates a builder with no interception point enabled.
    pub fn new() -> Self {
        Self::default()
    }

    /// Enables or disables the [`InterceptionPoint::Send`] interception point.
    pub fn set_send(mut self, enabled: bool) -> Self {
        self.inner.set(InterceptionPoint::Send, enabled);
        self
    }

    /// Enables or disables the [`InterceptionPoint::Route`] interception point.
    pub fn set_route(mut self, enabled: bool) -> Self {
        self.inner.set(InterceptionPoint::Route, enabled);
        self
    }

    /// Enables or disables the [`InterceptionPoint::Receive`] interception point.
    pub fn set_receive(mut self, enabled: bool) -> Self {
        self.inner.set(InterceptionPoint::Receive, enabled);
        self
    }

    /// Builds the [`TimestampInstrumentation`].
    ///
    /// Fails if no interception point is enabled.
    pub fn build(self) -> ZResult<TimestampInstrumentation> {
        if self.inner.is_empty() {
            bail!("At least one interception point must be enabled in a timestamp instrumentation");
        }
        Ok(self.inner)
    }
}

/// The ordered list of timestamps recorded along the path of a message.
///
/// Each [`TimestampStackRecord`] states the [`InterceptionPoint`] at which it was recorded
/// and the timestamp taken there. Records are appended in traversal order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStack {
    pub(crate) instrumentation: TimestampInstrumentation,
    pub(crate) records: Vec<TimestampStackRecord>,
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

    /// The records of this stack, in traversal order.
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

/// The context passed to a timestamp callback (see
/// [`OpenBuilder::with_timestamp_callback`](crate::session::OpenBuilder::with_timestamp_callback)).
#[non_exhaustive]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TimestampContext {
    /// The [`ZenohId`] of the node recording the timestamp.
    pub zid: ZenohId,
    /// The mode of the node recording the timestamp.
    pub whatami: WhatAmI,
}

/// A user-provided function producing custom timestamps.
pub(crate) type TimestampCallback = Arc<dyn Fn(TimestampContext) -> Vec<u8> + Send + Sync>;

/// The node-level state used to append records to timestamp stacks.
pub(crate) struct TimestampRecorder {
    zid: ZenohIdProto,
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
        zid: ZenohIdProto,
        whatami: WhatAmI,
        hlc: Option<Arc<HLC>>,
        callback: Option<TimestampCallback>,
    ) -> Self {
        let hlc = hlc.unwrap_or_else(|| {
            Arc::new(
                uhlc::HLCBuilder::new()
                    .with_id(uhlc::ID::from(&zid))
                    .build(),
            )
        });
        Self {
            zid,
            whatami,
            hlc,
            callback,
        }
    }

    /// Produces the timestamp of a record taken by this node.
    fn timestamp(&self) -> Option<InstrumentationTimestamp> {
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

    /// Appends a record for the given interception point to the stack,
    /// if the point is enabled in the stack configuration and the stack is not full.
    #[inline]
    pub(crate) fn record<const ID: u8>(
        &self,
        stack: &mut TimestampStackType<{ ID }>,
        point: InterceptionPoint,
    ) {
        if !stack.instrumentation.is_instrumented(point) || !stack.can_push() {
            return;
        }
        if let Some(timestamp) = self.timestamp() {
            stack.push(TimestampStackRecord::new(point, timestamp));
        }
    }

    /// Creates a new stack with the given configuration and records the given point in it.
    pub(crate) fn new_stack<const ID: u8>(
        &self,
        instrumentation: TimestampInstrumentation,
        point: InterceptionPoint,
    ) -> TimestampStackType<{ ID }> {
        let mut stack = TimestampStackType::new(instrumentation);
        self.record(&mut stack, point);
        stack
    }
}
