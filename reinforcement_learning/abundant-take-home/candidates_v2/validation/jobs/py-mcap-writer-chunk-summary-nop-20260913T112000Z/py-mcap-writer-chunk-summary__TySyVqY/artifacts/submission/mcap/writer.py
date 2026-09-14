import struct
import zlib
from enum import Enum, Flag, auto
from importlib.metadata import PackageNotFoundError, version
from io import BufferedWriter, RawIOBase
from typing import IO, Any, Dict, OrderedDict, Union

from .exceptions import UnsupportedCompressionError

try:
    __version__ = version("mcap")
except PackageNotFoundError:
    __version__ = "0.0.0"

try:
    import lz4.frame  # type: ignore
except ImportError:
    lz4 = None

try:
    import zstandard
except ImportError:
    zstandard = None

from .data_stream import RecordBuilder
from .records import (
    Attachment,
    Channel,
    DataEnd,
    Footer,
    Header,
    Message,
    Metadata,
    Schema,
)

MCAP0_MAGIC = struct.pack("<8B", 137, 77, 67, 65, 80, 48, 13, 10)
LIBRARY_IDENTIFIER = f"mcap-python/{__version__}"


class CompressionType(Enum):
    NONE = auto()
    LZ4 = auto()
    ZSTD = auto()


class IndexType(Flag):
    """Determines what indexes should be written to the MCAP file. If in doubt, choose ALL."""

    NONE = auto()
    ATTACHMENT = auto()
    CHUNK = auto()
    MESSAGE = auto()
    METADATA = auto()
    ALL = ATTACHMENT | CHUNK | MESSAGE | METADATA


