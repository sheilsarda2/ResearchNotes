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
use alloc::{vec, vec::Vec};

use zenoh_result::{bail, ZResult};

/// The maximum number of records a [`TimestampStack`] can hold.
pub const MAX_TIMESTAMP_STACK_RECORDS: usize = 255;

/// Flag set in a record when the timestamp is expressed in a custom format.
pub const CUSTOM_TIMESTAMP_FLAG: u8 = 1 << 7;

/// The point of a Zenoh node where a timestamp can be recorded.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InterceptionPoint {
    /// The application sending a publication, a query or a reply.
    Send = 0b001,
    /// The routing layer of a Zenoh node.
    Route = 0b010,
    /// The application receiving a publication, a query or a reply.
    Receive = 0b100,
}

impl TryFrom<u8> for InterceptionPoint {
    type Error = zenoh_result::Error;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value & !CUSTOM_TIMESTAMP_FLAG {
            0b001 => Ok(InterceptionPoint::Send),
            0b010 => Ok(InterceptionPoint::Route),
            0b100 => Ok(InterceptionPoint::Receive),
            v => bail!("Invalid interception point: {v}"),
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
    pub fn new() -> Self {
        Self::default()
    }

    fn set(mut self, point: InterceptionPoint, value: bool) -> Self {
        if value {
            self.flags |= point as u8;
        } else {
            self.flags &= !(point as u8);
        }
        self
    }

    /// Enable or disable the recording of a timestamp at the [`InterceptionPoint::Send`] point.
    pub fn set_send(self, value: bool) -> Self {
        self.set(InterceptionPoint::Send, value)
    }

    /// Enable or disable the recording of a timestamp at the [`InterceptionPoint::Route`] point.
    pub fn set_route(self, value: bool) -> Self {
        self.set(InterceptionPoint::Route, value)
    }

    /// Enable or disable the recording of a timestamp at the [`InterceptionPoint::Receive`] point.
    pub fn set_receive(self, value: bool) -> Self {
        self.set(InterceptionPoint::Receive, value)
    }

    /// Build the [`TimestampInstrumentation`].
    ///
    /// Fails if no interception point has been enabled.
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
    /// Returns `true` if a timestamp must be recorded at the given interception point.
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
            bail!("Invalid timestamp instrumentation flags: {flags}");
        }
        Ok(Self { flags })
    }

    #[cfg(feature = "test")]
    #[doc(hidden)]
    pub fn rand() -> Self {
        use rand::Rng;
        let mut rng = rand::thread_rng();
        Self {
            flags: rng.gen_range(1..8),
        }
    }
}

/// The timestamp recorded at an interception point.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InstrumentationTimestamp {
    /// A timestamp taken from the hybrid logical clock of the node.
    UHLC(uhlc::Timestamp),
    /// A timestamp in a custom, user-defined format.
    Custom(Vec<u8>),
}

/// A record of a [`TimestampStack`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStackRecord {
    point: InterceptionPoint,
    timestamp: InstrumentationTimestamp,
}

impl TimestampStackRecord {
    pub fn new(point: InterceptionPoint, timestamp: InstrumentationTimestamp) -> Self {
        Self { point, timestamp }
    }

    /// The interception point where this record was taken.
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
                uhlc::ID::try_from(crate::core::ZenohIdProto::rand().to_le_bytes()).unwrap(),
            ))
        } else {
            let len = rng.gen_range(1..16);
            InstrumentationTimestamp::Custom((0..len).map(|_| rng.gen()).collect())
        };
        Self { point, timestamp }
    }
}

/// The stack of timestamps recorded along the path of a message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStack {
    instrumentation: TimestampInstrumentation,
    records: Vec<TimestampStackRecord>,
}

impl TimestampStack {
    pub fn new(instrumentation: TimestampInstrumentation) -> Self {
        Self {
            instrumentation,
            records: vec![],
        }
    }

    #[doc(hidden)]
    pub fn with_records(
        instrumentation: TimestampInstrumentation,
        records: Vec<TimestampStackRecord>,
    ) -> Self {
        Self {
            instrumentation,
            records,
        }
    }

    /// The instrumentation configuration used by the sender of the message.
    pub fn instrumentation(&self) -> TimestampInstrumentation {
        self.instrumentation
    }

    /// The records of this stack, in traversal order.
    pub fn records(&self) -> &[TimestampStackRecord] {
        &self.records
    }

    /// Appends a record to this stack, if it is not full.
    #[doc(hidden)]
    pub fn push(&mut self, record: TimestampStackRecord) {
        if self.records.len() < MAX_TIMESTAMP_STACK_RECORDS {
            self.records.push(record);
        }
    }

    /// Returns `true` if a timestamp must be recorded at the given interception point.
    #[doc(hidden)]
    pub fn is_instrumented(&self, point: InterceptionPoint) -> bool {
        self.instrumentation.is_instrumented(point)
    }

    #[cfg(feature = "test")]
    #[doc(hidden)]
    pub fn rand() -> Self {
        use rand::Rng;
        let mut rng = rand::thread_rng();
        let n = rng.gen_range(0..4);
        Self {
            instrumentation: TimestampInstrumentation::rand(),
            records: (0..n).map(|_| TimestampStackRecord::rand()).collect(),
        }
    }
}
