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
use alloc::vec::Vec;

use zenoh_buffers::{
    reader::{DidntRead, HasReader, Reader},
    writer::{DidntWrite, Writer},
    ZBuf,
};
use zenoh_protocol::{
    common::{iext, imsg::has_flag, ZExtZBufHeader},
    core::Timestamp,
    network::ts_stack::{
        InstrumentationTimestamp, InterceptionPoint, TimestampInstrumentation, TimestampStack,
        TimestampStackRecord, CUSTOM_TIMESTAMP_FLAG, MAX_TIMESTAMP_STACK_LEN,
    },
};

use crate::{LCodec, RCodec, WCodec, Zenoh080, Zenoh080Bounded, Zenoh080Header};

impl LCodec<&TimestampStackRecord> for Zenoh080 {
    fn w_len(self, x: &TimestampStackRecord) -> usize {
        let body = match x.timestamp() {
            InstrumentationTimestamp::UHLC(ts) => {
                let len = self.w_len(ts);
                self.w_len(len) + len
            }
            InstrumentationTimestamp::Custom(bytes) => self.w_len(bytes.as_slice()),
        };
        1 + body
    }
}

impl LCodec<&TimestampStack> for Zenoh080 {
    fn w_len(self, x: &TimestampStack) -> usize {
        let mut len = 1 + self.w_len(x.records().len());
        for r in x.records() {
            len += self.w_len(r);
        }
        len
    }
}

impl<W> WCodec<&TimestampStack, &mut W> for Zenoh080
where
    W: Writer,
{
    type Output = Result<(), DidntWrite>;

    fn write(self, writer: &mut W, x: &TimestampStack) -> Self::Output {
        self.write(&mut *writer, x.instrumentation().flags())?;
        self.write(&mut *writer, x.records().len())?;
        for r in x.records() {
            let mut flags: u8 = r.point().into();
            if r.is_custom() {
                flags |= CUSTOM_TIMESTAMP_FLAG;
            }
            self.write(&mut *writer, flags)?;
            match r.timestamp() {
                InstrumentationTimestamp::UHLC(ts) => {
                    self.write(&mut *writer, self.w_len(ts))?;
                    self.write(&mut *writer, ts)?;
                }
                InstrumentationTimestamp::Custom(bytes) => {
                    self.write(&mut *writer, bytes.as_slice())?;
                }
            }
        }
        Ok(())
    }
}

/// Write the timestamp stack extension with the given extension id.
pub(crate) fn write_ts_stack<const ID: u8, W>(
    codec: Zenoh080,
    writer: &mut W,
    x: &TimestampStack,
    more: bool,
) -> Result<(), DidntWrite>
where
    W: Writer,
{
    let header: ZExtZBufHeader<{ ID }> = ZExtZBufHeader::new(codec.w_len(x));
    codec.write(&mut *writer, (&header, more))?;
    codec.write(&mut *writer, x)
}

impl<R> RCodec<TimestampStack, &mut R> for Zenoh080
where
    R: Reader,
{
    type Error = DidntRead;

    fn read(self, reader: &mut R) -> Result<TimestampStack, Self::Error> {
        let flags: u8 = self.read(&mut *reader)?;
        let instrumentation =
            TimestampInstrumentation::try_from_flags(flags).map_err(|_| DidntRead)?;
        let count: usize = self.read(&mut *reader)?;
        if count > MAX_TIMESTAMP_STACK_LEN {
            return Err(DidntRead);
        }
        let mut records = Vec::with_capacity(count);
        for _ in 0..count {
            let rflags: u8 = self.read(&mut *reader)?;
            let bytes: Vec<u8> = self.read(&mut *reader)?;
            let Ok(point) = InterceptionPoint::try_from(rflags) else {
                // Unknown interception point: skip the record.
                continue;
            };
            if has_flag(rflags, CUSTOM_TIMESTAMP_FLAG) {
                records.push(TimestampStackRecord::new(
                    point,
                    InstrumentationTimestamp::Custom(bytes),
                ));
            } else {
                let mut tsreader = bytes.reader();
                let Ok(ts): Result<Timestamp, DidntRead> = self.read(&mut tsreader) else {
                    // Undecodable timestamp: skip the record.
                    continue;
                };
                records.push(TimestampStackRecord::new(
                    point,
                    InstrumentationTimestamp::UHLC(ts),
                ));
            }
        }
        Ok(TimestampStack::with_records(instrumentation, records))
    }
}

/// Read the timestamp stack extension body. The extension header has already been read.
pub(crate) fn read_ts_stack<R>(
    codec: Zenoh080Header,
    reader: &mut R,
) -> Result<(TimestampStack, bool), DidntRead>
where
    R: Reader,
{
    let bodec = Zenoh080Bounded::<u32>::new();
    let len: usize = bodec.read(&mut *reader)?;
    let zbuf: ZBuf = reader.read_zbuf(len)?;
    let more = has_flag(codec.header, iext::FLAG_Z);
    let stack: TimestampStack = codec.codec.read(&mut zbuf.reader())?;
    Ok((stack, more))
}
