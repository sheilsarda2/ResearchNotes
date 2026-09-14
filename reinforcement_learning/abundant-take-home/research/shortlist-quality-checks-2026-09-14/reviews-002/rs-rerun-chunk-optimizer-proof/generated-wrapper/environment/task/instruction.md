# Chunk-index-based, memory-bounded chunk optimizer for Rerun (`re_chunk_optimizer`)

You are working in a checkout of the Rerun repository (`/workspace/repo`, Rust workspace, toolchain
pinned to 1.96.0 by `rust-toolchain`). Your job is to turn the skeleton crate
`crates/store/re_chunk_optimizer` (today: a typed view over an RRD chunk index plus a "mergeability"
analysis used by `rerun rrd stats`) into a working **optimizer**: a pull-based stream that rewrites
the chunks of a recording toward a target chunk layout — merging small chunks, splitting large ones,
and isolating selected component columns into chunks of their own — while never holding the whole
recording in memory and while planning exclusively from the chunk index (the RRD footer manifest).

The recording is reached through `re_log_encoding::ChunkProvider` (index + on-demand chunk loading).
Two small additions to neighbouring crates are part of the job (see R6, R7, R8).

## 0. Ground rules

- **Edit only these directories:** `crates/store/re_chunk_optimizer/src/`,
  `crates/store/re_log_encoding/src/`, `crates/store/re_chunk_store/src/`. Nothing else you change
  is carried over to grading. In particular:
  - Every `Cargo.toml` and `Cargo.lock` is fixed and must not be edited. The manifests of the three
    crates already declare every dependency you need (`futures`, `itertools`, `thiserror`,
    `re_byte_size`, `re_log`, `re_types_core`, `re_chunk`, `re_log_encoding` with the `decoder`
    feature, `async-trait`, `ahash`, ...). Do not add build scripts.
  - Files under any `tests/`, `benches/` or `snapshots/` directory are replaced by pristine copies
    plus hidden tests at grading time; feel free to add your own tests while working, but the
    contract below is what is graded.
  - Do not touch `rerun_py/` or the viewer; they are not built.
- The environment is **offline**. Build and test with `cargo` using the pre-warmed cache, for example
  `cargo test --offline -p re_chunk_optimizer -p re_log_encoding -p re_chunk_store` (the exact
  package selection the grader builds; `re_log_encoding`'s own tests only compile when it is built
  together with `re_chunk_optimizer`). `cargo check --offline -p re_chunk_optimizer --tests` is the
  fast iteration loop. Unit tests inside `src/` are yours; the grader runs integration tests only.
- Keep everything that exists working: `re_chunk_optimizer::{analyze_chunk_index,
  ChunkIndexAnalysis, MergeAssessment, Error}` stay exported and behave as today (a snapshot test
  covers `analyze_chunk_index`), and the existing integration tests of `re_log_encoding` and
  `re_chunk_store` keep passing.
- Useful existing building blocks (already in the tree, read their docs): `re_log_encoding::{ChunkProvider,
  ChunkProviderError, RawRrdManifest, RrdManifest, RrdChunkProvider}`, `RawRrdManifest::{build_in_memory_from_chunks,
  calc_temporal_map, col_*}`, `re_chunk::Chunk::{concatenable, concat_and_sort, split_rows,
  SplitRowsOptions, components_sliced, components_dropped, all_timelines_sorted, components, is_static}`,
  `re_byte_size::SizeBytes`, `re_log_types::{EntityPathFilter, ResolvedEntityPathFilter, TimelineName}`,
  `re_types_core::{ComponentIdentifier, ComponentType, FIELD_METADATA_KEY_COMPONENT,
  FIELD_METADATA_KEY_COMPONENT_TYPE}`.

## 1. Public API (R1)

All of the following must be reachable exactly under these paths and names.

### 1.1 `re_chunk_optimizer`

