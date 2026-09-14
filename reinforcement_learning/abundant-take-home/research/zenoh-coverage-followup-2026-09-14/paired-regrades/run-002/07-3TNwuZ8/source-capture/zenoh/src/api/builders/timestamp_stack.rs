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

use crate::api::timestamp_stack::TimestampInstrumentation;

/// Allows setting the [`TimestampInstrumentation`] configuration on a builder.
#[zenoh_macros::unstable]
pub trait TimestampInstrumentationBuilderTrait {
    /// Sets or clears the [`TimestampInstrumentation`] configuration to be used to instrument
    /// the message with a timestamp stack.
    fn timestamp_instrumentation<TS: Into<Option<TimestampInstrumentation>>>(
        self,
        timestamp_instrumentation: TS,
    ) -> Self;
}
