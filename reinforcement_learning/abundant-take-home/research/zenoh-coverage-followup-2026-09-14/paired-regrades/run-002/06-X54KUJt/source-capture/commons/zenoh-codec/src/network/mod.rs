//
// Copyright (c) 2022 ZettaScale Technology
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
mod declare;
mod interest;
mod oam;
mod push;
mod request;
mod response;

use alloc::vec::Vec;

use zenoh_buffers::{
    reader::{BacktrackableReader, DidntRead, HasReader, Reader},
    writer::{DidntWrite, Writer},
};
use zenoh_protocol::{
    common::{imsg, ZExtZ64, ZExtZBufHeader},
    core::{
        timestamp_stack::{
            InstrumentationTimestamp, InterceptionPoint, TimestampInstrumentation, TimestampStack,
            TimestampStackRecord, CUSTOM_TIMESTAMP_FLAG, MAX_TIMESTAMP_STACK_RECORDS,
        },
        EntityId, Reliability, ZenohIdProto,
    },
    network::{
        ext::{self, EntityGlobalIdType},
        id, NetworkBody, NetworkBodyRef, NetworkMessage, NetworkMessageExt, NetworkMessageRef,
    },
};

use crate::{
    LCodec, RCodec, WCodec, Zenoh080, Zenoh080Header, Zenoh080Length, Zenoh080Reliability,
};

// NetworkMessage
impl<W> WCodec<NetworkMessageRef<'_>, &mut W> for Zenoh080
where
    W: Writer,
{
    type Output = Result<(), DidntWrite>;

    #[inline(always)]
    fn write(self, writer: &mut W, x: NetworkMessageRef) -> Self::Output {
        let NetworkMessageRef { body, .. } = x;
        if let NetworkBodyRef::Push(b) = body {
            return self.write(&mut *writer, b);
        }
        #[cold]
        fn write_not_push<W: Writer>(
            codec: Zenoh080,
            writer: &mut W,
            body: NetworkBodyRef,
        ) -> Result<(), DidntWrite> {
            match body {
                NetworkBodyRef::Push(_) => unreachable!(),
                NetworkBodyRef::Request(b) => codec.write(&mut *writer, b),
                NetworkBodyRef::Response(b) => codec.write(&mut *writer, b),
                NetworkBodyRef::ResponseFinal(b) => codec.write(&mut *writer, b),
                NetworkBodyRef::Interest(b) => codec.write(&mut *writer, b),
                NetworkBodyRef::Declare(b) => codec.write(&mut *writer, b),
                NetworkBodyRef::OAM(b) => codec.write(&mut *writer, b),
            }
        }
        write_not_push(self, writer, body)
    }
}

impl<W> WCodec<&NetworkMessage, &mut W> for Zenoh080
where
    W: Writer,
{
    type Output = Result<(), DidntWrite>;

    fn write(self, writer: &mut W, x: &NetworkMessage) -> Self::Output {
        self.write(writer, x.as_ref())
    }
}

impl<R> RCodec<NetworkMessage, &mut R> for Zenoh080
where
    R: Reader,
{
    type Error = DidntRead;

    fn read(self, reader: &mut R) -> Result<NetworkMessage, Self::Error> {
        let codec = Zenoh080Reliability::new(Reliability::DEFAULT);
        codec.read(reader)
    }
}

impl<R> RCodec<NetworkMessage, &mut R> for Zenoh080Reliability
where
    R: Reader,
{
    type Error = DidntRead;

    #[inline(always)]
    fn read(self, reader: &mut R) -> Result<NetworkMessage, Self::Error> {
        let header: u8 = self.codec.read(&mut *reader)?;

        let codec = Zenoh080Header::new(header);
        let mut msg: NetworkMessage = codec.read(&mut *reader)?;
        msg.reliability = self.reliability;
        Ok(msg)
    }
}

