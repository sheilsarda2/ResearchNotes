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

//! Helper to append a `Route` record to the timestamp stack extension of a network message
//! being forwarded by this node's routing layer.
#[cfg(feature = "unstable")]
use super::tables::TablesData;

/// Appends a `Route` record (if enabled by the instrumentation configuration carried by the
/// message) to the timestamp stack extension, once per node whose routing layer forwards the
/// message.
#[cfg(feature = "unstable")]
pub(crate) fn append_route_record<const ID: u8>(
    ext_ts_stack: &mut Option<zenoh_protocol::network::ext::TsStackType<ID>>,
    tables: &TablesData,
) {
    use crate::api::timestamp_stack::{ts_stack_from_wire, ts_stack_to_wire, InterceptionPoint};

    let Some(wire) = ext_ts_stack.take() else {
        return;
    };
    let Some(mut stack) = ts_stack_from_wire(wire) else {
        return;
    };
    if let Some(rt) = tables.runtime.as_ref().and_then(|w| w.upgrade()) {
        stack.append(
            InterceptionPoint::Route,
            rt.zid(),
            rt.whatami(),
            rt.ts_stack_hlc(),
            rt.ts_stack_callback().as_ref(),
        );
    }
    *ext_ts_stack = Some(ts_stack_to_wire(&stack));
}