class Writer:
    """
    Writes MCAP data.

    :param output: A filename or stream to write to.
    :param chunk_size: The maximum size of individual data chunks in a chunked file.
    :param compression: Compression to apply to chunk data, if any.
    :param index_types: Indexes to write to the file. See IndexType for possibilities.
    :param repeat_channels: Repeat channel information at the end of the file.
    :param repeat_schemas: Repeat schemas at the end of the file.
    :param use_chunking: Group data in chunks.
    :param use_statistics: Write statistics record.
    :param use_summary_offsets: Write summary offset records.
    """

    def __init__(
        self,
        output: Union[str, IO[Any], BufferedWriter],
        chunk_size: int = 1024 * 1024,
        compression: CompressionType = CompressionType.ZSTD,
        index_types: IndexType = IndexType.ALL,
        repeat_channels: bool = True,
        repeat_schemas: bool = True,
        use_chunking: bool = True,
        use_statistics: bool = True,
        use_summary_offsets: bool = True,
        enable_crcs: bool = True,
        enable_data_crcs: bool = False,
    ):
        self.__should_close = False
        if isinstance(output, str):
            self.__stream = open(output, "wb")
            self.__should_close = True
        elif isinstance(output, RawIOBase):
            self.__stream = BufferedWriter(output)
        else:
            self.__stream = output
        self.__record_builder = RecordBuilder()
        self.__channels: OrderedDict[int, Channel] = OrderedDict()
        self.__schemas: OrderedDict[int, Schema] = OrderedDict()
        self.__chunk_size = chunk_size
        self.__compression = compression
        self.__index_types = index_types
        self.__repeat_channels = repeat_channels
        self.__repeat_schemas = repeat_schemas
        self.__use_chunking = use_chunking
        self.__use_statistics = use_statistics
        self.__use_summary_offsets = use_summary_offsets
        self.__enable_crcs = enable_crcs
        self.__enable_data_crcs = enable_data_crcs
        self.__data_section_crc = 0

        # validate compression
        if self.__compression == CompressionType.LZ4:
            if lz4 is None:
                raise UnsupportedCompressionError("lz4")
        elif self.__compression == CompressionType.ZSTD:
            if zstandard is None:
                raise UnsupportedCompressionError("zstandard")

        if (
            use_chunking
            or use_statistics
            or use_summary_offsets
            or repeat_channels
            or repeat_schemas
            or bool(index_types & IndexType.ALL)
        ):
            raise NotImplementedError(
                "chunked writing, indexes, statistics and the summary section "
                "are not implemented; only unchunked, unindexed output is available"
            )

    def add_attachment(
        self, create_time: int, log_time: int, name: str, media_type: str, data: bytes
    ):
        """
        Adds an attachment to the file.

        :param log_time: Time at which the attachment was recorded.
        :param create_time: Time at which the attachment was created. If not available,
            must be set to zero.
        :param name: Name of the attachment, e.g "scene1.jpg".
        :param media_type: Media Type (e.g "text/plain").
        :param data: Attachment data.
        """
        attachment = Attachment(
            create_time=create_time,
            log_time=log_time,
            name=name,
            media_type=media_type,
            data=data,
        )
        attachment.write(self.__record_builder)
        self.__flush()

    def add_message(
        self,
        channel_id: int,
        log_time: int,
        data: bytes,
        publish_time: int,
        sequence: int = 0,
    ):
        """
        Adds a new message to the file. If chunking is enabled the message will be added to the
        current chunk.

        :param channel_id: The id of the channel to which the message should be added.
        :param sequence: Optional message counter assigned by publisher.
        :param log_time: Time at which the message was recorded as nanoseconds since a
            user-understood epoch (i.e unix epoch, robot boot time, etc.).
        :param publish_time: Time at which the message was published as nanoseconds since a
            user-understood epoch (i.e unix epoch, robot boot time, etc.).
        :param data: Message data, to be decoded according to the schema of the channel.
        """
        message = Message(
            channel_id=channel_id,
            log_time=log_time,
            data=data,
            publish_time=publish_time,
            sequence=sequence,
        )
        message.write(self.__record_builder)
        self.__flush()

    def add_metadata(self, name: str, data: Dict[str, str]):
        """
        Adds key-value metadata to the file.

        :param name: A name to associate with the metadata.
        :param data: Key-value metadata.
        """
        metadata = Metadata(name=name, metadata=data)
        metadata.write(self.__record_builder)
        self.__flush()

    def finish(self):
        """
        Writes any final indexes, summaries etc to the file. Note that it does
        not close the underlying output stream.
        """
        DataEnd(self.__data_section_crc).write(self.__record_builder)
        self.__flush()

        Footer(summary_start=0, summary_offset_start=0, summary_crc=0).write(
            self.__record_builder
        )
        self.__flush()
        self.__stream.write(MCAP0_MAGIC)
        if self.__should_close:
            self.__stream.close()

    def register_channel(
        self,
        topic: str,
        message_encoding: str,
        schema_id: int,
        metadata: Dict[str, str] = {},
    ) -> int:
        """
        Registers a new message channel. Returns the numeric id of the new channel.

        :param schema_id: The schema for messages on this channel. A schema_id of 0 indicates there
            is no schema for this channel.
        :param topic: The channel topic.
        :param message_encoding: Encoding for messages on this channel. See the list of well-known
            message encodings for common values.
        :param metadata: Metadata about this channel.
        """
        channel_id = len(self.__channels) + 1
        channel = Channel(
            id=channel_id,
            topic=topic,
            message_encoding=message_encoding,
            schema_id=schema_id,
            metadata=metadata,
        )
        self.__channels[channel_id] = channel
        channel.write(self.__record_builder)
        return channel_id

    def register_schema(self, name: str, encoding: str, data: bytes):
        """
        Registers a new message schema. Returns the new integer schema id.

        :param name: An identifier for the schema.
        :param encoding: Format for the schema. See the list of well-known schema encodings for
            common values. An empty string indicates no schema is available.
        :param data: Schema data. Must conform to the schema encoding. If `encoding` is an empty
            string, `data` should be 0 length.
        """
        schema_id = len(self.__schemas) + 1
        schema = Schema(id=schema_id, data=data, encoding=encoding, name=name)
        self.__schemas[schema_id] = schema
        schema.write(self.__record_builder)
        return schema_id

    def start(self, profile: str = "", library: str = LIBRARY_IDENTIFIER):
        """
        Starts writing to the output stream.

        :param profile: The profile is used for indicating requirements for fields
            throughout the file (encoding, user_data, etc).
        :param library: Free-form string for writer to specify its name, version, or other
            information for use in debugging.
        """
        self.__stream.write(MCAP0_MAGIC)
        if self.__enable_data_crcs:
            self.__data_section_crc = zlib.crc32(MCAP0_MAGIC, self.__data_section_crc)
        Header(profile, library).write(self.__record_builder)
        self.__flush()

    def __flush(self):
        data = self.__record_builder.end()
        if self.__enable_data_crcs:
            self.__data_section_crc = zlib.crc32(data, self.__data_section_crc)
        self.__stream.write(data)


__all__ = ["CompressionType", "IndexType", "LIBRARY_IDENTIFIER", "Writer"]