```rust
pub use settings::{ColumnSelector, MergeSplitOverride, MergeSplitSettings, OptimizationSettings, OwnChunkRule};
pub use optimize::optimize;                 // module names are yours to choose
pub use error::Error;

/// Optimize the chunks of a provider into a pull-based stream. Planning happens here; no chunk is
/// loaded until the stream is polled.
pub fn optimize(
    provider: Arc<dyn re_log_encoding::ChunkProvider>,
    settings: &OptimizationSettings,
) -> Result<impl futures::Stream<Item = Result<Arc<re_chunk::Chunk>, Error>> + use<>, Error>;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OptimizationSettings {
    /// Merge and split chunks toward a target; `None` disables merge/split entirely.
    pub merge_split: Option<MergeSplitSettings>,
    /// Timeline ordering the merge sweep; `None` means file order. Only read when `merge_split` is `Some`.
    pub target_timeline: Option<re_log_types::TimelineName>,
    /// Columns that always get chunks of their own. Rules are tried in order; the first rule whose
    /// entity filter and selector both match a `(entity, column)` decides.
    pub own_chunk: Vec<OwnChunkRule>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MergeSplitSettings {
    /// Byte target for output chunks, in measured (decoded, heap) bytes.
    pub max_bytes: std::num::NonZeroU64,
    /// Row guard for content whose timelines are all sorted; `None` disables it.
    pub max_rows: Option<std::num::NonZeroU64>,
    /// Row guard for content with at least one unsorted timeline; `None` disables it.
    pub max_rows_if_unsorted: Option<std::num::NonZeroU64>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OwnChunkRule {
    /// `None` applies to every entity, including the `/__properties` subtree (which a resolved
    /// `EntityPathFilter::all()` does not match).
    pub entity_filter: Option<re_log_types::EntityPathFilter>,
    pub column: ColumnSelector,
    pub merge_split: MergeSplitOverride,
}
impl OwnChunkRule {
    /// `entity_filter: None`, `merge_split: MergeSplitOverride::Inherit`.
    pub fn new(column: ColumnSelector) -> Self;
    pub fn with_entity_filter(self, entity_filter: re_log_types::EntityPathFilter) -> Self;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ColumnSelector {
    /// Every column carrying this component type, whatever its archetype; an untyped column never matches.
    Type(re_chunk::ComponentType),
    /// This column identifier, typed or not.
    Column(re_chunk::ComponentIdentifier),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MergeSplitOverride {
    /// Use `OptimizationSettings::merge_split` (so `None` there means passthrough).
    Inherit,
    /// Emit every slice of the column as-is, even when the global setting merges.
    Passthrough,
    /// Merge/split the column's slices toward this target, even when the global setting is `None`.
    MergeSplit(MergeSplitSettings),
}

/// Exposed for tests; `#[doc(hidden)]` is fine.
pub mod testing {
    /// `true` iff `5 * size > 6 * max_bytes` (exact integer arithmetic, no overflow for any u64 inputs).
    pub fn should_split_chunk(size: u64, max_bytes: u64) -> bool;
    /// The smallest `t` such that `should_split_chunk(size, t)` is false, i.e. `ceil(5 * size / 6)`.
    pub fn smallest_non_splitting_target(size: u64) -> u64;
}
```

`Error` keeps its existing variants (`ReadColumn { column: &'static str, source: CodecError }`,
`TemporalMap { source: CodecError }`, `UnknownChunkId { chunk_id: ChunkId, entity_path: EntityPath }`)
and gains these, all `#[derive(thiserror::Error, Debug)]`:

```rust
MalformedComponentColumn { column: String, reason: &'static str },
LoadChunks   { entity_path: EntityPath, num_chunks: usize, source: re_log_encoding::ChunkProviderError },
MissingChunk { chunk_id: ChunkId, entity_path: EntityPath },
MergeChunks  { entity_path: EntityPath, source: re_chunk::ChunkError },
IndexMismatch { chunk_id: ChunkId, entity_path: EntityPath, missing: Vec<ComponentIdentifier> },
EmptySelection { chunk_id: ChunkId, entity_path: EntityPath },
```

### 1.2 `re_log_encoding` (R6)

```rust
/// `ChunkProvider` over already-materialized chunks. Re-exported at the crate root
/// (`re_log_encoding::InMemoryChunkProvider`), available without any cargo feature.
pub struct InMemoryChunkProvider { /* private */ }
impl InMemoryChunkProvider {
    pub fn new(store_id: &re_log_types::StoreId, chunks: impl IntoIterator<Item = Arc<Chunk>>) -> CodecResult<Self>;
}
impl ChunkProvider for InMemoryChunkProvider { ... }
```

### 1.3 `re_chunk_store` (R7)

`re_chunk_store::LazyStore` implements `re_log_encoding::ChunkProvider`.

## 2. Vocabulary

