# Restore index-based reading in the MCAP C++ library

## Where you are

`/workspace/repo` is a checkout of the [MCAP](https://mcap.dev) monorepo (foxglove/mcap). The C++
implementation is a header-only library under `cpp/mcap/include/mcap/` (`reader.hpp`/`reader.inl`,
`writer.hpp`/`writer.inl`, `types.hpp`/`types.inl`, `errors.hpp`, `internal.hpp`,
`read_job_queue.hpp`, `intervaltree.hpp`, `crc32.hpp`). One translation unit defines
`MCAP_IMPLEMENTATION` before including `mcap/reader.hpp` or `mcap/mcap.hpp` and thereby pulls in
the `.inl` files. The format specification is `website/docs/spec/index.md`; read `AGENTS.md` for the
project's design principles (bounded memory when reading).

Build and test the C++ code the way upstream does:

```
cd /workspace/repo/cpp
./build.sh --build-tests-only          # Conan (offline, cache is warm) + CMake, clang 14, Debug
./test/build/Debug/bin/unit-tests
./test/build/Debug/bin/unit-tests-nocompress
```

Binaries land in `cpp/test/build/Debug/bin/`: `unit-tests`, `unit-tests-nocompress` (built with
`MCAP_COMPRESSION_NO_LZ4` and `MCAP_COMPRESSION_NO_ZSTD`), `indexed-reader-conformance`,
`streamed-reader-conformance`, `streamed-writer-conformance`. Sources are in `cpp/test/`. The
compiler flags are in `cpp/test/CMakeLists.txt`: `-Wall -Wextra -pedantic -Wshadow -Wpointer-arith
-Werror`, C++17. gcc 11 is also installed (`CC=gcc CXX=g++ ./build.sh --build-tests-only`). There is
no network access; do not try to add dependencies.

The Go, Rust, Python, TypeScript and Swift implementations of the format are present in the tree
as reference material. Their toolchains are not installed, so they cannot be run. The cross-language
conformance corpus (`tests/conformance/data`) is not present.

## What is missing

The C++ reader's index-based read path has been removed. The streamed (file-order) path is
complete and must keep working. Concretely, in `cpp/mcap/include/mcap/`:

* `McapReader::readSummary()` returns `StatusCode::NotImplemented` for every method. Nothing
  populates `footer()`, `statistics()`, `chunkIndexes()`, `attachmentIndexes()`,
  `metadataIndexes()` or the Summary copies of `schemas()`/`channels()`.
* `McapReader::byteRange()` always returns the whole Data section.
* `IndexedMessageReader` sets `StatusCode::NotImplemented` and yields nothing, so
  `McapReader::readMessages()` with `ReadOrder::LogTimeOrder` or `ReadOrder::ReverseLogTimeOrder`
  produces no messages.
* `internal::ReadJobQueue` (`read_job_queue.hpp`) is a shell whose `push()` discards its argument.
* `StatusCode::NotImplemented` was added to `errors.hpp` as a placeholder. Keep it or remove it,
  but nothing may return it when you are done.

Your job is to implement this path so that the library reads indexed MCAP files exactly as the
specification and the other implementations in this repository do. The behavioural contract is
spelled out below; every item is checked.

## API you must keep

All declarations currently in `reader.hpp` are public API and must keep their signatures (they are
documented there). In particular:

```cpp
enum struct ReadSummaryMethod { NoFallbackScan, AllowFallbackScan, ForceScan };

struct ReadMessageOptions {
  Timestamp startTime = 0;              // inclusive
  Timestamp endTime = MaxTime;          // exclusive
  std::function<bool(std::string_view)> topicFilter;   // empty = all topics
  enum struct ReadOrder { FileOrder, LogTimeOrder, ReverseLogTimeOrder };
  ReadOrder readOrder = ReadOrder::FileOrder;
  Status validate() const;
};

class McapReader {
  Status open(IReadable&); Status open(std::string_view); Status open(std::ifstream&);
  void close();
  Status readSummary(ReadSummaryMethod method, const ProblemCallback& onProblem = [](const Status&) {});
  LinearMessageView readMessages(Timestamp startTime = 0, Timestamp endTime = MaxTime);
  LinearMessageView readMessages(const ProblemCallback& onProblem, Timestamp startTime = 0, Timestamp endTime = MaxTime);
  LinearMessageView readMessages(const ProblemCallback& onProblem, const ReadMessageOptions& options);
  std::pair<ByteOffset, ByteOffset> byteRange(Timestamp startTime, Timestamp endTime = MaxTime) const;
  IReadable* dataSource();
  const std::optional<Header>& header() const;
  const std::optional<Footer>& footer() const;
  const std::optional<Statistics>& statistics() const;
  const std::unordered_map<ChannelId, ChannelPtr> channels() const;
  const std::unordered_map<SchemaId, SchemaPtr> schemas() const;
  ChannelPtr channel(ChannelId) const;  SchemaPtr schema(SchemaId) const;
  const std::vector<ChunkIndex>& chunkIndexes() const;
  const std::multimap<std::string, MetadataIndex>& metadataIndexes() const;
  const std::multimap<std::string, AttachmentIndex>& attachmentIndexes() const;
  // static ReadRecord/ReadFooter/Parse* helpers: unchanged and available to you
};

struct IndexedMessageReader {
  IndexedMessageReader(McapReader& reader, const ReadMessageOptions& options,
                       const std::function<void(const Message&, RecordOffset)> onMessage);
  bool next();          // true when a message was delivered to onMessage
  Status status() const;
};
```

`LinearMessageView` constructs an `IndexedMessageReader` for the two log-time orders and a
`TypedRecordReader` for `FileOrder`; keep that wiring (see `LinearMessageView::Iterator::Impl` in
`reader.inl`). Private members of `McapReader` and `IndexedMessageReader` are yours to change.

`internal::ReadJobQueue` is exercised directly by upstream unit tests, so its shape is required too:

```cpp
namespace mcap::internal {
struct ReadMessageJob     { Timestamp timestamp; RecordOffset offset; size_t chunkReaderIndex; };
struct DecompressChunkJob { Timestamp messageStartTime; Timestamp messageEndTime;
                            ByteOffset chunkStartOffset; ByteOffset messageIndexEndOffset; };
using ReadJob = std::variant<ReadMessageJob, DecompressChunkJob>;
struct ReadJobQueue {
  explicit ReadJobQueue(bool reverse);
  void push(DecompressChunkJob&&); void push(ReadMessageJob&&);
  ReadJob pop(); size_t len() const;
};
}
```

You may add new headers, but only files inside `cpp/mcap/include/mcap/` are collected. Everything
must compile warning-free with the flags above, with and without the two `MCAP_COMPRESSION_NO_*`
macros, under clang 14. Only lz4 and zstd may be used for decompression, and only behind those
macros as the existing code does.

## Behavioural contract

Terminology: "Data section start" is the offset just after the Header record; the Footer record
occupies the last `1 + 8 + 20 + 8 = 37` bytes of the file (opcode, length, body, trailing magic).

### R1. `readSummary()`

* R1.1 If the reader is not open, return `StatusCode::NotOpen`. Every non-success `Status`
  returned by `readSummary()` is also delivered to `onProblem` before returning.
* R1.2 `NoFallbackScan` and `AllowFallbackScan` first attempt the Summary section:
  * R1.2.1 Read the Footer at `fileSize - 37`: the trailing magic must match (`MagicMismatch`),
    the opcode must be Footer (`InvalidFile`), the record length must be 20 (`InvalidRecord`).
    The parsed footer becomes `footer()`.
  * R1.2.2 A `summary_start` of 0 means "no Summary records" and is treated as the Footer's
    offset (`fileSize - 37`); likewise `summary_offset_start`. If `summary_offset_start` is
    smaller than `summary_start`, return `InvalidFooter` without changing any other state.
  * R1.2.3 Parse every record in `[summary_start, summary_offset_start)` with the same record
    parsing the streamed reader uses. Schema and Channel records go into `schemas()`/`channels()`
    keyed by id (an id already present is not replaced). Chunk Index records go into
    `chunkIndexes()`, kept sorted ascending by `chunkStartOffset` regardless of the order in the
    file; a second Chunk Index with an already-present `chunkStartOffset` is dropped. Attachment
    Index and Metadata Index records go into the multimaps keyed by `name` (duplicate names are
    kept). The Statistics record becomes `statistics()`. Chunk, attachment and metadata indexes
    from any earlier call are discarded before parsing. A record that fails to parse makes
    `readSummary()` return that parse status.
  * R1.2.4 After the section has been read, the end of the Data section is `summary_start`
    (this is what `byteRange()` and file-order iteration use as the upper bound).
  * R1.2.5 If the section contains no Statistics record (including when the file has no Summary
    section at all), the attempt fails with `MissingStatistics` and `statistics()` stays empty,
    but everything parsed in R1.2.3 remains available through the accessors. With
    `NoFallbackScan` that status is returned. Index-based reading (R3) must work in this state:
    the conformance corpus contains indexed files without Statistics that are read this way.
* R1.3 `AllowFallbackScan` falls back to a scan (R1.4) whenever the section attempt did not
  return Success, for any reason including `MissingStatistics` and `InvalidFooter`. `ForceScan`
  always scans and never touches the Summary section.
* R1.4 The scan reads the Data section from its start to the Data End record with the streamed
  reader (walking into chunks), discarding previously stored schemas, channels and indexes:
  * Schema and Channel records found in or outside chunks populate `schemas()`/`channels()`.
  * One `ChunkIndex` per Chunk record, in file order: `messageStartTime`, `messageEndTime`,
    `compression`, `compressedSize` and `uncompressedSize` copied from the Chunk;
    `chunkStartOffset` = file offset of the Chunk record; `chunkLength` = the full record length
    `9 + 8 + 8 + 8 + 4 + (4 + compression.size()) + 8 + compressedSize`; `messageIndexOffsets`
    empty and `messageIndexLength` 0. Message Index records are not consulted by the scan.
  * One `AttachmentIndex` per Attachment record and one `MetadataIndex` per Metadata record,
    built with the `AttachmentIndex(const Attachment&, ByteOffset)` and
    `MetadataIndex(const Metadata&, ByteOffset)` constructors in `types.hpp`.
  * `statistics()` computed from what was seen: `messageCount` = number of Message records
    (chunked or not), `channelMessageCounts` with an entry only for channels that have at least
    one message, `messageStartTime`/`messageEndTime` = minimum/maximum `log_time` (both 0 when
    there are no messages), `schemaCount`/`channelCount` = number of distinct ids seen,
    `attachmentCount`, `metadataCount`, `chunkCount`.
  * The end of the Data section becomes the offset of the Data End record.
  * If any record cannot be parsed or a Chunk cannot be decoded (an unrecognized compression
    string yields `UnrecognizedCompression`), the scan stops and `readSummary()` returns that
    status with `statistics()` empty and the summary not considered parsed.
* R1.5 On Success the summary counts as parsed: R2 narrows, and a later call re-parses.

### R2. `byteRange(startTime, endTime)`

* R2.1 Before a successful `readSummary()`, or when `chunkIndexes()` is empty, return
  `{Data section start, Data section end}` where the end is the current value: `fileSize - 37`
  after `open()`, `summary_start` after R1.2, the Data End offset after R1.4.
* R2.2 Otherwise consider the chunk indexes whose time range overlaps the query, both bounds
  inclusive: `messageEndTime >= startTime && messageStartTime <= endTime`. Return
  `{min chunkStartOffset, max (chunkStartOffset + chunkLength)}` over them, or `{0, 0}` when none
  overlaps.
* R2.3 `readMessages()` in `FileOrder` iterates exactly the byte range
  `byteRange(options.startTime, options.endTime)` and never parses the Summary section itself.
  Hence, without a prior `readSummary()`, file-order reading visits the whole Data section
  (including Message records outside chunks); after one, records outside the span of the
  overlapping chunks are never visited. Messages are still filtered by `[startTime, endTime)`
  and `topicFilter` individually.

### R3. Log-time ordered reading

`readMessages()` with `LogTimeOrder` or `ReverseLogTimeOrder` goes through `IndexedMessageReader`
and uses the Chunk Index and Message Index records; it never scans the Data section for messages.

* R3.1 If `chunkIndexes()` is empty when the reader is constructed, it calls
  `readSummary(ReadSummaryMethod::AllowFallbackScan)` first. A failure becomes the reader's
  `status()`: `next()` returns false, the view delivers the status to `onProblem` once and ends.
  (The corpus runner in `cpp/test/indexed_reader_conformance.cpp` shows the intended usage:
  `readSummary(NoFallbackScan)` first, then a `LogTimeOrder` read.)
* R3.2 If there are still no chunk indexes, or none of them has `messageIndexLength > 0`, the
  status is `NoMessageIndexesAvailable` and no message is yielded. This is the outcome for
  unchunked files, for chunked files whose Summary lacks Chunk Index records or whose Chunk
  Index records carry no message-index information, and for files whose summary could only be
  obtained by the scan of R1.4.
* R3.3 A channel is selected when `topicFilter` is empty or returns true for its topic. A
  message is selected when its channel is selected and `startTime <= log_time < endTime`.
* R3.4 Chunks with `messageStartTime >= endTime` or `messageEndTime < startTime` are skipped,
  and so are chunks whose `messageIndexOffsets` contains no selected channel. Nothing is read
  from skipped chunks.
* R3.5 For a chunk that is used, the bytes `[chunkStartOffset, chunkStartOffset + chunkLength +
  messageIndexLength)` hold the Chunk record followed by its Message Index records; any other
  record type in that span is `InvalidRecord`. The chunk's `records` are decoded according to
  its `compression` field: `""`, `"lz4"` and `"zstd"` are supported (the latter two only when the
  corresponding `MCAP_COMPRESSION_NO_*` macro is not defined), any other string is
  `UnrecognizedCompression`; decoding failures surface as `DecompressionFailed` or
  `DecompressionSizeMismatch`. Message records are located through the Message Index entries of
  selected channels whose timestamp is within the window; the `offset` of an entry is relative
  to the start of the uncompressed `records` data and the record found there must be a Message
  (`InvalidRecord` otherwise). Records inside a chunk are never decoded speculatively to find
  messages, and messages stored outside chunks are not reachable through this path.
* R3.6 Ordering. `LogTimeOrder` yields messages by ascending `log_time`; among equal
  `log_time`, the message earlier in the file comes first (earlier chunk by `chunkStartOffset`,
  then smaller offset within the chunk). `ReverseLogTimeOrder` yields descending `log_time`;
  among equal `log_time`, the message later in the file comes first. This must hold across
  chunks whose time ranges overlap or that appear out of time order in the file: a chunk has
  to be considered before any message with `log_time >= messageStartTime` is yielded (reverse:
  `log_time <= messageEndTime`).
* R3.7 Each yielded `MessageView` carries a copy of the Message valid until the iterator is
  advanced, `channel` = the reader's Channel for `message.channelId`, `schema` = the reader's
  Schema for `channel->schemaId`, or null when `schemaId == 0`. A message whose channel is
  unknown is reported through `onProblem` as `InvalidChannelId` and skipped; a non-zero schema id
  that is unknown is `InvalidSchemaId` and skipped. `messageOffset.offset` is the offset of the
  Message record within the uncompressed chunk data (as in the Message Index) and
  `messageOffset.chunkOffset` is the file offset of the Chunk record.
* R3.8 Any error after construction sets `status()`, makes `next()` return false and reaches
  `onProblem` through the view. `status()` is Success while messages are still being yielded and
  after a clean end.
* R3.9 Respect `AGENTS.md`: hold a decompressed chunk only while it still has selected messages
  to yield; do not load the whole file or all messages into memory.
* R3.10 After an index-based read, `statistics()` reflects whatever `readSummary()` produced
  (present after a section parse with Statistics or after a scan; absent when the caller's own
  `NoFallbackScan` returned `MissingStatistics`).

### R4. `internal::ReadJobQueue`

* R4.1 `ReadJobQueue(false)`: `pop()` returns the queued job with the smallest key; the key of a
  `ReadMessageJob` is `timestamp`, of a `DecompressChunkJob` its `messageStartTime`. Ties break
  towards the smaller position: `ReadMessageJob::offset` for messages, `chunkStartOffset` (as a
  file-level `RecordOffset`) for chunks.
* R4.2 `ReadJobQueue(true)`: `pop()` returns the job with the largest key; a
  `DecompressChunkJob`'s key is `messageEndTime`. Ties break towards the larger position
  (`messageIndexEndOffset` for chunks).
* R4.3 `len()` is the number of queued jobs. `RecordOffset` comparison operators are in
  `types.inl`.

### R5. Out of scope, but must not regress

* The writer (`writer.hpp`/`writer.inl`) and the file-order reader are complete; do not change
  their behaviour. `streamed-reader-conformance` and the unit tests that ship in the base commit
  must keep passing.
* CRC fields (`uncompressed_crc`, `data_section_crc`, `summary_crc`) do not have to be
  validated, but non-zero values must be tolerated.
* Unknown trailing bytes at the end of Header, Schema, Channel, Chunk Index, Statistics and
  Summary Offset records must be ignored (the record parsers already do this; do not break it).

## Specification references

All in `website/docs/spec/index.md`: "File Structure" (Magic, Header, Footer, Data Section
including "Use of chunk records", Summary Section, Summary Offset Section); "Footer (op=0x02)";
"Chunk (op=0x06)"; "Message Index (op=0x07)"; "Chunk Index (op=0x08)"; "Data End (op=0x0F)";
"Attachment Index (op=0x0A)"; "Metadata Index (op=0x0D)"; "Statistics (op=0x0B)"; "Summary Offset
(op=0x0E)"; and the diagram "Multiple Messages with Chunk Indices". Serialization of `Map` and
`Array<Tuple<Timestamp, uint64>>` is under "Serialization".

## How your work is checked

Only `cpp/mcap/include/mcap` is collected from your environment. It is copied over a pristine copy
of the repository's `cpp/` directory (your edits to `cpp/test/*` or build files are not used) and
rebuilt with `clang++ -std=c++17 -g -O0 -Wall -Wextra -pedantic -Wshadow -Wpointer-arith -Werror`,
in both compression configurations. Then:

1. The pristine `cpp/test/unit_tests.cpp` of the base commit, which contains test cases that were
   removed from your copy, must pass completely: 18 test cases with compression, 16 without.
2. `indexed-reader-conformance` is run over the 16 variants of the cross-language conformance
   corpus that the C++ indexed runner supports (inputs with messages and the features
   chunked, chunk index, repeated schemas, repeated channels, message index, with and without
   statistics, summary offsets and padding) and compared to the expected JSON exactly as
   `tests/conformance/scripts/run-tests` does.
3. MCAP files produced by an independent writer (zstd, lz4 and uncompressed chunks; many chunks
   with overlapping and out-of-order time ranges and duplicate timestamps; padded records;
   Chunk Index records in shuffled order; attachments and metadata between chunks; channels
   without messages and a message-less chunk; timestamps 0 and 2^63; a file with messages after
   the last chunk; files without Statistics, without a Summary, without Message Index records,
   without chunks; a Footer with `summary_offset_start < summary_start`; a chunk with an unknown
   compression string) are read through `readSummary()` with each method and through
   `readMessages()` in all three orders with time windows and topic filters, and every yielded
   message, offset, status and problem code is compared to the ground truth recorded when the
   files were written. `byteRange()` and the accessor contents after each `readSummary()` method
   are compared the same way.
4. On the indexed files, the Go and Rust indexed readers of this repository (built in the
   verifier) must produce the same message sequence as your `LogTimeOrder` read.
5. Submissions may contain only header files, must not spawn processes, touch the network or
   read outside the input file, and must not reference the verifier's binaries or log paths.
