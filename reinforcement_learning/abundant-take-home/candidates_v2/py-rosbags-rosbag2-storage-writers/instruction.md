# Implement the rosbag2 storage writer backends (sqlite3 and MCAP) in `rosbags`

You are working in `/workspace/repo`, a checkout of **rosbags 0.11.5** (pure-Python ROS 1 / ROS 2 bag library, Apache-2.0, https://gitlab.com/ternaris/rosbags). The package is installed in editable mode; `python -m pytest tests` runs the test-suite.

`rosbags.rosbag2.Writer` (`src/rosbags/rosbag2/writer.py`) implements the rosbag2 *directory* format: it creates the bag directory, tracks connections and message counts, applies `file`/`message` zstd compression, writes `metadata.yaml`, and delegates the actual storage file to a **storage plugin** selected with `StoragePlugin.SQLITE3` (default) or `StoragePlugin.MCAP`. Both storage plugins are missing: `Sqlite3Writer` in `src/rosbags/rosbag2/storage_sqlite3.py` and `McapWriter` in `src/rosbags/rosbag2/storage_mcap.py` currently raise `NotImplementedError` from every method. The two storage *readers* (`Sqlite3Reader`, `McapReader`), the directory `Reader`, `AnyReader`, the `rosbags-convert` CLI and the `Writer` front-end are complete.

Your task is to implement both storage writers so that bags written by `rosbags` are genuine rosbag2 bags: readable by the reference tooling (rosbag2's sqlite3 plugin schema, the MCAP specification and its reference implementations) and by rosbags' own readers. Requirements are numbered (R1, R2, ...) so that grading failures can be attributed; each is checked.

## Grading (what the verifier does)

The verifier rebuilds a clean tree: it takes **only** `src/rosbags/` from your container, restores the complete upstream test-suite at the base commit (including the writer tests that were removed from this checkout: `tests/rosbags/rosbag2/test_writer.py`, `tests/rosbags/rosbag2/test_roundtrip.py`, and the `test_write_*` tests of `tests/rosbags/rosbag2/test_storage_mcap.py`), and then runs:

1. the full upstream test-suite (all 146 tests must pass, no skips);
2. hidden differential tests that write bags with `rosbags.rosbag2.Writer` (and the storage classes directly) and check them with **mcap-python** (`mcap` 1.4.0, the reference MCAP reader), the foxglove **`mcap` CLI** (`mcap doctor` and `mcap info`, an independent Go implementation), the standard-library **`sqlite3`** module, and rosbags' own readers;
3. round trips of bags **recorded by ROS 2 rosbag2** (sqlite3 schema version 4 and MCAP) through your writers: message payloads, timestamps, topics and deserialized field values must be preserved exactly;
4. anti-tampering checks on `src/rosbags/`: no `pytest`/`_pytest`/`atexit`/`subprocess`/`sys.modules` use, no reference to the verifier's log paths, no `conftest.py` or test files, and no `import mcap` (mcap-python is a reference tool, not a dependency; `rosbags` may only depend on its declared runtime dependencies `apsw`, `lz4`, `numpy`, `ruamel.yaml`, `typing_extensions`, `zstandard` and the standard library).

Reward is binary: every group must pass completely. Only `src/rosbags/` is transferred; changes to tests, `pyproject.toml` or anything else are discarded.

Available in this environment for your own checks: the `mcap` Python package (`from mcap.reader import make_reader` and `mcap.stream_reader.StreamReader`), the `sqlite3` CLI, and two small bags recorded by rosbag2 (ROS 2 rolling) under `/workspace/fixtures/mcap/cdr_test` and `/workspace/fixtures/sqlite3/cdr_test`. The `mcap` CLI is **not** available here. No network access.

## Storage writer interface (both plugins)

The `Writer` front-end drives a storage plugin through the `StorageWriter` protocol declared in `writer.py` (under `TYPE_CHECKING`). Keep the class names, module locations and method signatures exactly as the stubs declare them.

- **R1** `__init__(self, path: Path, compression: CompressionMode)`: `path` is the bag directory (already created and empty). Create the storage file **inside** it named `f'{path.name}.db3'` (sqlite3) or `f'{path.name}.mcap'` (MCAP) and expose that file path as the instance attribute `self.path` (the front-end uses `storage.path` for `metadata.yaml` and for file-level compression). Create no other files.
- **R2** `add_msgtype(self, connection)`: called once per distinct `connection.msgtype`, before the first `add_connection` for that type, in the order types are first used.
- **R3** `add_connection(self, connection, offered_qos_profiles: str)`: called once per connection, in id order (`connection.id` is 1-based). `offered_qos_profiles` is the QoS profile list already serialized to text by the front-end (metadata version 9: block-style YAML list; version 8: legacy string; possibly `'[]'` or `''`). Store this string **verbatim**; never re-serialize it.
- **R4** `write(self, connection, timestamp: int, data: bytes | memoryview)`: append one message. `data` is opaque (in `message` compression mode the front-end already zstd-compressed it) and must be stored byte-for-byte; `timestamp` is nanoseconds.
- **R5** `close(self, version: int, metadata: str)`: `version` is the rosbag2 metadata version (8 or 9); `metadata` is the YAML text of the `rosbag2_bagfile_information` mapping (identical content to `metadata.yaml`). Flush everything and close the file; after `close` returns no file handle, journal or temporary file may remain.
- **R6** Compression modes: `CompressionMode.NONE`, `FILE` and `MESSAGE` are handled entirely by the front-end (the storage writes plain records). `CompressionMode.STORAGE` is storage-level compression: the MCAP writer must implement it with zstd chunk compression (R27); the sqlite3 writer must reject it by raising `rosbags.rosbag2.errors.WriterError` with a message containing `storage-side compression` from `__init__`, **before** creating any file.
- **R7** `connection.msgdef` is a `MessageDefinition(format, data)`; the definition *encoding string* is `'ros2msg'` when `format is MessageDefinitionFormat.MSG` and `'ros2idl'` when it is `MessageDefinitionFormat.IDL`. `connection.digest` is the RIHS01 type hash string, `connection.ext.serialization_format` the message encoding (`'cdr'`).
- **R8** Storage writers must not depend on the front-end's bookkeeping: `McapWriter`/`Sqlite3Writer` are also used directly (e.g. `McapWriter(bagdir, CompressionMode.NONE)`, `add_msgtype`, `add_connection`, `close(0, 'metadata')`) and must produce a valid file even with zero connections or zero messages.

## sqlite3 storage (rosbag2_storage_sqlite3, schema version 4)

The file must be a SQLite database with exactly the schema created by the rosbag2 sqlite3 plugin at schema version 4 (`rosbag2_storage_sqlite3/src/rosbag2_storage_sqlite3/sqlite_storage.cpp`, `SqliteStorage::initialize`), so that `PRAGMA table_info` of every table matches a bag recorded by ROS 2 rolling:

- **R9** Tables and columns (declared types and constraints exactly):
  - `schema(schema_version INTEGER PRIMARY KEY, ros_distro TEXT NOT NULL)`
  - `metadata(id INTEGER PRIMARY KEY, metadata_version INTEGER NOT NULL, metadata TEXT NOT NULL)`
  - `topics(id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, serialization_format TEXT NOT NULL, offered_qos_profiles TEXT NOT NULL, type_description_hash TEXT NOT NULL)`
  - `message_definitions(id INTEGER PRIMARY KEY, topic_type TEXT NOT NULL, encoding TEXT NOT NULL, encoded_message_definition TEXT NOT NULL, type_description_hash TEXT NOT NULL)`
  - `messages(id INTEGER PRIMARY KEY, topic_id INTEGER NOT NULL, timestamp INTEGER NOT NULL, data BLOB NOT NULL)`
  - index `timestamp_idx ON messages (timestamp ASC)`; no other user tables or indexes (SQLite's own `sqlite_*` tables are fine).
- **R10** `schema` holds exactly one row `(4, 'rosbags')` (rosbag2 stores its `$ROS_DISTRO` there; rosbags identifies itself as `rosbags`).
- **R11** `message_definitions` gets one row per `add_msgtype` call, ids `1..n` in call order: `(topic_type = connection.msgtype, encoding = 'ros2msg' | 'ros2idl' per R7, encoded_message_definition = connection.msgdef.data, type_description_hash = connection.digest)`.
- **R12** `topics` gets one row per `add_connection` call: `(id = connection.id, name = connection.topic, type = connection.msgtype, serialization_format = connection.ext.serialization_format, offered_qos_profiles = <the string passed in>, type_description_hash = connection.digest)`.
- **R13** `messages` gets one row per `write` call, ids `1..N` in write order: `(topic_id = connection.id, timestamp, data)`; `data` must round-trip as the identical bytes.
- **R14** `metadata` holds exactly one row, id 1, written at `close`: `(metadata_version = version, metadata = <the YAML text passed in>)`.
- **R15** After `close`, the database is fully committed and consistent (`PRAGMA integrity_check` is `ok`), opens read-only with the standard library, and is read by `rosbags.rosbag2.storage_sqlite3.Sqlite3Reader` with `schema == 4`, by `Reader(bagdir)` and by `Reader(<file>.db3)`.

## MCAP storage (MCAP specification, profile `ros2`, as written by rosbag2_storage_mcap)

Follow the MCAP format specification (https://mcap.dev/spec, https://github.com/foxglove/mcap/blob/main/website/docs/spec/index.md): little-endian integers, `String` = uint32 length-prefixed UTF-8, `Map<string,string>` = uint32 byte-length-prefixed sequence of key/value strings, records framed as `<opcode: uint8><content length: uint64><content>`. Section references below name the spec headings.

**File structure** (spec "File Structure"): `<Magic><Header><Data section><Summary section><Summary Offset section><Footer><Magic>`.

- **R16** Magic: the file starts and ends with the 8 bytes `0x89 'M' 'C' 'A' 'P' 0x30 '\r' '\n'`.
- **R17** Header (op 0x01, first record): `profile = 'ros2'`, `library = f'rosbags-{importlib.metadata.version("rosbags")}'`.
- **R18** Schema (op 0x03): one per `add_msgtype`, `id` starting at 1 in call order, `name = connection.msgtype`, `encoding` per R7, `data = connection.msgdef.data` (UTF-8 bytes).
- **R19** Channel (op 0x04): one per `add_connection`, `id = connection.id`, `schema_id` = id of the schema whose name equals `connection.msgtype`, `topic = connection.topic`, `message_encoding = connection.ext.serialization_format`, `metadata` = a map with exactly one entry `offered_qos_profiles -> <the string passed in>` (rosbag2_storage_mcap stores the serialized QoS under this key).
- **R20** Message (op 0x05): `channel_id = connection.id`, `sequence = 0`, `log_time = publish_time = timestamp`, `data` = the bytes passed in.
- **R21** Chunking (spec "Use of chunk records", "Chunk", "Message Index"): Schema, Channel and Message records are never written directly into the data section. They are appended, in call order, to the *pending chunk* (a buffer of framed records). Immediately after appending a Message record, if the pending buffer's **uncompressed** length exceeds 1 MiB (`> 2**20` bytes) the chunk is finalized (R22). At `close`, a non-empty pending buffer is finalized too; an empty one produces no chunk. This rule fixes the chunk boundaries exactly: e.g. one 1 MiB message yields one chunk, a 1 MiB message followed by a small one yields two chunks.
- **R22** Finalizing a chunk writes to the file: one Chunk record (op 0x06) with `message_start_time`/`message_end_time` = smallest/largest `log_time` of the messages in the chunk (see R30 when the chunk has no message), `uncompressed_size` = buffer length, `uncompressed_crc` = 0 or the correct CRC32 of the buffer, `compression` = `''` (plain, `records` = the buffer) or `'zstd'` (R27), `records` = uint64 length-prefixed (possibly compressed) bytes; then, **immediately following the chunk**, one Message Index record (op 0x07) per channel that has at least one message in the chunk, ordered by the channel's first message in the chunk, each listing `(log_time, offset)` for every message of that channel in the chunk in write order, where `offset` is the byte offset of the Message record (its opcode byte) within the uncompressed buffer. Chunks with no messages get no Message Index records.
- **R23** For every chunk keep a Chunk Index record (op 0x08) for the summary: `message_start_time`, `message_end_time` (as in the chunk), `chunk_start_offset` = file offset of the Chunk record's opcode byte, `chunk_length` = total length of the Chunk record including its 9-byte prefix, `message_index_offsets` = map `channel_id -> file offset of that channel's Message Index record` (empty map for a chunk without messages), `message_index_length` = total bytes of the chunk's Message Index records (0 if none), `compression`, `compressed_size` = length of the `records` payload as stored, `uncompressed_size`.
- **R24** At `close`, after the last chunk, write exactly one Metadata record (op 0x0C) with `name = 'rosbag2'` and metadata map `{'serialized_metadata': <the metadata text passed to close>}` (this is how rosbag2_storage_mcap embeds `metadata.yaml`), then the Data End record (op 0x0F) with `data_section_crc = 0` or the correct CRC32 of all bytes before it. Data End is the last record of the data section.
- **R25** Summary section (spec "Summary Section"): after Data End write, grouped by opcode and in exactly this order (omitting groups that are empty): all Schema records (copies of R18), all Channel records (copies of R19), all Chunk Index records in chunk order, one Metadata Index record (op 0x0D: `offset` and `length` of the Metadata record including prefix, `name = 'rosbag2'`), one Statistics record (op 0x0B). The Metadata Index and Statistics groups therefore always exist, so the summary is never empty.
- **R26** Statistics: `message_count` = total messages written, `schema_count` = number of schemas, `channel_count` = number of channels, `attachment_count = 0`, `metadata_count = 1`, `chunk_count` = number of chunks written, `message_start_time`/`message_end_time` = smallest/largest `log_time` over all messages (see R30 when there are none), `channel_message_counts` = map with an entry for **every** channel (zero counts included), sorted by channel id.
- **R27** `CompressionMode.STORAGE` selects `compression = 'zstd'` for every chunk: `records` is a zstd frame (as produced by `zstandard.ZstdCompressor().compress`, any level) that decompresses to the buffer. Any other mode selects `compression = ''`.
- **R28** Summary Offset section (spec "Summary Offset"): one Summary Offset record (op 0x0E) per non-empty summary group, in the group order of R25, with `group_opcode`, `group_start` = file offset of the group's first record and `group_length` = total bytes of the group's records.
- **R29** Footer (op 0x02, 20-byte content): `summary_start` = file offset of the first summary record, `summary_offset_start` = file offset of the first Summary Offset record, `summary_crc = 0` or the correct CRC32 of the bytes from `summary_start` up to and including `summary_offset_start`'s last byte. The Footer is followed only by the trailing magic, so the last 37 bytes of the file are `0x02`, the length `20` as uint64, the 20 footer bytes and the 8 magic bytes.
- **R30** rosbags convention for "no messages": when a chunk contains no Message record its Chunk and Chunk Index `message_start_time`/`message_end_time` are `2**63 - 1` and `0`; likewise the Statistics `message_start_time`/`message_end_time` of a file without messages are `2**63 - 1` and `0`. (This deliberately deviates from the spec's "zero if no messages" and matches the sentinel values used by rosbags' readers; `mcap doctor` accepts it.)
- **R31** Resulting files must be accepted by `mcap doctor`, read by mcap-python's `SeekingReader` (indexed access via Chunk Index and Message Index records, `iter_messages` with topic and time filters) and `NonSeekingReader(validate_crcs=True)` (so every CRC you emit must be correct or zero), and read by `rosbags.rosbag2.storage_mcap.McapReader` (which relies on R22/R23 to derive per-channel counts from `message_index_offsets` and `message_index_length`, on R25/R26 for statistics, and on R29 for the footer), `Reader(bagdir)`, `Reader(<file>.mcap)` and `AnyReader`.

## Behaviour through the `Writer` front-end (already implemented, must keep working)

- **R32** `Writer(path, version=9|8, storage_plugin=...)` as a context manager: `metadata.yaml` reports `storage_identifier` `sqlite3`/`mcap` and `relative_file_paths == ['<name>.db3' | '<name>.mcap']`; `CompressionMode.FILE` leaves only `<name>.<ext>.zstd` plus `metadata.yaml` in the directory; `CompressionMode.MESSAGE` stores zstd frames as message payloads. `Reader` round trips in every mode must return the written `(topic, timestamp, data)` triples in timestamp order with connection `msgtype`, `msgdef`, `digest` and QoS intact.
- **R33** `rosbags-convert --src <bag> --dst <out> --dst-storage sqlite3|mcap` (`python -m rosbags.convert`) must produce bags whose message sets equal the source's, for sources recorded by rosbag2 in either storage.
- **R34** Bags read from rosbag2-recorded fixtures (`Reader` on a sqlite3 or MCAP bag) and re-written with either plugin must contain identical `(topic, timestamp, data)` sets, identical per-topic counts and, after CDR deserialization with the embedded message definitions, identical field values.

## Scope boundaries

- Do not write Attachment records, per-message CRCs or any private record types; CRC fields may be zero.
- Do not change file naming, the `Writer`/`Reader` public API, or `metadata.yaml` content (the front-end owns it).
- Do not add dependencies. Do not import `mcap` inside `src/rosbags/`.
- Keep type annotations and docstrings in the style of the surrounding code; `ruff`/`mypy` are not part of grading.

## References

- MCAP specification: https://mcap.dev/spec (sections "File Structure", "Magic", "Header", "Footer", "Data Section", "Use of chunk records", "Summary Section", "Summary Offset Section", "Records" 0x01-0x0F, "Serialization").
- rosbag2 MCAP plugin conventions: `rosbag2_storage_mcap/src/mcap_storage.cpp` (ros2/rosbag2 commit `08780f9e`) — profile `ros2`, channel metadata key `offered_qos_profiles`, Metadata record `rosbag2` / `serialized_metadata`.
- rosbag2 sqlite3 plugin schema: `rosbag2_storage_sqlite3/src/rosbag2_storage_sqlite3/sqlite_storage.cpp` (`SqliteStorage::initialize`, schema version 4, `update_metadata`).
- rosbags documentation: https://ternaris.gitlab.io/rosbags (Rosbag2 topic), and the readers in `src/rosbags/rosbag2/storage_*.py`, which show exactly what a compliant file must contain.