- **Chunk index**: the RRD footer manifest (`RawRrdManifest` / `RrdManifest`) — one row per chunk with
  its id, entity, static flag, row count, byte offset/size, and per-(index, component) time-range
  columns (`<...>:start`, `<...>:end`, `<...>:num_rows`) plus per-component `:has_static_data` flags.
- **Column with data** (of a chunk, per the index): a component identifier that has a non-null
  time range on some index for that chunk, or whose static flag is set for that chunk. A column
  that is null on every row of a temporal chunk is invisible to the index and is treated as absent.
- **Group**: the temporal chunks of one entity that carry exactly the same set of timelines. Chunks
  only ever merge within a group.
- **Slice**: a column projection of one chunk (all rows kept). A slice keeping every column of the
  chunk *is* the chunk (same `Arc<Chunk>`, same `ChunkId`). A slice keeping a strict, non-empty
  subset is a new chunk with a fresh `ChunkId`, the same entity, rows, row ids, timelines and
  static-ness (`Chunk::components_sliced` / `components_dropped` produce exactly this).
- **Measured size** of a chunk: `re_byte_size::SizeBytes::total_size_bytes(&chunk)` of the
  decoded chunk. All byte thresholds in this contract are compared against measured sizes.
- **Split band**: a chunk of measured size `s` is *over the band* at target `t` iff
  `should_split_chunk(s, t)`, i.e. `s > 1.2 t` in exact arithmetic.
- **Identity**: an output "is emitted as-is" when the stream yields the very same `Arc<Chunk>` that
  was loaded from the provider (or the very same `Arc` that a slice resolved to), so `Arc::ptr_eq`
  holds and the `ChunkId` is unchanged.

## 3. Planning (R2)

Planning runs inside `optimize` and reads only `provider.raw_manifest()` (and, if you wish,
`provider.manifest()`). It performs **no `load_chunks` call**: constructing the stream, or
constructing and dropping it without polling, loads nothing (R2.1).

R2.2 **Static chunks** are never merged and never split. Each static chunk is emitted as-is (identity),
except that own-chunk rules (section 4) may slice it into column slices, each of which is emitted
as-is (a static mixed chunk under a rule yields two static chunks with fresh ids).

R2.3 **Temporal chunks** are partitioned per entity into groups; each group is swept in an order and
becomes one or more *runs* (section 5), never crossing a group or an entity.

R2.4 **Sweep order** of a group: if `target_timeline` is `Some(name)` and the group carries a timeline
of that name, chunks are ordered by ascending range start on that timeline (ties: range end, then
index order). Otherwise — no target, the group lacks that timeline, or the name matches no timeline
of the recording — file order: ascending chunk byte offset as recorded in the index (for an
`InMemoryChunkProvider` this is the order chunks were passed to `new`).

R2.5 A temporal chunk that the index shows on no timeline at all (a non-static "orphan" whose time
ranges are null on every (index, component) pair) still reaches the output: it is emitted as-is
(subject to own-chunk slicing), never merged.

R2.6 With `merge_split: None` and no applicable own-chunk rule, **every chunk of the recording is
emitted exactly once, as-is** (identity), static and temporal alike.

R2.7 Every non-null cell `(entity, row id, column)` of every input chunk appears in exactly one
output chunk. Rows are never dropped, duplicated, or moved to another entity; no output is empty.

## 4. Own-chunk rules (R3)

R3.1 Rules are resolved per `(entity, column-with-data)`: the rules are tried in `own_chunk` order and
the **first** rule whose entity filter matches the entity and whose selector matches the column
decides that column's treatment for that entity; later rules are ignored for it.

R3.2 Entity filter: `None` matches every entity, including `/__properties/...`. `Some(filter)`
matches an entity iff the filter (resolved without substitutions,
`EntityPathFilter::resolve_without_substitutions`) matches its path; note that a resolved
`EntityPathFilter::all()` does **not** match the `/__properties` subtree.

R3.3 Selector: `ColumnSelector::Column(c)` matches the column identifier `c` whether or not the
column carries a type. `ColumnSelector::Type(t)` matches a column identifier when at least one chunk
of that entity records the column with type `t` (the type comes from the index column metadata,
`FIELD_METADATA_KEY_COMPONENT_TYPE`); the match then applies to the identifier for every chunk of the
entity carrying it. A column recorded without a type never matches a `Type` selector.

