# Harden the PyTorch checkpoint reader in `burn-store`

The repository at `/workspace/repo` is [tracel-ai/burn](https://github.com/tracel-ai/burn) at commit `9cc7f63a` (2026-09-10). The crate `crates/burn-store` loads PyTorch checkpoints (`.pt`/`.pth`) into Burn through `PytorchReader` and `PytorchStore` (`crates/burn-store/src/pytorch/`). A run of bug reports has shown the reader to be unsafe on files it did not write itself and incomplete on files PyTorch does write:

- **#5412** — three fallbacks turn read failures into zero-filled tensors: a storage key that cannot be resolved, a storage offset past the data, and a storage holding fewer elements than the shape declares all load as zeros, so a corrupted or truncated checkpoint produces garbage inference with no error.
- **#5506** — a view whose logical shape is huge and whose strides are zero (an `expand`) passes the bounds check because its storage extent is one element, and materializing it exhausts memory; `try_reserve` does not help because the allocator hands out address space lazily.
- Protocol 4 pickles (`torch.save(..., pickle_protocol=4)`) fail on the `FRAME` opcode.
- ZIP entries are looked up at fixed paths, so `version` and `byteorder` are missed unless the archive uses the one layout the reader guessed, and every tensor triggers a scan of the archive for its storage.
- The pre-0.1.10 TAR container is parsed against an invented layout; the committed TAR fixtures were produced by the same wrong description and do not match real files.
- `_rebuild_tensor_v3`, `_rebuild_parameter_with_state` and correct `_rebuild_from_type_v2` handling are missing, so uint16/32/64 tensors and some tensor subclasses fail or load wrong.
- Unknown Python objects in a checkpoint (numpy scalars, `torch.device`, dtype objects) fail the whole load instead of being skipped.
- Untrusted lengths (string sizes, counts, storage sizes) are trusted for allocation, nesting is unbounded, and truncated files are noticed late or not at all.

Rework the reader so that every behavior below holds. Keep the public API (section 2) intact; the internal layout of `src/pytorch/` is yours to change, and `pickle_reader`/`lazy_data` may become private modules.

## 1. Working constraints

- Edit only `crates/burn-store/src/`. That directory is the only thing collected from your container and graded. `Cargo.toml`, `Cargo.lock`, `crates/burn-store/tests/`, `crates/burn-store/pytorch-tests/` and every other crate are rebuilt from pristine copies; changes there are discarded. In particular **do not add or remove dependencies**: the verifier compiles your sources against the unchanged `crates/burn-store/Cargo.toml` (deps: `burn-core`, `burn-pack`, `burn-tensor` (optional, `pytorch` feature), `byteorder`, `bytes`, `ciborium`, `half`, `hashbrown`, `memmap2`, `num-traits`, `regex`, `serde`, `textdistance`, `thiserror`, `zip 8.6`, `tar 0.4`, `safetensors`; dev-deps `burn-core` with `flex`, `burn-nn`, `tempfile`, `divan`).
- The verifier replaces `crates/burn-store/src/pytorch/tests/` and `crates/burn-store/src/safetensors/tests/` wholesale with its own copies (the upstream test suites for this change) before building. Unit tests you keep inline elsewhere in the crate (`#[cfg(test)] mod tests` in `pickle_reader.rs`, say) may use exactly one item from that directory, `crate::pytorch::tests::reader::test_data_path(filename: &str) -> PathBuf`, which resolves into `src/pytorch/tests/reader/test_data/`; nothing else there is stable. `src/pytorch/mod.rs` must keep declaring `#[cfg(test)] mod tests;` (or `pub mod tests;`) and `src/safetensors/mod.rs` likewise, so the restored directories compile in.
- No network. `cargo test --offline -p burn-store`, `-p burn-pack`, `-p pytorch-tests` and `-p safetensors-tests` build from the pre-fetched cache; other workspace packages (GPU backends, `tch`, `wgpu`) were not fetched and will not build. The first `cargo test -p burn-store` is incremental over a warm `target/`.
- `python3` (standard library only, no torch) is installed so you can assemble test checkpoints by hand the way `src/pytorch/tests/reader/create_tar_format.py` does. PyTorch itself exists only in the grading image; you cannot run it, and the grading image does not run anything of yours except `cargo test`.

## 2. Public interface (must be preserved)

Paths are as they exist today; keep them.

```rust
// burn_store::pytorch  (pub mod pytorch in lib.rs; also re-exported: burn_store::{PytorchStore, PytorchStoreError})
pub use reader::{PytorchError, PytorchReader};
pub use store::{PytorchStore, PytorchStoreError};

// burn_store::pytorch::reader  (pub mod reader)
pub struct PytorchReader { /* private */ }
impl PytorchReader {
    pub fn new<P: AsRef<Path>>(path: P) -> Result<Self, PytorchError>;
    pub fn with_top_level_key<P: AsRef<Path>>(path: P, key: &str) -> Result<Self, PytorchError>;
    pub fn from_reader<R: Read>(reader: R, top_level_key: Option<&str>) -> Result<Self, PytorchError>;
    pub fn keys(&self) -> Vec<String>;
    pub fn get(&self, name: &str) -> Option<&burn_pack::Tensor>;
    pub fn tensors(&self) -> &HashMap<String, burn_pack::Tensor>;
    pub fn into_tensors(self) -> HashMap<String, burn_pack::Tensor>;
    pub fn metadata(&self) -> &PytorchMetadata;
    pub fn len(&self) -> usize;
    pub fn is_empty(&self) -> bool;
    pub fn read_pickle_data<P: AsRef<Path>>(path: P, top_level_key: Option<&str>) -> Result<PickleValue, PytorchError>;
    pub fn load_config<D: DeserializeOwned, P: AsRef<Path>>(path: P, top_level_key: Option<&str>) -> Result<D, PytorchError>;
}
#[derive(Debug)] #[non_exhaustive]
pub enum PytorchError { Io(std::io::Error), Pickle(PickleError), Zip(zip::result::ZipError), Tar(std::io::Error), InvalidFormat(String), KeyNotFound(String), Serde(crate::nested::error::Error) }
// Display prefixes, unchanged: "IO error: ", "Pickle parsing error: ", "Zip archive error: ", "TAR archive error: ",
// "Invalid PyTorch file format: ", "Key not found in PyTorch file: ", "Serde deserialization error: ".
#[derive(Debug, Clone)]
pub struct PytorchMetadata { pub format_version: Option<String>, pub format_type: FileFormat, pub byte_order: ByteOrder,
    pub has_storage_alignment: bool, pub pytorch_version: Option<String>, pub tensor_count: usize, pub total_data_size: Option<usize> }
impl PytorchMetadata { pub fn is_modern_format(&self) -> bool; pub fn is_legacy_format(&self) -> bool; }
#[derive(Debug, Clone, PartialEq)] pub enum FileFormat { Zip, Tar, Legacy, Pickle }
#[derive(Debug, Clone, PartialEq)] pub enum ByteOrder { LittleEndian, BigEndian }
#[derive(Debug, Clone, PartialEq)] pub enum PickleValue { None, Bool(bool), Int(i64), Float(f64), String(String), List(Vec<PickleValue>), Dict(HashMap<String, PickleValue>), Bytes(Vec<u8>) }

// burn_store::pytorch::store — PytorchStore and PytorchStoreError: unchanged API and behavior.
// burn_store::bridge::to_data(&burn_pack::Tensor) -> Result<TensorData, burn_pack::Error>  materializes a tensor.
```

`PickleError` (in `pickle_reader`) keeps a `Display` whose messages for corrupt input mention the problem (`Invalid pickle opcode`, `Pickle stack underflow`, `Invalid data in pickle file`, `Unsupported Python type`, ...). `PytorchError::Pickle`'s Display therefore starts with `Pickle parsing error`, and `InvalidFormat`'s with `Invalid PyTorch file format`; existing tests match on the substrings `Pickle` and `Invalid`.

Tensors come back as `burn_pack::Tensor` values created through `burn_store::bridge::deferred`: `shape`, `dtype` and `name` are known after `new`, and bytes are read from the file only when `bridge::to_data`/`into_data` is called (**R7**). `name` is the dotted path of dictionary keys leading to the tensor.

## 3. File formats the reader must handle

All numbers in every container are little-endian. Only little-endian files are supported; the reader must detect the other case and refuse it rather than guess.

### 3.1 Pickle streams (all containers)

Metadata is a Python pickle. PyTorch writes protocol 2 by default, and callers pick any of 0–5 through `pickle_protocol`. The reader must accept protocols 0 through 5 (**R1**): protocol 0/1 text opcodes (`I`, `L`, `F`, `V`/`S` strings, `g`/`p` memo, `P` text persistent id, `c` GLOBAL, `(`/`t`/`l`/`d` containers), protocol 2 binary opcodes (`\x80 PROTO`, `K`/`M`/`J` ints, `\x8a LONG1`, `\x8b LONG4`, `G` float, `U`/`T` py2 strings, `X` BINUNICODE, `q`/`r` BINPUT, `h`/`j` BINGET, `Q` BINPERSID, `R` REDUCE, `\x81 NEWOBJ`, `b` BUILD, `\x85`–`\x87` TUPLEn, `\x88`/`\x89` bools, `)`/`]`/`}` empties, `e`/`u`/`a`/`s` appends/setitems), protocol 3 `B`/`C` bytes, protocol 4 `\x95 FRAME`, `\x8c SHORT_BINUNICODE`, `\x8d BINUNICODE8`, `\x8e BINBYTES8`, `\x8f EMPTY_SET`, `\x90 ADDITEMS`, `\x91 FROZENSET`, `\x92 NEWOBJ_EX`, `\x93 STACK_GLOBAL`, `\x94 MEMOIZE`, and protocol 5 `\x96 BYTEARRAY8`. `FRAME` is a length prefix that can be skipped. `\x82`–`\x84 EXT1/2/4`, `\x97 NEXT_BUFFER` and `\x98 READONLY_BUFFER` are refused with an error. A `PROTO` above 5, a byte that is not an opcode, a `STOP` with an empty stack, a memo lookup that misses, or a stream that ends before `STOP` is an error, never a panic.

Dictionary keys are `str` or `int`; an `int` key becomes its decimal string (**R10**). `collections.OrderedDict` is built from `REDUCE` (with an optional list of pairs) and may receive a `BUILD` with a state dict (PyTorch's state_dict carries `_metadata` that way); such state without tensors is accepted and ignored.

Tensors reach the pickle through **persistent ids**: a `('storage', storage_class, key, location, numel)` tuple, optionally followed by a sixth element that is `None` or, in early legacy files, `(view_key, offset, size)` describing a view of the root storage. `storage_class` is a `GLOBAL` such as `torch.FloatStorage` or, for element types without a typed storage class, `torch.storage.UntypedStorage` (then `numel` counts bytes). `key` is a `str` (or `int`, normalized to its decimal string) naming the storage in the container. The storage class maps to the element type (**R5**):

| storage class | dtype | | storage class | dtype |
|---|---|---|---|---|
| FloatStorage | F32 | | LongStorage | I64 |
| DoubleStorage | F64 | | IntStorage | I32 |
| HalfStorage | F16 | | ShortStorage | I16 |
| BFloat16Storage | BF16 | | CharStorage | I8 |
| BoolStorage | Bool | | ByteStorage | U8 |

Any other storage class (`QInt8Storage`, ...) is an unsupported type error. A tensor is a `REDUCE` of one of:

- `torch._utils._rebuild_tensor(storage, storage_offset, size, stride)` (PyTorch before 0.4),
- `torch._utils._rebuild_tensor_v2(storage, storage_offset, size, stride, requires_grad, backward_hooks[, metadata])`,
- `torch._utils._rebuild_tensor_v3(storage, storage_offset, size, stride, requires_grad, backward_hooks, dtype[, metadata])`, where `dtype` is a `GLOBAL` `torch.<name>` (`float32`/`float`, `float64`/`double`, `float16`/`half`, `bfloat16`, `int64`/`long`, `int32`/`int`, `int16`/`short`, `int8`, `uint8`, `uint16`, `uint32`, `uint64`, `bool`) and the storage is normally untyped; a typed storage whose class disagrees with `dtype` is an error,
- `torch._utils._rebuild_parameter(data, requires_grad, backward_hooks)` and `_rebuild_parameter_with_state(data, requires_grad, backward_hooks, state)`: the tensor is `data`; a `state` that holds tensors is an error rather than silently dropped,
- `torch._tensor._rebuild_from_type_v2(func, new_type, args, state)`: the tensor is `func(*args)` (a tensor subclass such as `nn.Parameter` or a user class); `state` holding tensors is an error.

`size` and `stride` are tuples of non-negative ints of equal length; `storage_offset` is a non-negative int. Every other `REDUCE`, `NEWOBJ`, `NEWOBJ_EX` or `BUILD` is left as an opaque object and the load continues (**R11**), *unless* the callable's arguments or the `BUILD` state hold a storage or tensor anywhere inside them: then the file holds a tensor this reader cannot represent (sparse `_rebuild_sparse_tensor`, quantized `_rebuild_qtensor`, nested tensors, a whole pickled `nn.Module`) and loading fails with an unsupported-type error at parse time instead of silently omitting the entry.

### 3.2 ZIP container (PyTorch 1.6+)

Detected by the `PK\x03\x04` (or empty-archive `PK\x05\x06`) signature. `torch.save(obj, "name.pt")` writes every entry under a directory named after the file (`name/data.pkl`); saving through a file object uses `archive/`; some tools write at the archive root or under deeper paths. **The directory holding `data.pkl` is the root for every other entry** (**R2**): `<root>data.pkl` (the pickle), `<root>data/<key>` (one raw little-endian storage per key), `<root>version` (the serialized-file format version, e.g. `3`), `<root>byteorder` (`little` or `big`, PyTorch ≥ 2.1), `<root>.format_version` (`1`), `<root>.storage_alignment` and `<root>.data/serialization_id` (recent versions). Entries may be stored or deflated. Metadata (**R3**): `pytorch_version` = trimmed `version` text or `None` when absent; `format_version` = trimmed `.format_version` text or `None`; `has_storage_alignment` = whether `.storage_alignment` exists; `total_data_size` = `Some(sum of the uncompressed sizes of the data/ entries)`; `format_type = Zip`. A `byteorder` of `big` is refused with an error whose message contains `Big-endian` (**R4**); any value other than `little`/`big` is refused with a message containing `Unrecognized byteorder`; a missing entry means little-endian. An archive without any `data.pkl` is an invalid-format error. A storage entry may be larger than any tensor needs (only the window a tensor touches is read, **R7**); one shorter than a tensor needs, or missing, must surface as an error when that tensor is materialized, with the message naming how many elements were required and how many were available in the form `requires {n} elements from storage '{key}', but only {m} are available` (**R7**). When the whole entry is consumed the ZIP CRC must be verified, and a mismatch is an error whose message contains `Invalid checksum` (the `zip` crate's wording) (**R7**).

### 3.3 Legacy container (PyTorch 0.1.10–1.5 and `_use_new_zipfile_serialization=False`)

A sequence of pickles followed by raw storage data: (1) the magic number `0x1950a86a20f9469cfc6c` (`119547037146038801333356`), pickled at whatever protocol the caller chose — protocol ≥ 2 writes it as `LONG1` bytes `6c fc 9c 46 f9 20 6a a8 50 19`, protocol 4 prefixes a `FRAME`, protocols 0/1 write the decimal text `L119547037146038801333356L\n`; detection must accept each encoding (the magic appears within the first 64 bytes) and must consume the pickle rather than skip a fixed number of bytes (**R8**); (2) the protocol version, which must be `1001`; (3) `sys_info`, a dict with `protocol_version`, `little_endian` (bool) and `type_sizes`; (4) the saved object; (5) a list of storage keys in the order their data follows. Then, per key, an `i64` element count followed by the storage bytes, back to back with no index. A `sys_info` whose `little_endian` is `False` is refused with a message containing `Big-endian`; a `sys_info` that is not a dict or lacks a bool `little_endian` is refused with a message containing `little_endian bool` (**R8**). Storage sizes are known only from the persistent ids in the object pickle; the layout must be derived from them and the key list and checked against the file length when the file is opened, so a truncated file fails in `PytorchReader::new` with a message containing `extends beyond the end of the file` (**R8**). When a storage is read, its `i64` count prefix must equal the element count the pickle declared; otherwise the read fails with a message of the form `holds {stored} elements but the pickle declares {declared}` (**R8**). A tensor may legitimately use only a prefix or a window of its storage (uncloned views), and several tensors may share one storage at different offsets. `total_data_size = Some(bytes after the key list)`, `format_type = Legacy`, `pytorch_version`/`format_version` = `None`.

### 3.4 TAR container (PyTorch before 0.1.10)

Detected by `ustar` at offset 257. Entries: `sys_info` (as above; `little_endian` must be true), `storages`, `tensors`, `pickle`. `storages` is: a count pickle; per storage a `(key, location, storage_class)` pickle (key an `int`, normalized to decimal string), an `i64` element count and the raw bytes; then a pickle list of `(view_key, root_key, element_offset, element_count)` storage views, which is present even when empty. `tensors` is: a count pickle; per tensor a `(key, storage_key, tensor_class)` pickle, an `i32` rank, 4 unused bytes, `rank` `i64` sizes, `rank` `i64` strides and an `i64` storage offset. `pickle` is the saved object whose tensors are persistent ids (the decimal `str` of a tensor key) naming entries of the `tensors` table (**R9**). Counts and sizes are untrusted: allocations must be bounded by the entry lengths, a count that exceeds what the entry holds is a `Pickle` error (the table runs out), a storage or view lying outside its data is an invalid-format error, and missing `storages`/`tensors`/`pickle` entries are invalid-format errors. `total_data_size = Some(length of the storages entry)`, `format_type = Tar`.

### 3.5 Plain pickle

Anything else is read as a single pickle (`format_type = Pickle`, `pytorch_version`/`format_version`/`total_data_size` = `None`). It can carry configuration for `read_pickle_data`/`load_config` and yields zero tensors; a persistent id in a plain pickle (or in `from_reader`) is an error, since no container can supply storage bytes (**R13**). An empty or truncated file is an error at open (**R14**).

## 4. Behavioral contract

**R6 — parse-time validation of tensor metadata.** For every tensor, computing `num_elements = Π size` and the storage extent `storage_offset + Σ (size_i − 1)·stride_i + 1` (for non-empty tensors; empty tensors need only their offset) must use checked arithmetic; overflow, mismatched `size`/`stride` ranks, an extent beyond the storage's declared element count (or beyond the view's end when a view tuple is present), and a logical byte length `num_elements × element_size` above `burn_pack::MAX_TENSOR_SIZE` are errors from `PytorchReader::new` (the file is rejected before any tensor is read). This is what closes #5506: a `(2^21, 2^21)` zero-stride view over a 32-element storage must be refused, not materialized.

**R7 — no zero substitution; lazy, bounded reads.** Materializing a tensor reads only the window `[storage_offset, extent)` of its storage, converts a non-contiguous view (any stride pattern, including stride 0 and overlapping `as_strided`) to row-major order, and normalizes bool bytes to 0/1. Any failure to obtain exactly the bytes the view needs — a missing storage, a storage shorter than the extent, a mismatched legacy count, a checksum failure, an I/O error — is an `Err` from `to_data`; no path may pad, zero-fill or truncate silently (#5412). Bytes beyond what the tensor touches are never required to exist and never validated.

**R10 — root selection and naming.** `new(path)` requires a dict at the root; with `with_top_level_key(path, key)` the value under `key` must be a dict, otherwise the error message contains `does not hold a dictionary`; a missing key is `PytorchError::KeyNotFound` whose message lists the available keys (its Display contains `Key` and `not found`). Tensors are collected from the selected dict and every nested dict, named by the keys joined with `.` (`layer1.weight`, `state.0.exp_avg`, `2.0.bias`); values that are lists, tuples or opaque objects are not descended into. `read_pickle_data` returns the selected value as a `PickleValue` (tuples become `List`, tensors/storages/classes/opaque objects become `None`, ints beyond `i64` become `None`), so `read_pickle_data(path, Some("epoch"))` on a checkpoint returns `Int(7)` even though `with_top_level_key(path, "epoch")` fails. `metadata().tensor_count == len()`.

**R12 — resource bounds and robustness.** Length prefixes read from the file (strings, bytes, longs, counts, storage sizes, ZIP entry sizes) never drive an unbounded up-front allocation: reserve at most what the file or entry could contain and grow with bytes that actually arrive. Container nesting depth is capped so a deeply nested pickle (hundreds of thousands of `TUPLE1`) fails with an error instead of overflowing the stack; memo/`DUP` copying is capped by node count and by copied payload bytes so doubling bombs and a large memoized blob fetched many times fail with an error. Malformed input of every kind in this document yields `Err`, never a panic, abort or unbounded memory growth.

**R15 — everything that works today keeps working.** `PytorchStore` (`from_file`, `with_top_level_key`, filtering, key remapping, `allow_partial`, `PyTorchToBurnAdapter`, enum-variant handling) is unchanged; the crate's existing `.pt` fixtures under `src/pytorch/tests/reader/test_data/`, the store tests, the safetensors tests, the integration tests in `crates/burn-store/tests/` and the `pytorch-tests` package (real `nn.Module` checkpoints for Linear, Conv, BatchNorm, GroupNorm, LayerNorm, Embedding, ConvTranspose, buffers, key remapping, enum modules, top-level keys) must all pass unchanged.

## 5. Out of scope

Loading `torch.save(model)` full-module pickles (#5590), sparse/quantized/nested tensors, big-endian files, writing PyTorch files, `burn-pack`, and every other crate. Behavior change accepted upstream: a checkpoint holding a sparse, quantized or nested tensor, or a tensor consumed by an unknown callable, fails at parse time rather than being skipped.

## 6. How the work is graded

A separate offline container with a pristine copy of the repository receives only `crates/burn-store/src`. It restores the two test directories (section 1), adds one integration test, rebuilds `burn-store` with `cargo test --offline --locked`, and requires all of the following, with exact counts:

1. `src/pytorch/tests` (upstream reader and store tests for this change, 108 tests) and `src/safetensors/tests` (52) pass with nothing ignored.
2. The five pristine integration test files in `crates/burn-store/tests/` (20 tests) and the `pytorch-tests` package (37) pass.
3. A **fixture matrix written by PyTorch 2.13 (CPU)** loads and matches: 28 files (ZIP at protocols 2–5 and via a file object, deep/root-level/deflated/no-`version` layouts, legacy at protocols 2 and 4, an early TAR with a storage view, plain pickles), 428 tensors covering every dtype in section 3.1 plus uint16/32/64, nested module state_dicts, optimizer state under int keys, shared storages with offset/stride/expand/transpose/`as_strided` views, scalars, empty tensors, NaN/inf/-0.0/denormals, `nn.Parameter`, a `Tensor` subclass, numpy/`torch.device`/`torch.dtype` objects next to tensors, and storages of several megabytes. For every tensor the name, dtype, shape, element count and the exact row-major bytes of `bridge::to_data` must equal what PyTorch holds; `metadata()` fields (section 3) and `read_pickle_data` ints are checked too.
4. 32 **malformed or unsupported files** — big-endian and unknown `byteorder`, no `data.pkl`, truncated archive, truncated pickle, short/missing/CRC-corrupted storages, the #5506 broadcast bomb, offsets and strides past the storage, typed-storage/dtype mismatch, a tensor fed to an unknown callable, `torch.save(model)`, sparse, quantized, truncated/big-endian/flag-less legacy files, DUP and memo bombs, 200 000-deep nesting, EXT opcodes, protocol 6, stack underflow, a storage persistent id in a plain pickle, absurd/truncated/incomplete TARs, a bare ZIP signature, an empty file — must each be rejected with `Err`. Each runs in its own process under a 6 GiB address-space limit and a 180 s timeout; a panic, abort, OOM kill or timeout fails that case. Cases marked "open" in section 3/4 (everything except the short/missing/CRC-corrupted storage entries) must fail in `PytorchReader::new`.
5. Anti-cheat: your sources must not reference `/logs`, `/tests/`, `/opt/`, `manifest.tsv`, `BURN_PT_`, `fixture_matrix`, spawn processes (`Command::new`), embed files (`include_bytes!`/`include_str!`) or branch on `cfg!(test)`.

The reward is 1 only if every group passes; `score.json` records each group separately.
