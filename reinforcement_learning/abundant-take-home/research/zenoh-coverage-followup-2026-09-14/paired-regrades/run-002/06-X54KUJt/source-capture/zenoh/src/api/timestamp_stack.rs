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
use std::sync::Arc;

use uhlc::{HLCBuilder, HLC};
use zenoh_config::{wrappers::ZenohId, WhatAmI};
#[allow(unused_imports)]
pub use zenoh_protocol::core::timestamp_stack::{
    InstrumentationTimestamp, InterceptionPoint, TimestampInstrumentation,
    TimestampInstrumentationBuilder, TimestampStack, TimestampStackRecord,
};
use zenoh_protocol::core::ZenohIdProto;

/// The context passed to a timestamp callback when a timestamp must be recorded.
#[non_exhaustive]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TimestampContext {
    /// The [`ZenohId`] of the node recording the timestamp.
    pub zid: ZenohId,
    /// The mode of the node recording the timestamp.
    pub whatami: WhatAmI,
}

pub(crate) type TimestampCallback = Arc<dyn Fn(TimestampContext) -> Vec<u8> + Send + Sync>;

/// Records timestamps on behalf of a Zenoh node.
pub(crate) struct TimestampProvider {
    context: TimestampContext,
    hlc: HLC,
    callback: Option<TimestampCallback>,
}

impl std::fmt::Debug for TimestampProvider {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TimestampProvider")
            .field("context", &self.context)
            .finish_non_exhaustive()
    }
}

impl TimestampProvider {
    pub(crate) fn new(
        zid: ZenohIdProto,
        whatami: WhatAmI,
        callback: Option<TimestampCallback>,
    ) -> Self {
        let hlc = HLCBuilder::new()
            .with_id(uhlc::ID::from(&zid))
            .build();
        Self {
            context: TimestampContext {
                zid: zid.into(),
                whatami,
            },
            hlc,
            callback,
        }
    }

    fn record(&self, point: InterceptionPoint) -> Option<TimestampStackRecord> {
        let timestamp = match &self.callback {
            Some(cb) => {
                let bytes = cb(self.context);
                if bytes.is_empty() {
                    return None;
                }
                InstrumentationTimestamp::Custom(bytes)
            }
            None => InstrumentationTimestamp::UHLC(self.hlc.new_timestamp()),
        };
        Some(TimestampStackRecord::new(point, timestamp))
    }

    /// Appends a record to the given stack, if any, and if the given interception point
    /// is enabled in its instrumentation configuration.
    pub(crate) fn instrument(
        &self,
        stack: &mut Option<TimestampStack>,
        point: InterceptionPoint,
    ) {
        if let Some(stack) = stack.as_mut() {
            if stack.instrumentation().is_instrumented(point) {
                if let Some(record) = self.record(point) {
                    stack.push(record);
                }
            }
        }
    }

    /// Returns a copy of the given stack with a record appended for the given interception point.
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