impl<R> RCodec<NetworkMessage, &mut R> for Zenoh080Header
where
    R: Reader,
{
    type Error = DidntRead;

    #[inline(always)]
    fn read(self, reader: &mut R) -> Result<NetworkMessage, Self::Error> {
        if imsg::mid(self.header) == id::PUSH {
            return Ok(NetworkBody::Push(self.read(&mut *reader)?).into());
        }
        #[cold]
        fn read_not_push<R: Reader>(
            header: Zenoh080Header,
            reader: &mut R,
        ) -> Result<NetworkMessage, DidntRead> {
            let body = match imsg::mid(header.header) {
                id::REQUEST => NetworkBody::Request(header.read(&mut *reader)?),
                id::RESPONSE => NetworkBody::Response(header.read(&mut *reader)?),
                id::RESPONSE_FINAL => NetworkBody::ResponseFinal(header.read(&mut *reader)?),
                id::INTEREST => NetworkBody::Interest(header.read(&mut *reader)?),
                id::DECLARE => NetworkBody::Declare(header.read(&mut *reader)?),
                id::OAM => NetworkBody::OAM(header.read(&mut *reader)?),
                _ => return Err(DidntRead),
            };

            Ok(body.into())
        }
        read_not_push(self, reader)
    }
}

#[derive(Debug)]
pub struct NetworkMessageIter<R> {
    codec: Zenoh080Reliability,
    reader: R,
}

impl<R> NetworkMessageIter<R> {
    pub fn new(reliability: Reliability, reader: R) -> Self {
        let codec = Zenoh080Reliability::new(reliability);
        Self { codec, reader }
    }
}

impl<R: BacktrackableReader> Iterator for NetworkMessageIter<R> {
    type Item = NetworkMessage;

    fn next(&mut self) -> Option<Self::Item> {
        let mark = self.reader.mark();
        let msg = self.codec.read(&mut self.reader).ok();
        if msg.is_none() {
            self.reader.rewind(mark);
        }
        msg
    }
}

// Extensions: QoS
impl<W, const ID: u8> WCodec<(ext::QoSType<{ ID }>, bool), &mut W> for Zenoh080
where
    W: Writer,
{
    type Output = Result<(), DidntWrite>;

    fn write(self, writer: &mut W, x: (ext::QoSType<{ ID }>, bool)) -> Self::Output {
        let (x, more) = x;
        let ext: ZExtZ64<{ ID }> = x.into();
        self.write(&mut *writer, (&ext, more))
    }
}

impl<R, const ID: u8> RCodec<(ext::QoSType<{ ID }>, bool), &mut R> for Zenoh080
where
    R: Reader,
{
    type Error = DidntRead;

    fn read(self, reader: &mut R) -> Result<(ext::QoSType<{ ID }>, bool), Self::Error> {
        let header: u8 = self.read(&mut *reader)?;
        let codec = Zenoh080Header::new(header);
        codec.read(reader)
    }
}

impl<R, const ID: u8> RCodec<(ext::QoSType<{ ID }>, bool), &mut R> for Zenoh080Header
where
    R: Reader,
{
    type Error = DidntRead;

    fn read(self, reader: &mut R) -> Result<(ext::QoSType<{ ID }>, bool), Self::Error> {
        let (ext, more): (ZExtZ64<{ ID }>, bool) = self.read(&mut *reader)?;
        Ok((ext.into(), more))
    }
}

// Extensions: Timestamp
impl<W, const ID: u8> WCodec<(&ext::TimestampType<{ ID }>, bool), &mut W> for Zenoh080
where
    W: Writer,
{
    type Output = Result<(), DidntWrite>;

    fn write(self, writer: &mut W, x: (&ext::TimestampType<{ ID }>, bool)) -> Self::Output {
        let (tstamp, more) = x;
        let header: ZExtZBufHeader<{ ID }> = ZExtZBufHeader::new(self.w_len(&tstamp.timestamp));
        self.write(&mut *writer, (&header, more))?;
        self.write(&mut *writer, &tstamp.timestamp)
    }
}

