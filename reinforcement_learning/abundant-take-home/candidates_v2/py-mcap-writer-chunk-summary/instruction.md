# Implement chunked writing and the summary section in the MCAP Python writer

## Context

This checkout is `foxglove/mcap` at commit `aebd536b` reduced to the Python package
(`python/mcap`), the MCAP format specification (`website/docs/spec/index.md`, with
`notes.md`, `registry.md` and the Kaitai description `mcap.ksy`), one small sample file
(`testdata/mcap/demo.mcap`) and one conformance case
(`tests/conformance/data/OneMessage/OneMessage.mcap` + `.json`) that an upstream unit test
reads. The other language implementations and the rest of the conformance corpus are
deliberately not present. The specification is the authority; section names quoted below
("Chunk (op=0x06)", "Summary Section", ...) are headings of `website/docs/spec/index.md`.

The package is installed in editable mode; `python -m pytest python/mcap/tests` runs the
upstream unit tests (16 of them currently fail with `NotImplementedError`).

The reader side is complete: `mcap.reader`, `mcap.stream_reader` and every `read()` in
`mcap.records`. The writer is not. `mcap/writer.py` can only produce unchunked, unindexed
files without a summary section, and `Writer.__init__` raises `NotImplementedError` whenever
chunking, any index type, statistics, summary offsets or repeated Schema/Channel records are
requested. Seven record types in `mcap/records.py` (`Chunk`, `ChunkIndex`, `MessageIndex`,
`AttachmentIndex`, `MetadataIndex`, `Statistics`, `SummaryOffset`) have `read()` but no
`write()` serializer, so they fall through to the base class and raise.

Implement the missing subsystem so that the writer produces complete, indexed MCAP files that
are **byte-for-byte identical** to the files of the project's cross-language conformance
corpus and are readable by the Rust and Go MCAP readers.

## Scope and rules

- Change only files under `python/mcap/mcap/`. That directory is the only thing collected
  for grading, and only `.py` files (plus `py.typed`) in it are accepted; you may add new
  modules there. `python/mcap/tests/`, the runner scripts inside it, packaging metadata and
  the specification are restored from pristine copies before grading, so edits to them have
  no effect.
- Python 3.10. Dependencies are the standard library plus `lz4` and `zstandard`, which are
  installed. Do not add dependencies. Do not spawn subprocesses or read files other than the
  ones the caller passes in.
- The reader must keep working exactly as it does now; its conformance is re-run.
- No reference implementation is available in this environment. The grader has the Rust and
  Go readers built from the same commit, and the full conformance corpus; you do not.

## Public API

Everything below is importable today and must keep these names, signatures and defaults.

```python
from mcap.writer import Writer, CompressionType, IndexType, LIBRARY_IDENTIFIER, MCAP0_MAGIC

class CompressionType(Enum):  NONE, LZ4, ZSTD
class IndexType(Flag):        NONE, ATTACHMENT, CHUNK, MESSAGE, METADATA,
                              ALL = ATTACHMENT | CHUNK | MESSAGE | METADATA

Writer(output,                       # str path (opened "wb", closed by finish()), RawIOBase
                                     # (wrapped in a BufferedWriter) or any binary stream
       chunk_size=1024 * 1024,
       compression=CompressionType.ZSTD,
       index_types=IndexType.ALL,
       repeat_channels=True, repeat_schemas=True,
       use_chunking=True, use_statistics=True, use_summary_offsets=True,
       enable_crcs=True, enable_data_crcs=False)
Writer.start(profile="", library=LIBRARY_IDENTIFIER)
Writer.register_schema(name, encoding, data) -> int
Writer.register_channel(topic, message_encoding, schema_id, metadata={}) -> int
Writer.add_message(channel_id, log_time, data, publish_time, sequence=0)
Writer.add_attachment(create_time, log_time, name, media_type, data)
Writer.add_metadata(name, data)
Writer.finish()                      # writes everything pending; never closes a caller stream
```

`Writer(...)` must still raise `mcap.exceptions.UnsupportedCompressionError` when the library
for the requested compression is not importable. After your change no combination of the
constructor options may raise `NotImplementedError`. `LIBRARY_IDENTIFIER` stays
`"mcap-python/<package version>"` and remains the default header library string.

In `mcap.records`, every record dataclass exposes `write(self, stream: RecordBuilder) -> None`
and a static `read(stream: ReadDataStream)`. `RecordBuilder.start_record(opcode)` /
`finish_record()` (in `mcap/data_stream.py`) frame a record with its opcode and uint64
content length; `write1/2/4/8`, `write_prefixed_string` and `write` emit fields. Reading back
what `write()` produced must yield an equal dataclass instance.

## Behavioral contract

Each numbered requirement is checked by the grader; "offset" always means a byte offset from
the start of the file, including the leading magic.

**File structure** (spec: "File Structure", "Header (op=0x01)", "Footer (op=0x02)",
"Data Section", "Summary Section", "Summary Offset Section")

