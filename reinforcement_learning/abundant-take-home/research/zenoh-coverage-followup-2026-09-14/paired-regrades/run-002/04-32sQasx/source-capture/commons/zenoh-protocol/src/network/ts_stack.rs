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

//! Timestamp instrumentation: timestamp stack carried by network messages.

use alloc::vec::Vec;

use zenoh_result::{bail, ZResult};

/// The flag set in a record header when the timestamp is in a custom format.
pub const CUSTOM_TIMESTAMP_FLAG: u8 = 1 << 7;

/// The maximum number of records a [`TimestampStack`] may contain.
pub const MAX_TIMESTAMP_STACK_LEN: usize = u8::MAX as usize;

/// The point of a message path at which a timestamp may be taken.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InterceptionPoint {
    /// The message is sent by an application.
    Send = 0b001,
    /// The message is routed by a zenoh node.
    Route = 0b010,
    /// The message is received by an application.
    Receive = 0b100,
}

impl TryFrom<u8> for InterceptionPoint {
    type Error = zenoh_result::Error;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value & !CUSTOM_TIMESTAMP_FLAG {
            0b001 => Ok(InterceptionPoint::Send),
            0b010 => Ok(InterceptionPoint::Route),
            0b100 => Ok(InterceptionPoint::Receive),
            other => bail!("Invalid interception point: {}", other),
        }
    }
}

impl From<InterceptionPoint> for u8 {
    fn from(value: InterceptionPoint) -> Self {
        value as u8
    }
}

/// A builder for [`TimestampInstrumentation`].
#[derive(Debug, Default, Clone, Copy)]
pub struct TimestampInstrumentationBuilder {
    flags: u8,
}

impl TimestampInstrumentationBuilder {
    /// Creates a new builder, with no interception point enabled.
    pub fn new() -> Self {
        Self::default()
    }

    fn set(mut self, point: InterceptionPoint, enable: bool) -> Self {
        let flag = point as u8;
        if enable {
            self.flags |= flag;
        } else {
            self.flags &= !flag;
        }
        self
    }

    /// Enables or disables the [`InterceptionPoint::Send`] interception point.
    pub fn set_send(self, enable: bool) -> Self {
        self.set(InterceptionPoint::Send, enable)
    }

    /// Enables or disables the [`InterceptionPoint::Route`] interception point.
    pub fn set_route(self, enable: bool) -> Self {
        self.set(InterceptionPoint::Route, enable)
    }

    /// Enables or disables the [`InterceptionPoint::Receive`] interception point.
    pub fn set_receive(self, enable: bool) -> Self {
        self.set(InterceptionPoint::Receive, enable)
    }

    /// Builds the [`TimestampInstrumentation`].
    ///
    /// It fails if no interception point has been enabled.
    pub fn build(self) -> ZResult<TimestampInstrumentation> {
        if self.flags == 0 {
            bail!("At least one interception point must be enabled");
        }
        Ok(TimestampInstrumentation { flags: self.flags })
    }
}

/// The configuration of the timestamp instrumentation of a message.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct TimestampInstrumentation {
    flags: u8,
}

impl TimestampInstrumentation {
    /// Returns `true` if the given interception point is enabled.
    pub fn is_instrumented(&self, point: InterceptionPoint) -> bool {
        self.flags & (point as u8) != 0
    }

    #[doc(hidden)]
    pub fn flags(&self) -> u8 {
        self.flags
    }

    #[doc(hidden)]
    pub fn try_from_flags(flags: u8) -> ZResult<Self> {
        if flags == 0 {
            bail!("Invalid timestamp instrumentation flags: {}", flags);
        }
        Ok(Self { flags })
    }

    #[cfg(feature = "test")]
    #[doc(hidden)]
    pub fn rand() -> Self {
        use rand::Rng;
        let mut rng = rand::thread_rng();
        Self {
            flags: rng.gen_range(1..=0b111),
        }
    }
}

/// A timestamp of a [`TimestampStackRecord`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InstrumentationTimestamp {
    /// A timestamp taken from the hybrid logical clock of the node.
    UHLC(uhlc::Timestamp),
    /// A timestamp in a custom format, provided by the application.
    Custom(Vec<u8>),
}

/// A record of a [`TimestampStack`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStackRecord {
    point: InterceptionPoint,
    timestamp: InstrumentationTimestamp,
}

impl TimestampStackRecord {
    /// Creates a new record.
    pub fn new(point: InterceptionPoint, timestamp: InstrumentationTimestamp) -> Self {
        Self { point, timestamp }
    }

    /// The interception point at which this record has been taken.
    pub fn point(&self) -> InterceptionPoint {
        self.point
    }

    /// Returns `true` if the timestamp of this record is in a custom format.
    pub fn is_custom(&self) -> bool {
        matches!(self.timestamp, InstrumentationTimestamp::Custom(_))
    }

    /// The timestamp of this record.
    pub fn timestamp(&self) -> &InstrumentationTimestamp {
        &self.timestamp
    }

    #[cfg(feature = "test")]
    #[doc(hidden)]
    pub fn rand() -> Self {
        use rand::Rng;
        let mut rng = rand::thread_rng();
        let point = match rng.gen_range(0..3) {
            0 => InterceptionPoint::Send,
            1 => InterceptionPoint::Route,
            _ => InterceptionPoint::Receive,
        };
        let timestamp = if rng.gen_bool(0.5) {
            InstrumentationTimestamp::UHLC(uhlc::Timestamp::new(
                uhlc::NTP64(rng.gen()),
                uhlc::ID::try_from([rng.gen::<u8>().max(1); 4].as_slice()).unwrap(),
            ))
        } else {
            let len: usize = rng.gen_range(0..16);
            let mut payload = alloc::vec![0u8; len];
            rng.fill(&mut payload[..]);
            InstrumentationTimestamp::Custom(payload)
        };
        Self { point, timestamp }
    }
}

/// The stack of timestamps carried by an instrumented message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStack {
    instrumentation: TimestampInstrumentation,
    records: Vec<TimestampStackRecord>,
}

impl TimestampStack {
    /// Creates a new empty stack with the given instrumentation configuration.
    pub fn new(instrumentation: TimestampInstrumentation) -> Self {
        Self {
            instrumentation,
            records: Vec::new(),
        }
    }

    /// Creates a new stack with the given instrumentation configuration and records.
    pub fn with_records(
        instrumentation: TimestampInstrumentation,
        records: Vec<TimestampStackRecord>,
    ) -> Self {
        Self {
            instrumentation,
            records,
        }
    }

    /// The instrumentation configuration set by the sender of the message.
    pub fn instrumentation(&self) -> TimestampInstrumentation {
        self.instrumentation
    }

    /// The records of this stack, in traversal order.
    pub fn records(&self) -> &[TimestampStackRecord] {
        &self.records
    }

    /// Appends a record to this stack, if the maximum number of records is not reached.
    pub fn push_record(&mut self, record: TimestampStackRecord) {
        if self.records.len() < MAX_TIMESTAMP_STACK_LEN {
            self.records.push(record);
        }
    }

    /// Returns `true` if the given interception point is enabled.
    pub fn is_instrumented(&self, point: InterceptionPoint) -> bool {
        self.instrumentation.is_instrumented(point)
    }

    #[cfg(feature = "test")]
    #[doc(hidden)]
    pub fn rand() -> Self {
        use rand::Rng;
        let mut rng = rand::thread_rng();
        let n = rng.gen_range(0..=3);
        Self {
            instrumentation: TimestampInstrumentation::rand(),
            records: (0..n).map(|_| TimestampStackRecord::rand()).collect(),
        }
    }
}