impl<R, const ID: u8> RCodec<(ext::TimestampType<{ ID }>, bool), &mut R> for Zenoh080
where
    R: Reader,
{
    type Error = DidntRead;

    fn read(self, reader: &mut R) -> Result<(ext::TimestampType<{ ID }>, bool), Self::Error> {
        let header: u8 = self.read(&mut *reader)?;
        let codec = Zenoh080Header::new(header);
        codec.read(reader)
    }
}

impl<R, const ID: u8> RCodec<(ext::TimestampType<{ ID }>, bool), &mut R> for Zenoh080Header
where
    R: Reader,
{
    type Error = DidntRead;

    fn read(self, reader: &mut R) -> Result<(ext::TimestampType<{ ID }>, bool), Self::Error> {
        let (_, more): (ZExtZBufHeader<{ ID }>, bool) = self.read(&mut *reader)?;
        let timestamp: uhlc::Timestamp = self.codec.read(&mut *reader)?;
        Ok((ext::TimestampType { timestamp }, more))
    }
}

// Extensions: NodeId
impl<W, const ID: u8> WCodec<(ext::NodeIdType<{ ID }>, bool), &mut W> for Zenoh080
where
    W: Writer,
{
    type Output = Result<(), DidntWrite>;

    fn write(self, writer: &mut W, x: (ext::NodeIdType<{ ID }>, bool)) -> Self::Output {
        let (x, more) = x;
        let ext: ZExtZ64<{ ID }> = x.into();
        self.write(&mut *writer, (&ext, more))
    }
}

impl<R, const ID: u8> RCodec<(ext::NodeIdType<{ ID }>, bool), &mut R> for Zenoh080
where
    R: Reader,
{
    type Error = DidntRead;

    fn read(self, reader: &mut R) -> Result<(ext::NodeIdType<{ ID }>, bool), Self::Error> {
        let header: u8 = self.read(&mut *reader)?;
        let codec = Zenoh080Header::new(header);
        codec.read(reader)
    }
}

impl<R, const ID: u8> RCodec<(ext::NodeIdType<{ ID }>, bool), &mut R> for Zenoh080Header
where
    R: Reader,
{
    type Error = DidntRead;

    fn read(self, reader: &mut R) -> Result<(ext::NodeIdType<{ ID }>, bool), Self::Error> {
        let (ext, more): (ZExtZ64<{ ID }>, bool) = self.read(&mut *reader)?;
        Ok((ext.into(), more))
    }
}

// Extension: EntityId
impl<const ID: u8> LCodec<&ext::EntityGlobalIdType<{ ID }>> for Zenoh080 {
    fn w_len(self, x: &ext::EntityGlobalIdType<{ ID }>) -> usize {
        let EntityGlobalIdType { zid, eid } = x;

        1 + self.w_len(zid) + self.w_len(*eid)
    }
}

impl<W, const ID: u8> WCodec<(&ext::EntityGlobalIdType<{ ID }>, bool), &mut W> for Zenoh080
where
    W: Writer,
{
    type Output = Result<(), DidntWrite>;

    fn write(self, writer: &mut W, x: (&ext::EntityGlobalIdType<{ ID }>, bool)) -> Self::Output {
        let (x, more) = x;
        let header: ZExtZBufHeader<{ ID }> = ZExtZBufHeader::new(self.w_len(x));
        self.write(&mut *writer, (&header, more))?;

        let flags: u8 = (x.zid.size() as u8 - 1) << 4;
        self.write(&mut *writer, flags)?;

        let lodec = Zenoh080Length::new(x.zid.size());
        lodec.write(&mut *writer, &x.zid)?;

        self.write(&mut *writer, x.eid)?;
        Ok(())
    }
}

impl<R, const ID: u8> RCodec<(ext::EntityGlobalIdType<{ ID }>, bool), &mut R> for Zenoh080Header
where
    R: Reader,
{
    type Error = DidntRead;

    fn read(self, reader: &mut R) -> Result<(ext::EntityGlobalIdType<{ ID }>, bool), Self::Error> {
        let (_, more): (ZExtZBufHeader<{ ID }>, bool) = self.read(&mut *reader)?;

        let flags: u8 = self.codec.read(&mut *reader)?;
        let length = 1 + ((flags >> 4) as usize);

        let lodec = Zenoh080Length::new(length);
        let zid: ZenohIdProto = lodec.read(&mut *reader)?;

        let eid: EntityId = self.codec.read(&mut *reader)?;

        Ok((ext::EntityGlobalIdType { zid, eid }, more))
    }
}