- **R1.** `start()` writes the magic and the Header. `finish()` writes, in order: the chunk
  still being accumulated (if it is written at all, see R4), the Data End record, the summary
  section (R11), the summary offset section (R12), the Footer (R13) and the trailing magic.
- **R2.** `register_schema` assigns ids 1, 2, 3, ... in call order and returns the id;
  `register_channel` does the same independently for channel ids. A `schema_id` of 0 on a
  channel means "no schema" and is passed through unchanged.

**Chunking** (spec: "Chunk (op=0x06)", "Use of chunk records")

- **R3.** With `use_chunking=True`, Schema, Channel and Message records are not written to
  the file when registered or added; they are appended, in call order, to the uncompressed
  record buffer of the chunk currently being accumulated. Attachment and Metadata records are
  never placed inside a chunk: they are written to the data section at the moment
  `add_attachment` / `add_metadata` is called, which puts them *before* the chunk currently
  being accumulated, and calling them does not finalize that chunk. With
  `use_chunking=False` every record is written directly to the data section in call order and
  no Chunk or Message Index record exists.
- **R4.** After each Schema, Channel or Message record is appended, the chunk is finalized if
  the uncompressed buffer is now strictly larger than `chunk_size` bytes; whatever is buffered
  when `finish()` is called is finalized then. A chunk is written only if its buffer contains
  at least one Message record; a chunked file to which no message was added therefore contains
  no Chunk record, and its statistics report zero chunks.
- **R5.** A finalized chunk becomes one Chunk record whose `message_start_time` /
  `message_end_time` are the minimum / maximum `log_time` of the messages in the chunk,
  `uncompressed_size` is the buffer length, `uncompressed_crc` is the CRC32 of the uncompressed
  buffer when `enable_crcs` is true and 0 otherwise, `compression` is `"zstd"`, `"lz4"` or
  `""` for `CompressionType.ZSTD`, `LZ4`, `NONE`, and `records` is the buffer compressed with
  `zstandard.compress(buffer)` or `lz4.frame.compress(buffer)` at the libraries' default
  settings (upstream tests pin the resulting sizes), or the buffer itself for `NONE`.

**Message Index** (spec: "Message Index (op=0x07)")

- **R6.** When `IndexType.MESSAGE` is enabled, immediately after each Chunk record, and
  contiguously, one Message Index record is written for every channel that has at least one
  message in that chunk, in order of each channel's first message within the chunk. Its
  `records` array holds one `(log_time, offset)` entry per message of that channel in the order
  the messages were added, where `offset` is the position of the Message record's opcode byte
  relative to the start of the *uncompressed* chunk buffer. When `IndexType.MESSAGE` is not
  enabled no Message Index records are written.

**Chunk Index** (spec: "Chunk Index (op=0x08)")

- **R7.** When `IndexType.CHUNK` is enabled, the summary section holds one Chunk Index per
  Chunk record written, in file order: `chunk_start_offset` is the offset of the Chunk record's
  opcode byte; `chunk_length` is the full record length including opcode and length prefix;
  `message_index_offsets` maps each channel id to the offset of that channel's Message Index
  record, in the order those records were written, and is empty when message indexes are not
  written; `message_index_length` is the total byte length of the Message Index records that
  follow the chunk (0 when there are none); `compression`, `compressed_size` (the byte length of
  the chunk's `records` field), `uncompressed_size`, `message_start_time` and
  `message_end_time` repeat the chunk's values.

**Attachment and Metadata indexes** (spec: "Attachment Index (op=0x0A)",
"Metadata Index (op=0x0D)")

- **R8.** When `IndexType.ATTACHMENT` is enabled, the summary holds one Attachment Index per
  Attachment, in file order, with `offset` = offset of the Attachment record's opcode byte,
  `length` = its full record length, `log_time`, `create_time`, `data_size = len(data)`,
  `name` and `media_type`. When `IndexType.METADATA` is enabled, likewise one Metadata Index
  per Metadata record with `offset`, `length` and `name`.

**Data End** (spec: "Data End (op=0x0F)")

- **R9.** `data_section_crc` is the CRC32 of every byte of the file before the Data End record
  (leading magic included) when `enable_data_crcs` is true, else 0. This already works for the
  unchunked path and must stay correct once chunks and message indexes are written.

**Statistics** (spec: "Statistics (op=0x0B)")

- **R10.** When `use_statistics` is true, exactly one Statistics record is written with:
  `message_count` (messages added), `schema_count` (schemas registered), `channel_count`
  (channels registered), `attachment_count`, `metadata_count`, `chunk_count` (Chunk records
  actually written), `message_start_time` / `message_end_time` (minimum / maximum `log_time`
  over all messages, 0 when there are none) and `channel_message_counts` mapping channel id to
  its message count for every channel with at least one message, in order of each channel's
  first message.

**Summary section** (spec: "Summary Section")

- **R11.** The summary section is made of the following groups in this order, each present
  only when its option is enabled: (1) Schema records, copies of every registered schema in
  registration order, when `repeat_schemas`; (2) Channel records, copies of every registered
  channel in registration order, when `repeat_channels`; (3) the Statistics record when
  `use_statistics`; (4) Chunk Index records when `IndexType.CHUNK`; (5) Attachment Index
  records when `IndexType.ATTACHMENT`; (6) Metadata Index records when `IndexType.METADATA`.
  Records of one group are contiguous. An enabled group with nothing to list occupies zero
  bytes.

**Summary Offset section** (spec: "Summary Offset Section", "Summary Offset (op=0x0E)")

- **R12.** When `use_summary_offsets` is true, one Summary Offset record is written after the
  summary section for **every enabled group, in the order of R11, including enabled groups
  that contain no records**: `group_opcode` is the group's record opcode (0x03, 0x04, 0x0B,
  0x08, 0x0A, 0x0D), `group_start` is the offset at which the group's records begin (for an
  empty group, the offset at which they would have begun) and `group_length` is the total byte
  length of the group's records (0 for an empty group). Consequently a default-configured
  writer on which only `start()` and `finish()` are called produces exactly: Header, Data End,
  Statistics (all counts zero), six Summary Offset records and Footer.

