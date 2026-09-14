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

//! Timestamp stack extension used for latency measurement instrumentation.
//!
//! ```text
//!  7 6 5 4 3 2 1 0
//! +-+-+-+-+-+-+-+-+
//! |Z|1_0|    ID   |
//! +-+-+-+---------+
//! ~    len:z32    ~
//! +---------------+
//! |  conf_flags   |  bit0 Send, bit1 Route, bit2 Receive (must be non-zero)
//! +---------------+
//! %     count     %  number of records (0..=255)
//! +---------------+
//! ~   [records]   ~
//! +---------------+
//!
//! record:
//!  7 6 5 4 3 2 1 0
//! +-+-+-+-+-+-+-+-+
//! |C|0|0|0|0|point|  point: exactly one of 0b001 (Send), 0b010 (Route), 0b100 (Receive)
//! +-+-+-+-+-+-+-+-+  C: custom timestamp format
//! ~   timestamp   ~  <u8;z32>: UHLC timestamp encoding, or custom bytes verbatim
//! +---------------+
//! ```

use alloc::vec::Vec;

use zenoh_result::bail;

/// Bit-level constants of the timestamp stack wire format.
pub mod flag {
    /// Send interception point.
    pub const SEND: u8 = 0b001;
    /// Route interception point.
    pub const ROUTE: u8 = 0b010;
    /// Receive interception point.
    pub const RECEIVE: u8 = 0b100;
    /// Mask of the interception point bits.
    pub const POINT_MASK: u8 = 0b111;
    /// Custom timestamp format flag.
    pub const CUSTOM: u8 = 1 << 7;
}

/// The maximum number of records a timestamp stack can hold.
pub const MAX_RECORDS: usize = 255;

/// A point along the path of a message where a timestamp can be recorded.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum InterceptionPoint {
    /// The application issuing the message.
    Send = flag::SEND,
    /// The routing layer of a node forwarding the message.
    Route = flag::ROUTE,
    /// The application receiving the message.
    Receive = flag::RECEIVE,
}

impl InterceptionPoint {
    /// All the interception points, in traversal order.
    pub const ALL: [InterceptionPoint; 3] = [
        InterceptionPoint::Send,
        InterceptionPoint::Route,
        InterceptionPoint::Receive,
    ];

    #[cfg(feature = "test")]
    #[doc(hidden)]
    pub fn rand() -> Self {
        use rand::seq::SliceRandom;
        *Self::ALL.choose(&mut rand::thread_rng()).unwrap()
    }
}

impl TryFrom<u8> for InterceptionPoint {
    type Error = zenoh_result::Error;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        // The custom format flag (bit 7) is not part of the interception point.
        match value & !flag::CUSTOM {
            flag::SEND => Ok(InterceptionPoint::Send),
            flag::ROUTE => Ok(InterceptionPoint::Route),
            flag::RECEIVE => Ok(InterceptionPoint::Receive),
            unknown => bail!("Unknown interception point: {unknown:#010b}"),
        }
    }
}

impl From<InterceptionPoint> for u8 {
    fn from(value: InterceptionPoint) -> Self {
        value as u8
    }
}

/// The set of [`InterceptionPoint`]s at which timestamps must be recorded.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Hash)]
pub struct TimestampInstrumentation {
    flags: u8,
}

impl TimestampInstrumentation {
    /// Builds an instrumentation configuration from its raw wire flags.
    ///
    /// Only the interception point bits are retained.
    pub const fn from_flags(flags: u8) -> Self {
        Self {
            flags: flags & flag::POINT_MASK,
        }
    }

    /// The raw wire flags of this configuration.
    pub const fn flags(&self) -> u8 {
        self.flags
    }

    /// Returns `true` if no interception point is enabled.
    pub const fn is_empty(&self) -> bool {
        self.flags == 0
    }

    /// Returns `true` if the given interception point is enabled.
    pub const fn is_instrumented(&self, point: InterceptionPoint) -> bool {
        self.flags & (point as u8) != 0
    }

    /// Enables or disables the given interception point.
    pub fn set(&mut self, point: InterceptionPoint, enabled: bool) {
        if enabled {
            self.flags |= point as u8;
        } else {
            self.flags &= !(point as u8);
        }
    }