// Extension: TimestampStack
pub(crate) fn ts_stack_len(codec: Zenoh080, x: &TimestampStack) -> usize {
    let mut len = 1 + codec.w_len(x.records().len() as u64);
    for record in x.records() {
        len += 1;
        len += match record.timestamp() {
            InstrumentationTimestamp::UHLC(ts) => {
                let n = codec.w_len(ts);
                codec.w_len(n as u64) + n
            }
            InstrumentationTimestamp::Custom(bytes) => codec.w_len(bytes.len() as u64) + bytes.len(),
        };
    }
    len
}

pub(crate) fn write_ts_stack<const ID: u8, W>(
    codec: Zenoh080,
    writer: &mut W,
    x: &TimestampStack,
    more: bool,
) -> Result<(), DidntWrite>
where
    W: Writer,
{
    if x.records().len() > MAX_TIMESTAMP_STACK_RECORDS {
        return Err(DidntWrite);
    }
    let header: ZExtZBufHeader<{ ID }> = ZExtZBufHeader::new(ts_stack_len(codec, x));
    codec.write(&mut *writer, (&header, more))?;
    codec.write(&mut *writer, x.instrumentation().flags())?;
    codec.write(&mut *writer, x.records().len() as u64)?;
    for record in x.records() {
        let mut flags: u8 = u8::from(record.point());
        match record.timestamp() {
            InstrumentationTimestamp::UHLC(ts) => {
                codec.write(&mut *writer, flags)?;
                codec.write(&mut *writer, codec.w_len(ts) as u64)?;
                codec.write(&mut *writer, ts)?;
            }
            InstrumentationTimestamp::Custom(bytes) => {
                flags |= CUSTOM_TIMESTAMP_FLAG;
                codec.write(&mut *writer, flags)?;
                codec.write(&mut *writer, &bytes[..])?;
            }
        }
    }
    Ok(())
}

pub(crate) fn read_ts_stack<const ID: u8, R>(
    codec: Zenoh080Header,
    reader: &mut R,
) -> Result<(TimestampStack, bool), DidntRead>
where
    R: Reader,
{
    let (_, more): (ZExtZBufHeader<{ ID }>, bool) = codec.read(&mut *reader)?;
    let flags: u8 = codec.codec.read(&mut *reader)?;
    if flags == 0 {
        return Err(DidntRead);
    }
    let instrumentation =
        TimestampInstrumentation::try_from_flags(flags).map_err(|_| DidntRead)?;
    let count: u64 = codec.codec.read(&mut *reader)?;
    if count > MAX_TIMESTAMP_STACK_RECORDS as u64 {
        return Err(DidntRead);
    }
    let mut records = Vec::with_capacity(count as usize);
    for _ in 0..count {
        let rflags: u8 = codec.codec.read(&mut *reader)?;
        let bytes: Vec<u8> = codec.codec.read(&mut *reader)?;
        let Ok(point) = InterceptionPoint::try_from(rflags & !CUSTOM_TIMESTAMP_FLAG) else {
            continue;
        };
        if rflags & CUSTOM_TIMESTAMP_FLAG != 0 {
            records.push(TimestampStackRecord::new(
                point,
                InstrumentationTimestamp::Custom(bytes),
            ));
        } else {
            let mut r = bytes.reader();
            let Ok(ts) = <Zenoh080 as RCodec<uhlc::Timestamp, &mut _>>::read(codec.codec, &mut r)
            else {
                continue;
            };
            records.push(TimestampStackRecord::new(
                point,
                InstrumentationTimestamp::UHLC(ts),
            ));
        }
    }
    Ok((TimestampStack::with_records(instrumentation, records), more))
}