R3.4 Effect on a chunk carrying matched ("own") columns: each own column present in the chunk becomes
its own slice (exactly that one column); everything else of the chunk forms one "rest" slice (the
chunk itself if it has no own column; nothing if all its columns are own). Within a group, the
slices of one own column across the group's chunks form that column's run; the rest slices form the
group's rest run. **Own-column runs are emitted before the rest run, in ascending order of the
column identifier (`ComponentIdentifier`'s `Ord`).** The static and orphan chunks' own slices are
emitted as-is (never merged), own slice(s) first, then the rest slice.

R3.5 `MergeSplitOverride` of the deciding rule selects the target for that column's runs:
`Inherit` → `settings.merge_split` (`None` → passthrough); `Passthrough` → every slice emitted as-is;
`MergeSplit(t)` → the run merges/splits toward `t` even when `settings.merge_split` is `None`.

R3.6 Consequently, with merging disabled and a rule for one column of otherwise two-column chunks,
each input chunk yields exactly its two slices (one column each, all rows), and nothing merges.

R3.7 A rule that matches no column of a chunk leaves that chunk untouched (identity).

R3.8 The index may contain two *identically named* sets of per-index columns for one component
identifier (the same identifier logged under a typed descriptor on one entity and an untyped one on
another). Column matching must therefore never be by column name alone (see R8); the typed variant
records the type, the untyped one records "no type"; if both variants have data for the same chunk,
the typed one wins.

## 5. Merge/split runs (R4)

A run is an ordered sequence of input slices with a target `MergeSplitSettings`. It is executed
lazily as the stream is polled (R4.1): chunks are loaded through `provider.load_chunks` in run order,
any batching is allowed, but a run over a single chunk loads exactly that one chunk when it is
first polled, and the whole recording is never loaded up front (memory-bounded: at most one load
batch of a run plus the run's pending output are held). Outputs of a run are emitted in run order,
and a run's outputs are emitted before the next run's.

R4.2 **Splitting on admission.** Before a loaded input with more than one row joins the current
output, it is split if (a) its measured size is over the band (`should_split_chunk(size, max_bytes)`),
or (b) `max_rows` is `Some(n)` and it has more than `n` rows, or (c) `max_rows_if_unsorted` is
`Some(n)`, it has more than `n` rows, and not all its timelines are sorted. Splitting uses
`Chunk::split_rows` with `SplitRowsOptions { chunk_max_bytes: max_bytes, chunk_max_rows: max_rows
or 0, chunk_max_rows_if_unsorted: max_rows_if_unsorted or 0 }` (0 = disabled). The pieces (fresh
`ChunkId`s, all rows preserved) **take the chunk's place in the input sequence**, so an under-target
tail piece may merge with the chunks that follow it. If the split yields a single piece, the
original is admitted as-is. Single-row chunks are never split. A chunk whose measured size lies in
the band `(max_bytes, 1.2 max_bytes]` is not split; it emits alone.

R4.3 **Fit test.** An input `c` (after splitting) joins the current output iff
`accumulated_bytes + measured(c) <= max_bytes` and the row guard holds:
`accumulated_rows + rows(c) <= guard`, where `guard = max_rows` if `c` and everything already
accumulated have all timelines sorted, else `guard = max_rows_if_unsorted`; a guard of `None` never
fails. If `c` does not fit and the current output is non-empty, the current output is finished and
emitted, and `c` starts the next output. An input that does not fit into an *empty* output still
starts it (it will emit alone). `accumulated_bytes` must be the **measured size of the merged
accumulated content**, not the sum of its inputs' sizes: per-chunk framing collapses when chunks
merge, and a run over many tiny chunks must pack to within one chunk of the data floor
`ceil(total_merged_bytes / max_bytes)` — strictly fewer outputs than any first-fit sweep over the
inputs' individual sizes (index sizes or measured sizes) would give.

R4.4 **Merging.** Only run-adjacent content is merged, using `Chunk::concat_and_sort` (fresh
`ChunkId`, rows in `RowId` order, columns unioned with null padding). Two pieces are merged only if
`Chunk::concatenable` holds and the merged result either has all timelines sorted or has at most
`max_rows_if_unsorted` rows (`None` = no limit). A refused merge never errors: the operands stay
separate outputs, in run order. A piece that cannot merge with a neighbour also separates the run —
the content before it and after it are never merged with each other across it.

R4.5 **Identity.** An output that consists of exactly one input slice which was never merged or
split is emitted as that very `Arc<Chunk>` (`Arc::ptr_eq`, same `ChunkId`): a lone chunk in its
group, a chunk that fits with nothing, a band chunk, an oversized chunk that could not be split, a
mismatched chunk. Everything else (merged content, split pieces, strict column slices) has a fresh
`ChunkId`.

R4.6 **Idempotence.** Re-optimizing the output of a run at the same settings splits nothing (every
output is within the band) and, when no two adjacent outputs of one column set fit together under
the target, changes nothing: same chunk count, same set of `ChunkId`s. In general a second pass at
the same settings never increases the chunk count.

R4.7 **Row guards are hard limits on outputs.** No emitted chunk with an unsorted timeline has more
rows than `max_rows_if_unsorted` (when `Some`); no emitted all-sorted chunk built by merging has more
rows than `max_rows` (when `Some`). A merge of two sorted chunks whose result would be unsorted and
over `max_rows_if_unsorted` is refused and both operands are emitted separately (sorted, identity);
with that guard disabled the same merge is kept. A run whose content is unsorted cuts at
`max_rows_if_unsorted` even when `max_rows` is disabled; a sorted run is unaffected by a small
`max_rows_if_unsorted`.

R4.8 **Errors** end the stream with an `Err` item: `Error::LoadChunks { entity_path, num_chunks, source }`
when `load_chunks` fails (entity of the run, number of chunk ids requested);
`Error::MissingChunk { chunk_id, entity_path }` when the provider returns fewer chunks than requested
(the first requested id that is absent); `Error::MergeChunks` when `concat_and_sort` fails;
`Error::IndexMismatch { chunk_id, entity_path, missing }` when a loaded chunk lacks a column the index
recorded for it and a slice needs it; `Error::EmptySelection` when a rest slice would keep no column.

## 6. `InMemoryChunkProvider` (R6)

`InMemoryChunkProvider::new(store_id, chunks)` builds an in-memory chunk index from the chunks with
`RawRrdManifest::build_in_memory_from_chunks` in the **iteration order given** (so file order is
insertion order) and a validated `RrdManifest` from it; `manifest()`/`raw_manifest()` return those
(the raw manifest has one row per chunk); `source()` is a human-readable string mentioning the store
id; `load_chunks(ids)` returns the very same `Arc<Chunk>` values it was constructed with, and an
unknown id is a `ChunkProviderError`.

## 7. `LazyStore` as a provider (R7)

`LazyStore` implements `ChunkProvider` by delegating `manifest`, `raw_manifest` and `source` to its
inner provider, and `load_chunks` through its own chunk-tracking load path so that
`LazyStore::chunks_loaded()` counts chunks loaded via the trait. A `LazyStore` can therefore be
passed to `optimize` as an `Arc<dyn ChunkProvider>` and yields the same rows as its inner provider.
Other crates keep compiling (`re_server` calls `manifest()`, `raw_manifest()` and `source()` on
`LazyStore`; keep these callable either as inherent methods or via the trait).

## 8. Chunk-index temporal map with duplicate column names (R8)

`RawRrdManifest::calc_temporal_map` must read **every** set of per-index component columns, even when
two descriptors share a component identifier and therefore produce identically named `:start`,
`:end` and `:num_rows` columns (e.g. `MyPoints:points` typed under an archetype on entity `real`
and the same identifier untyped on entity `fake`): each chunk appears under its entity, timeline
and component with its row count and time range. The sibling `:end`/`:num_rows` columns of a
`:start` column must be identified by the full field metadata of that column, never by name or by
the `rerun:component` value alone. The view the optimizer builds from the index follows the same
rule (R3.8).

## 9. What is graded

Hidden integration tests, run offline against a clean tree that contains only your three `src/`
directories: the upstream optimizer test-suite (`re_chunk_optimizer/tests/optimize.rs`, 22 tests
over `InMemoryChunkProvider` and `RrdChunkProvider`, covering R2–R5), an upstream
`re_log_encoding` manifest test for R8, authored contract tests for R1–R8 (including R6/R7), the
existing `re_chunk_optimizer` analysis snapshot test, and the existing `re_log_encoding` and
`re_chunk_store` integration tests as regression guards. All tests must compile and pass; exact test
counts are checked. There is no partial credit.
