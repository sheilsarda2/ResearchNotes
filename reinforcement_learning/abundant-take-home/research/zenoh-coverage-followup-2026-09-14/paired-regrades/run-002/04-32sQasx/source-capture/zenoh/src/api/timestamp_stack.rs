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
//! Timestamp instrumentation allows measuring where the latency accrues along the path
//! of a message.

use std::sync::Arc;

use uhlc::{HLCBuilder, HLC};
use zenoh_config::wrappers::ZenohId;
#[allow(unused_imports)]
pub use zenoh_protocol::network::ts_stack::{
    InstrumentationTimestamp, InterceptionPoint, TimestampInstrumentation,
    TimestampInstrumentationBuilder, TimestampStack, TimestampStackRecord,
};
use zenoh_protocol::{
    core::{WhatAmI, ZenohIdProto},
    network::ts_stack::MAX_TIMESTAMP_STACK_LEN,
};

/// The context passed to a timestamp callback.
///
/// See [`OpenBuilder::with_timestamp_callback`](crate::session::OpenBuilder::with_timestamp_callback).
#[non_exhaustive]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TimestampContext {
    /// The [`ZenohId`] of the node taking the timestamp.
    pub zid: ZenohId,
    /// The mode of the node taking the timestamp.
    pub whatami: WhatAmI,
}

/// The type of the user provided timestamp callback.
pub(crate) type TimestampCallback = Arc<dyn Fn(TimestampContext) -> Vec<u8> + Send + Sync>;

/// The entity in charge of taking the timestamps of a node.
#[allow(dead_code)]
pub(crate) struct Instrumenter {
    context: TimestampContext,
    hlc: Arc<HLC>,
    callback: Option<TimestampCallback>,
}

impl std::fmt::Debug for Instrumenter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Instrumenter")
            .field("context", &self.context)
            .field("custom", &self.callback.is_some())
            .finish()
    }
}

#[allow(dead_code)]
impl Instrumenter {
    pub(crate) fn new(
        zid: ZenohIdProto,
        whatami: WhatAmI,
        hlc: Option<Arc<HLC>>,
        callback: Option<TimestampCallback>,
    ) -> Self {
        let hlc = hlc
            .unwrap_or_else(|| Arc::new(HLCBuilder::new().with_id(uhlc::ID::from(&zid)).build()));
        Self {
            context: TimestampContext {
                zid: zid.into(),
                whatami,
            },
            hlc,
            callback,
        }
    }

    fn timestamp(&self) -> Option<InstrumentationTimestamp> {
        match &self.callback {
            Some(callback) => {
                let bytes = callback(self.context);
                (!bytes.is_empty()).then_some(InstrumentationTimestamp::Custom(bytes))
            }
            None => Some(InstrumentationTimestamp::UHLC(self.hlc.new_timestamp())),
        }
    }

    /// Appends a record for the given interception point to the stack, if the stack is
    /// instrumented for this point.
    pub(crate) fn instrument(
        &self,
        stack: &mut Option<TimestampStack>,
        point: InterceptionPoint,
    ) -> bool {
        let Some(stack) = stack.as_mut() else {
            return false;
        };
        if !stack.instrumentation().is_instrumented(point) {
            return false;
        }
        if stack.records().len() >= MAX_TIMESTAMP_STACK_LEN {
            return false;
        }
        let Some(timestamp) = self.timestamp() else {
            return false;
        };
        stack.push_record(TimestampStackRecord::new(point, timestamp));
        true
    }

    /// Returns a copy of the given stack with a record for the given interception point appended.
    pub(crate) fn instrumented(
        &self,
        stack: &Option<TimestampStack>,
        point: InterceptionPoint,
    ) -> Option<TimestampStack> {
        let mut stack = stack.clone();
        self.instrument(&mut stack, point);
        stack
    }
}