    #[cfg(feature = "test")]
    #[doc(hidden)]
    pub fn rand() -> Self {
        use rand::Rng;
        let flags: u8 = rand::thread_rng().gen_range(1..=flag::POINT_MASK);
        Self::from_flags(flags)
    }
}

/// The timestamp of a [`TimestampStackRecord`].
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum InstrumentationTimestamp {
    /// A timestamp produced by the hybrid logical clock of the recording node.
    UHLC(uhlc::Timestamp),
    /// Custom bytes produced by the timestamp callback of the recording node.
    Custom(Vec<u8>),
}

impl InstrumentationTimestamp {
    /// Returns `true` if the timestamp is in custom format.
    pub fn is_custom(&self) -> bool {
        matches!(self, InstrumentationTimestamp::Custom(_))
    }

    #[cfg(feature = "test")]
    #[doc(hidden)]
    pub fn rand() -> Self {
        use rand::Rng;
        let mut rng = rand::thread_rng();
        if rng.gen_bool(0.5) {
            let time = uhlc::NTP64(rng.gen());
            let id = uhlc::ID::try_from(crate::core::ZenohIdProto::rand().to_le_bytes()).unwrap();
            InstrumentationTimestamp::UHLC(uhlc::Timestamp::new(time, id))
        } else {
            let len = rng.gen_range(1..=16);
            InstrumentationTimestamp::Custom((0..len).map(|_| rng.gen()).collect())
        }
    }
}

/// A record of a [`TimestampStackType`]: the interception point at which it was recorded
/// and the timestamp taken there.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct TimestampStackRecord {
    pub point: InterceptionPoint,
    pub timestamp: InstrumentationTimestamp,
}

impl TimestampStackRecord {
    /// Builds a new record.
    pub fn new(point: InterceptionPoint, timestamp: InstrumentationTimestamp) -> Self {
        Self { point, timestamp }
    }

    /// The interception point at which this record was taken.
    pub fn point(&self) -> InterceptionPoint {
        self.point
    }

    /// Returns `true` if the timestamp of this record is in custom format.
    pub fn is_custom(&self) -> bool {
        self.timestamp.is_custom()
    }

    /// The timestamp of this record.
    pub fn timestamp(&self) -> &InstrumentationTimestamp {
        &self.timestamp
    }

    /// The wire flags of this record.
    pub fn flags(&self) -> u8 {
        let mut flags = self.point as u8;
        if self.is_custom() {
            flags |= flag::CUSTOM;
        }
        flags
    }

    #[cfg(feature = "test")]
    #[doc(hidden)]
    pub fn rand() -> Self {
        Self {
            point: InterceptionPoint::rand(),
            timestamp: InstrumentationTimestamp::rand(),
        }
    }
}

/// The timestamp stack extension: an instrumentation configuration and the ordered list
/// of records taken along the path of the message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimestampStackType<const ID: u8> {
    pub instrumentation: TimestampInstrumentation,
    pub records: Vec<TimestampStackRecord>,
}

impl<const ID: u8> TimestampStackType<{ ID }> {
    /// Builds an empty stack with the given configuration.
    pub fn new(instrumentation: TimestampInstrumentation) -> Self {
        Self {
            instrumentation,
            records: Vec::new(),
        }
    }

    /// Returns `true` if a record can still be appended to the stack.
    pub fn can_push(&self) -> bool {
        self.records.len() < MAX_RECORDS
    }

    /// Appends a record to the stack, if the stack is not full.
    ///
    /// Returns `true` if the record was appended.
    pub fn push(&mut self, record: TimestampStackRecord) -> bool {
        if self.can_push() {
            self.records.push(record);
            true
        } else {
            false
        }
    }

    /// Converts this extension into another extension id.
    pub fn convert<const OTHER: u8>(self) -> TimestampStackType<{ OTHER }> {
        TimestampStackType {
            instrumentation: self.instrumentation,
            records: self.records,
        }
    }

    #[cfg(feature = "test")]
    #[doc(hidden)]
    pub fn rand() -> Self {
        use rand::Rng;
        let mut rng = rand::thread_rng();
        let instrumentation = TimestampInstrumentation::rand();
        let records = (0..rng.gen_range(0..=3))
            .map(|_| TimestampStackRecord::rand())
            .collect();
        Self {
            instrumentation,
            records,
        }
    }
}