**Footer** (spec: "Footer (op=0x02)")

- **R13.** `summary_start` is the offset of the first summary record, or 0 when no summary
  record was written at all. `summary_offset_start` is the offset of the first Summary Offset
  record when `use_summary_offsets` is true and at least one group is enabled, else 0.
  `summary_crc`, when `enable_crcs` is true, is the CRC32 of all bytes from `summary_start`
  (from the footer's own position when the summary is empty) through the end of the footer's
  `summary_offset_start` field, i.e. the summary section, the summary offset section and the
  first 25 bytes of the Footer record (opcode, length, `summary_start`, `summary_offset_start`,
  with the same values as written); when `enable_crcs` is false it is 0.

**Serialization** (spec: "Serialization" and the record tables)

- **R14.** `write()` for `Chunk`, `ChunkIndex`, `MessageIndex`, `AttachmentIndex`,
  `MetadataIndex`, `Statistics` and `SummaryOffset` emits exactly the fields of the spec
  tables, in table order, with the spec's encodings: little-endian fixed-width integers,
  uint32-length-prefixed UTF-8 strings, `Map<uint16,uint64>` as a uint32 byte length followed by
  10-byte entries, `Array<Tuple<Timestamp,uint64>>` as a uint32 byte length followed by 16-byte
  entries, `Map<string,string>` as in the existing Channel/Metadata writers, and the chunk's
  `records` as a uint64-length-prefixed byte string. `record.read(...)` of the written bytes
  must equal the original instance.

**Reading**

- **R15.** `mcap.stream_reader.StreamReader` and `mcap.reader.SeekingReader` /
  `NonSeekingReader` keep producing today's results on the conformance corpus.

## How the grader exercises the writer

- **R16. Corpus conformance.** The grader runs the pristine `python/mcap/tests/run_writer_test.py`
  on each of the 208 corpus cases whose feature set does not include `pad`. That script builds
  `Writer(BytesIO(), index_types=<from features>, compression=CompressionType.NONE,
  repeat_channels="rch" in features, repeat_schemas="rsh" in features, use_chunking="ch" in
  features, use_statistics="st" in features, use_summary_offsets="sum" in features,
  enable_crcs=True, enable_data_crcs=True)` where `ax`, `chx`, `mx`, `mdx` enable
  `IndexType.ATTACHMENT`, `CHUNK`, `MESSAGE`, `METADATA`; replays the case's Header (via
  `start(profile, library)`), Schema, Channel, Message, Attachment and Metadata records through
  the public API; calls `finish()`; and writes the bytes to stdout. The output must equal the
  corpus `.mcap` byte for byte. Each corpus case contains at most one channel and one schema
  and fits in a single chunk; the files were produced by the project's reference generator
  under the rules above. The pristine `run_reader_test.py` is run on all 416 corpus files
  (streamed) and on the 16 files that carry `ch`, `chx`, `mx`, `rch` and `rsh` (indexed).
- **R17. Cross-implementation.** Every file the Python writer produces for the corpus is also
  read with the Rust and Go MCAP readers (streamed, and indexed where the file carries chunk
  indexes) and must yield the same records as the corresponding corpus file. In addition the
  grader writes its own files through the public API, multi-chunk (small `chunk_size`), with
  three channels, two schemas, an attachment and a metadata record, under each compression
  type, with and without CRCs, chunked and unchunked, and a header-only default file, and
  checks R3-R13 on the bytes and with the Rust and Go readers (all messages recovered; indexed
  reads in non-decreasing `log_time` order).
- **Upstream unit tests.** The restored `python/mcap/tests` (36 tests; the timing test
  `test_insert_order_is_faster` is excluded) must pass with no skips.

Grading is binary: all of the above must pass. Per-group results are logged for diagnosis.
