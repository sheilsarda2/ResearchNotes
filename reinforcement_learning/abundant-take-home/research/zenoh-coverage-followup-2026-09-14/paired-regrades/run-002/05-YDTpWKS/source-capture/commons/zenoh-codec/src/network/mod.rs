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
    core::{EntityId, Reliability, Timestamp, ZenohIdProto},
    network::{
        ext::{
            self, interception_point, EntityGlobalIdType, TimestampStackRecord,
            TimestampStackValue, TS_STACK_CUSTOM_FLAG, TS_STACK_MAX_RECORDS,
        },
        id, NetworkBody, NetworkBodyRef, NetworkMessage, NetworkMessageExt, NetworkMessageRef,
    },
};

use crate::{
    LCodec, RCodec, WCodec, Zenoh080, Zenoh080Bounded, Zenoh080Header, Zenoh080Length,
    Zenoh080Reliability,
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
impl<const ID: u8> LCodec<&ext::TimestampStackType<{ ID }>> for Zenoh080 {
    fn w_len(self, x: &ext::TimestampStackType<{ ID }>) -> usize {
        let mut len = 1 + self.w_len(x.records.len());
        for r in x.records.iter() {
            let tslen = match &r.timestamp {
                TimestampStackValue::Uhlc(ts) => self.w_len(ts),
                TimestampStackValue::Custom(bs) => bs.len(),
            };
            len += 1 + self.w_len(tslen) + tslen;
        }
        len
    }
}

impl<W, const ID: u8> WCodec<(&ext::TimestampStackType<{ ID }>, bool), &mut W> for Zenoh080
where
    W: Writer,
{
    type Output = Result<(), DidntWrite>;

    fn write(self, writer: &mut W, x: (&ext::TimestampStackType<{ ID }>, bool)) -> Self::Output {
        let (x, more) = x;
        // NOTE: a stack without any instrumented interception point or with too many records
        // is not encodable.
        if x.instrumentation == 0 || x.records.len() > TS_STACK_MAX_RECORDS {
            return Err(DidntWrite);
        }

        let header: ZExtZBufHeader<{ ID }> = ZExtZBufHeader::new(self.w_len(x));
        self.write(&mut *writer, (&header, more))?;

        self.write(&mut *writer, x.instrumentation)?;
        self.write(&mut *writer, x.records.len())?;

        let bodec = Zenoh080Bounded::<u32>::new();
        for r in x.records.iter() {
            self.write(&mut *writer, r.flags())?;
            match &r.timestamp {
                TimestampStackValue::Uhlc(ts) => {
                    bodec.write(&mut *writer, self.w_len(ts))?;
                    self.write(&mut *writer, ts)?;
                }
                TimestampStackValue::Custom(bs) => {
                    bodec.write(&mut *writer, bs.as_slice())?;
                }
            }
        }
        Ok(())
    }
}

impl<R, const ID: u8> RCodec<(ext::TimestampStackType<{ ID }>, bool), &mut R> for Zenoh080
where
    R: Reader,
{
    type Error = DidntRead;

    fn read(self, reader: &mut R) -> Result<(ext::TimestampStackType<{ ID }>, bool), Self::Error> {
        let header: u8 = self.read(&mut *reader)?;
        let codec = Zenoh080Header::new(header);
        codec.read(reader)
    }
}

impl<R, const ID: u8> RCodec<(ext::TimestampStackType<{ ID }>, bool), &mut R> for Zenoh080Header
where
    R: Reader,
{
    type Error = DidntRead;

    fn read(self, reader: &mut R) -> Result<(ext::TimestampStackType<{ ID }>, bool), Self::Error> {
        let (_, more): (ZExtZBufHeader<{ ID }>, bool) = self.read(&mut *reader)?;

        let instrumentation: u8 = self.codec.read(&mut *reader)?;
        if instrumentation == 0 {
            return Err(DidntRead);
        }
        let count: usize = self.codec.read(&mut *reader)?;
        if count > TS_STACK_MAX_RECORDS {
            return Err(DidntRead);
        }

        let bodec = Zenoh080Bounded::<u32>::new();
        let mut records = Vec::with_capacity(count);
        for _ in 0..count {
            let flags: u8 = self.codec.read(&mut *reader)?;
            let bytes: Vec<u8> = bodec.read(&mut *reader)?;

            let point = flags & !TS_STACK_CUSTOM_FLAG;
            if !interception_point::is_valid(point) {
                // Unknown interception point: skip the record for forward compatibility.
                continue;
            }
            if imsg::has_flag(flags, TS_STACK_CUSTOM_FLAG) {
                records.push(TimestampStackRecord {
                    point,
                    timestamp: TimestampStackValue::Custom(bytes),
                });
            } else {
                let mut breader = bytes.as_slice().reader();
                match self.codec.read(&mut breader) {
                    Ok(ts) => {
                        let ts: Timestamp = ts;
                        records.push(TimestampStackRecord {
                            point,
                            timestamp: TimestampStackValue::Uhlc(ts),
                        });
                    }
                    // Undecodable timestamp: skip the record for forward compatibility.
                    Err(_) => continue,
                }
            }
        }

        Ok((
            ext::TimestampStackType {
                instrumentation,
                records,
            },
            more,
        ))
    }
}
