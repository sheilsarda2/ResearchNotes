//! Contract tests authored for the task verifier. Every assertion here traces to a sentence of
//! `instruction.md` (see `provenance.json` -> `derivability`). They complement the upstream
//! `optimize.rs` tests and only use the public API described in the instruction.

#![expect(clippy::unwrap_used)]

use std::collections::BTreeSet;
use std::num::NonZeroU64;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};

use futures::executor::block_on;
use futures::{StreamExt as _, TryStreamExt as _};

use re_byte_size::SizeBytes as _;
use re_chunk::{ArrowArray as _, Chunk, ChunkId, RowId};
use re_chunk_optimizer::testing::{should_split_chunk, smallest_non_splitting_target};
use re_chunk_optimizer::{
    ColumnSelector, Error, MergeSplitOverride, MergeSplitSettings, OptimizationSettings,
    OwnChunkRule, optimize,
};
use re_chunk_store::LazyStore;
use re_log_encoding::{
    ChunkProvider, ChunkProviderError, InMemoryChunkProvider, RawRrdManifest, RrdManifest,
};
use re_log_types::example_components::{MyColor, MyPoint, MyPoints};
use re_log_types::{EntityPathFilter, StoreId, StoreKind, TimePoint, Timeline};
use re_types_core::{Component as _, ComponentBatch as _, ComponentDescriptor, ComponentIdentifier};

// ---------------------------------------------------------------------------------------------
// Fixtures

fn frame() -> Timeline {
    Timeline::new_sequence("frame")
}

fn store_id() -> StoreId {
    StoreId::new(StoreKind::Recording, "contract_app", "contract_recording")
}

fn points() -> ComponentIdentifier {
    MyPoints::descriptor_points().component
}

fn colors() -> ComponentIdentifier {
    MyPoints::descriptor_colors().component
}

fn untyped_descriptor() -> ComponentDescriptor {
    ComponentDescriptor {
        archetype: None,
        component: "custom".into(),
        component_type: None,
    }
}

fn custom() -> ComponentIdentifier {
    untyped_descriptor().component
}

fn point_chunk(id: u128, entity: &str, times: &[i64], points_per_row: u32) -> Arc<Chunk> {
    let mut builder = Chunk::builder_with_id(ChunkId::from_u128(id), entity);
    for (i, &time) in times.iter().enumerate() {
        builder = builder.with_serialized_batches(
            RowId::from_u128((id << 32) + i as u128 + 1),
            [(frame(), time)],
            [MyPoint::from_iter(0..points_per_row)
                .try_serialized(MyPoints::descriptor_points())
                .unwrap()],
        );
    }
    Arc::new(builder.build().unwrap())
}

fn mixed_chunk(id: u128, entity: &str, times: &[i64], per_row: u32) -> Arc<Chunk> {
    let mut builder = Chunk::builder_with_id(ChunkId::from_u128(id), entity);
    for (i, &time) in times.iter().enumerate() {
        builder = builder.with_serialized_batches(
            RowId::from_u128((id << 32) + i as u128 + 1),
            [(frame(), time)],
            [
                MyPoint::from_iter(0..per_row)
                    .try_serialized(MyPoints::descriptor_points())
                    .unwrap(),
                MyColor::from_iter(0..per_row)
                    .try_serialized(MyPoints::descriptor_colors())
                    .unwrap(),
            ],
        );
    }
    Arc::new(builder.build().unwrap())
}

/// A typed points column next to an untyped `custom` column.
fn typed_and_untyped_chunk(id: u128, entity: &str, times: &[i64]) -> Arc<Chunk> {
    let mut builder = Chunk::builder_with_id(ChunkId::from_u128(id), entity);
    for (i, &time) in times.iter().enumerate() {
        builder = builder.with_serialized_batches(
            RowId::from_u128((id << 32) + i as u128 + 1),
            [(frame(), time)],
            [
                MyPoint::from_iter(0..4)
                    .try_serialized(MyPoints::descriptor_points())
                    .unwrap(),
                MyPoint::from_iter(0..4)
                    .try_serialized(untyped_descriptor())
                    .unwrap(),
            ],
        );
    }
    Arc::new(builder.build().unwrap())
}

fn static_point_chunk(id: u128, entity: &str, points_per_row: u32) -> Arc<Chunk> {
    Arc::new(
        Chunk::builder_with_id(ChunkId::from_u128(id), entity)
            .with_serialized_batches(
                RowId::from_u128(id << 32),
                TimePoint::default(),
                [MyPoint::from_iter(0..points_per_row)
                    .try_serialized(MyPoints::descriptor_points())
                    .unwrap()],
            )
            .build()
            .unwrap(),
    )
}

fn provider_of(chunks: impl IntoIterator<Item = Arc<Chunk>>) -> Arc<InMemoryChunkProvider> {
    Arc::new(InMemoryChunkProvider::new(&store_id(), chunks).unwrap())
}

fn measured(chunk: &Arc<Chunk>) -> u64 {
    chunk.as_ref().total_size_bytes()
}

fn settings(max_bytes: u64) -> OptimizationSettings {
    OptimizationSettings {
        merge_split: Some(MergeSplitSettings {
            max_bytes: NonZeroU64::new(max_bytes).unwrap(),
            max_rows: None,
            max_rows_if_unsorted: None,
        }),
        target_timeline: None,
        own_chunk: Vec::new(),
    }
}

fn disabled() -> OptimizationSettings {
    OptimizationSettings {
        merge_split: None,
        target_timeline: None,
        own_chunk: Vec::new(),
    }
}

fn with_rules(mut s: OptimizationSettings, rules: Vec<OwnChunkRule>) -> OptimizationSettings {
    s.own_chunk = rules;
    s
}

fn collect(provider: Arc<dyn ChunkProvider>, s: &OptimizationSettings) -> Vec<Arc<Chunk>> {
    block_on(optimize(provider, s).unwrap().try_collect()).unwrap()
}

fn row_set(chunks: &[Arc<Chunk>]) -> BTreeSet<(String, RowId)> {
    chunks
        .iter()
        .flat_map(|c| {
            let e = c.entity_path().to_string();
            c.row_ids().map(move |r| (e.clone(), r)).collect::<Vec<_>>()
        })
        .collect()
}

fn cell_set(chunks: &[Arc<Chunk>]) -> BTreeSet<(String, RowId, ComponentIdentifier)> {
    chunks
        .iter()
        .flat_map(|c| {
            let e = c.entity_path().to_string();
            let row_ids: Vec<RowId> = c.row_ids().collect();
            c.components()
                .iter()
                .flat_map(|(&column, serialized)| {
                    let e = e.clone();
                    row_ids
                        .iter()
                        .enumerate()
                        .filter(move |&(i, _)| serialized.list_array.is_valid(i))
                        .map(move |(_, &r)| (e.clone(), r, column))
                        .collect::<Vec<_>>()
                })
                .collect::<Vec<_>>()
        })
        .collect()
}

fn columns_of(chunk: &Chunk) -> BTreeSet<ComponentIdentifier> {
    chunk.components().keys().copied().collect()
}

fn ids(chunks: &[Arc<Chunk>]) -> BTreeSet<ChunkId> {
    chunks.iter().map(|c| c.id()).collect()
}

/// Provider wrapper that counts loads and can be told to misbehave.
struct Wrapped {
    inner: Arc<InMemoryChunkProvider>,
    loads: AtomicU64,
    mode: Mode,
}

#[derive(Clone, Copy)]
enum Mode {
    Normal,
    Fail,
    ReturnNothing,
}

#[derive(thiserror::Error, Debug)]
#[error("simulated provider failure")]
struct Simulated;

#[async_trait::async_trait]
impl ChunkProvider for Wrapped {
    fn manifest(&self) -> &Arc<RrdManifest> {
        self.inner.manifest()
    }

    fn raw_manifest(&self) -> &Arc<RawRrdManifest> {
        self.inner.raw_manifest()
    }

    fn source(&self) -> String {
        self.inner.source()
    }

    async fn load_chunks(&self, ids: &[ChunkId]) -> Result<Vec<Arc<Chunk>>, ChunkProviderError> {
        self.loads.fetch_add(ids.len() as u64, Ordering::Relaxed);
        match self.mode {
            Mode::Normal => self.inner.load_chunks(ids).await,
            Mode::Fail => Err(ChunkProviderError(Box::new(Simulated))),
            Mode::ReturnNothing => Ok(Vec::new()),
        }
    }
}

fn wrapped(chunks: Vec<Arc<Chunk>>, mode: Mode) -> Arc<Wrapped> {
    Arc::new(Wrapped {
        inner: provider_of(chunks),
        loads: AtomicU64::new(0),
        mode,
    })
}

// ---------------------------------------------------------------------------------------------
// Tests

/// `InMemoryChunkProvider`: manifest over the given chunks, loads hand back the very same
/// `Arc`s, unknown ids are an error.
#[test]
fn in_memory_provider_basics() {
    let inputs = vec![
        point_chunk(1, "a", &[0, 1], 4),
        point_chunk(2, "a", &[10, 11], 4),
        point_chunk(3, "b", &[0], 4),
    ];
    let provider = provider_of(inputs.clone());

    assert_eq!(provider.manifest().num_chunks(), 3);
    assert_eq!(provider.raw_manifest().data.num_rows(), 3);

    let loaded = block_on(provider.load_chunks(&[inputs[2].id(), inputs[0].id()])).unwrap();
    assert_eq!(loaded.len(), 2);
    for chunk in &loaded {
        let input = inputs.iter().find(|i| i.id() == chunk.id()).unwrap();
        assert!(Arc::ptr_eq(chunk, input));
    }

    assert!(block_on(provider.load_chunks(&[ChunkId::from_u128(999)])).is_err());
}

/// `LazyStore` is a `ChunkProvider` usable as the optimizer's input; loads issued through the
/// trait are tracked by `chunks_loaded()`.
#[test]
fn lazy_store_is_a_chunk_provider() {
    let inputs = vec![
        point_chunk(1, "a", &[0, 1], 8),
        point_chunk(2, "a", &[10, 11], 8),
        point_chunk(3, "b", &[0, 1], 8),
    ];
    let lazy = Arc::new(LazyStore::new(provider_of(inputs.clone())));
    assert_eq!(lazy.chunks_loaded(), 0);

    let dyn_provider: Arc<dyn ChunkProvider> = Arc::clone(&lazy) as _;
    assert_eq!(dyn_provider.manifest().num_chunks(), 3);

    let outputs = collect(dyn_provider, &settings(1024 * 1024));
    assert_eq!(row_set(&inputs), row_set(&outputs));
    assert_eq!(lazy.chunks_loaded(), inputs.len() as u64);
}

/// A provider that fails to load surfaces as `Error::LoadChunks` carrying the entity and the
/// number of requested chunks; the stream yields no chunk for that run.
#[test]
fn provider_load_failure_is_reported() {
    let inputs = vec![point_chunk(1, "ent", &[0, 1], 8)];
    let provider = wrapped(inputs, Mode::Fail);

    let stream = optimize(Arc::clone(&provider) as _, &settings(1024 * 1024)).unwrap();
    futures::pin_mut!(stream);
    let first = block_on(stream.next()).unwrap();
    match first {
        Err(Error::LoadChunks {
            entity_path,
            num_chunks,
            ..
        }) => {
            assert_eq!(entity_path, "ent".into());
            assert_eq!(num_chunks, 1);
        }
        other => panic!("expected Error::LoadChunks, got {other:?}"),
    }
}

/// A provider that omits a requested chunk surfaces as `Error::MissingChunk` naming that chunk.
#[test]
fn provider_omitting_a_chunk_is_reported() {
    let inputs = vec![point_chunk(7, "ent", &[0, 1], 8)];
    let provider = wrapped(inputs, Mode::ReturnNothing);

    let stream = optimize(Arc::clone(&provider) as _, &settings(1024 * 1024)).unwrap();
    futures::pin_mut!(stream);
    let first = block_on(stream.next()).unwrap();
    match first {
        Err(Error::MissingChunk {
            chunk_id,
            entity_path,
        }) => {
            assert_eq!(chunk_id, ChunkId::from_u128(7));
            assert_eq!(entity_path, "ent".into());
        }
        other => panic!("expected Error::MissingChunk, got {other:?}"),
    }
}

/// Planning reads only the manifest: constructing (and dropping) the stream loads nothing, for
/// both an enabled and a disabled merge/split setting.
#[test]
fn planning_is_pure() {
    let inputs = vec![
        point_chunk(1, "a", &[0, 1], 8),
        point_chunk(2, "a", &[10, 11], 8),
        static_point_chunk(3, "a", 8),
    ];
    for s in [settings(1024), disabled()] {
        let provider = wrapped(inputs.clone(), Mode::Normal);
        let stream = optimize(Arc::clone(&provider) as _, &s).unwrap();
        assert_eq!(provider.loads.load(Ordering::Relaxed), 0);
        drop(stream);
        assert_eq!(provider.loads.load(Ordering::Relaxed), 0);
    }
}

/// `merge_split: None` and no rules: every chunk, static or temporal, is emitted as the same
/// `Arc` it was loaded as, exactly once.
#[test]
fn disabled_optimization_passes_everything_through() {
    let inputs = vec![
        static_point_chunk(1, "a", 4),
        point_chunk(2, "a", &[0, 1], 4),
        point_chunk(3, "a", &[10, 11], 4),
        point_chunk(4, "b", &[0, 1], 4),
    ];
    let outputs = collect(provider_of(inputs.clone()), &disabled());

    assert_eq!(outputs.len(), inputs.len());
    assert_eq!(ids(&inputs), ids(&outputs));
    for output in &outputs {
        let input = inputs.iter().find(|i| i.id() == output.id()).unwrap();
        assert!(Arc::ptr_eq(output, input));
    }
}

/// Static chunks are never merged nor split, whatever the target: they come out whole and
/// identity-preserved, even when they are far larger than the target or share an entity.
#[test]
fn statics_never_merge_or_split() {
    let big = static_point_chunk(1, "s", 4096);
    let small_a = static_point_chunk(2, "t", 2);
    let small_b = static_point_chunk(3, "t", 2);
    let inputs = vec![big.clone(), small_a.clone(), small_b.clone()];

    let target = measured(&small_a) / 4;
    assert!(should_split_chunk(measured(&big), target));

    let outputs = collect(provider_of(inputs.clone()), &settings(target.max(1)));
    assert_eq!(outputs.len(), 3);
    assert_eq!(ids(&inputs), ids(&outputs));
    for output in &outputs {
        assert!(output.is_static());
        let input = inputs.iter().find(|i| i.id() == output.id()).unwrap();
        assert!(Arc::ptr_eq(output, input));
    }
}

/// A run whose chunks each fill the target alone emits them one by one, in run (file) order,
/// identity preserved: the fit test is `accumulated + next <= max_bytes`.
#[test]
fn chunks_that_do_not_fit_together_emit_alone_in_order() {
    let inputs: Vec<Arc<Chunk>> = (0..4)
        .map(|i| point_chunk(i + 1, "ent", &[i as i64 * 10, i as i64 * 10 + 1], 64))
        .collect();
    let size = measured(&inputs[0]);
    for c in &inputs {
        assert_eq!(measured(c), size, "fixture must be uniform");
    }

    let outputs = collect(provider_of(inputs.clone()), &settings(size));
    assert_eq!(outputs.len(), 4);
    for (output, input) in std::iter::zip(&outputs, &inputs) {
        assert!(Arc::ptr_eq(output, input));
    }

    // One byte more than one chunk still fits exactly one; twice the size fits two.
    let outputs = collect(provider_of(inputs.clone()), &settings(2 * size));
    assert_eq!(
        outputs.iter().map(|c| c.num_rows()).collect::<Vec<_>>(),
        vec![4, 4]
    );
    assert_eq!(row_set(&inputs), row_set(&outputs));
}

/// A lone oversized chunk is split into several fresh-id pieces that keep every row; no piece
/// triggers a split at the same target, so a second pass has nothing left to split.
#[test]
fn oversized_chunk_splits_into_fresh_pieces() {
    let times: Vec<i64> = (0..64).collect();
    let input = point_chunk(1, "ent", &times, 128);
    let target = measured(&input) / 4;
    assert!(should_split_chunk(measured(&input), target));

    let outputs = collect(provider_of(vec![input.clone()]), &settings(target));
    assert!(outputs.len() > 1, "expected a split, got {} output", outputs.len());
    for piece in &outputs {
        assert_ne!(piece.id(), input.id());
        assert!(piece.num_rows() >= 1);
        assert!(
            !should_split_chunk(measured(piece), target),
            "piece of {} bytes exceeds the split band at target {target}",
            measured(piece)
        );
    }
    assert_eq!(row_set(std::slice::from_ref(&input)), row_set(&outputs));

    // Re-optimizing the pieces at the same target does not increase the chunk count.
    let second = collect(provider_of(outputs.clone()), &settings(target));
    assert!(second.len() <= outputs.len());
    assert_eq!(row_set(&outputs), row_set(&second));
}

/// No emitted chunk with an unsorted timeline ever holds more rows than `max_rows_if_unsorted`;
/// every row survives.
#[test]
fn unsorted_output_never_exceeds_unsorted_guard() {
    // Sorted 2-row chunks whose file order interleaves their time ranges: any merge of file
    // neighbours is time-unsorted.
    let inputs = vec![
        point_chunk(1, "ent", &[10, 11], 16),
        point_chunk(2, "ent", &[0, 1], 16),
        point_chunk(3, "ent", &[30, 31], 16),
        point_chunk(4, "ent", &[20, 21], 16),
        point_chunk(5, "ent", &[50, 51], 16),
        point_chunk(6, "ent", &[40, 41], 16),
    ];
    let s = OptimizationSettings {
        merge_split: Some(MergeSplitSettings {
            max_bytes: NonZeroU64::new(1 << 40).unwrap(),
            max_rows: None,
            max_rows_if_unsorted: NonZeroU64::new(4),
        }),
        target_timeline: None,
        own_chunk: Vec::new(),
    };
    let outputs = collect(provider_of(inputs.clone()), &s);

    assert!(outputs.len() < inputs.len(), "some merging must happen");
    for output in &outputs {
        if !output.all_timelines_sorted() {
            assert!(output.num_rows() <= 4, "unsorted output has {} rows", output.num_rows());
        }
    }
    assert_eq!(row_set(&inputs), row_set(&outputs));
}

/// `ColumnSelector::Type` matches only columns that carry that type; an untyped column is only
/// reachable through `ColumnSelector::Column`. A rule that matches nothing changes nothing.
#[test]
fn own_chunk_type_selector_ignores_untyped_columns() {
    let input = typed_and_untyped_chunk(1, "ent", &[0, 1]);
    let huge = settings(1 << 40);

    // By type: the typed points column is isolated, the untyped one is the rest.
    let outputs = collect(
        provider_of(vec![input.clone()]),
        &with_rules(
            huge.clone(),
            vec![OwnChunkRule::new(ColumnSelector::Type(MyPoint::name()))],
        ),
    );
    assert_eq!(outputs.len(), 2);
    let column_sets: BTreeSet<_> = outputs.iter().map(|c| columns_of(c)).collect();
    assert_eq!(
        column_sets,
        BTreeSet::from([BTreeSet::from([points()]), BTreeSet::from([custom()])])
    );
    for output in &outputs {
        assert_ne!(output.id(), input.id());
        assert_eq!(output.num_rows(), 2);
    }
    assert_eq!(
        cell_set(std::slice::from_ref(&input)),
        cell_set(&outputs)
    );

    // By column identifier: the untyped column is isolated.
    let outputs = collect(
        provider_of(vec![input.clone()]),
        &with_rules(
            huge.clone(),
            vec![OwnChunkRule::new(ColumnSelector::Column(custom()))],
        ),
    );
    assert_eq!(outputs.len(), 2);
    let column_sets: BTreeSet<_> = outputs.iter().map(|c| columns_of(c)).collect();
    assert_eq!(
        column_sets,
        BTreeSet::from([BTreeSet::from([points()]), BTreeSet::from([custom()])])
    );

    // A rule matching no column of the chunk leaves it untouched.
    let outputs = collect(
        provider_of(vec![input.clone()]),
        &with_rules(
            huge,
            vec![OwnChunkRule::new(ColumnSelector::Type(MyColor::name()))],
        ),
    );
    assert_eq!(outputs.len(), 1);
    assert!(Arc::ptr_eq(&outputs[0], &input));
}

/// Rules are tried in order and the first matching rule decides the column's treatment.
#[test]
fn own_chunk_rules_first_match_wins() {
    let inputs = vec![
        mixed_chunk(1, "ent", &[0, 1], 8),
        mixed_chunk(2, "ent", &[10, 11], 8),
    ];
    let huge = settings(1 << 40);
    let passthrough_colors = OwnChunkRule {
        merge_split: MergeSplitOverride::Passthrough,
        ..OwnChunkRule::new(ColumnSelector::Column(colors()))
    };
    let merge_colors = OwnChunkRule::new(ColumnSelector::Type(MyColor::name()));

    // Passthrough rule first: the two colors slices are emitted as-is, the points merge.
    let outputs = collect(
        provider_of(inputs.clone()),
        &with_rules(
            huge.clone(),
            vec![passthrough_colors.clone(), merge_colors.clone()],
        ),
    );
    let colors_outputs: Vec<_> = outputs
        .iter()
        .filter(|c| columns_of(c) == BTreeSet::from([colors()]))
        .collect();
    let points_outputs: Vec<_> = outputs
        .iter()
        .filter(|c| columns_of(c) == BTreeSet::from([points()]))
        .collect();
    assert_eq!(outputs.len(), 3);
    assert_eq!(colors_outputs.len(), 2);
    assert!(colors_outputs.iter().all(|c| c.num_rows() == 2));
    assert_eq!(points_outputs.len(), 1);
    assert_eq!(points_outputs[0].num_rows(), 4);
    assert_eq!(cell_set(&inputs), cell_set(&outputs));

    // Merging rule first: one colors chunk, one points chunk.
    let outputs = collect(
        provider_of(inputs.clone()),
        &with_rules(huge, vec![merge_colors, passthrough_colors]),
    );
    assert_eq!(outputs.len(), 2);
    assert!(outputs.iter().all(|c| c.num_rows() == 4));
    assert_eq!(
        outputs.iter().map(|c| columns_of(c)).collect::<BTreeSet<_>>(),
        BTreeSet::from([BTreeSet::from([colors()]), BTreeSet::from([points()])])
    );
    assert_eq!(cell_set(&inputs), cell_set(&outputs));
}

/// A rule without an entity filter applies to every entity, the `/__properties` subtree
/// included; a rule filtered with `EntityPathFilter::all()` skips that subtree.
#[test]
fn own_chunk_without_entity_filter_covers_properties_subtree() {
    let inputs = vec![
        mixed_chunk(1, "ent", &[0, 1], 8),
        mixed_chunk(2, "__properties/x", &[0, 1], 8),
    ];
    let huge = settings(1 << 40);
    let columns_by_entity = |outputs: &[Arc<Chunk>], entity: &str| {
        outputs
            .iter()
            .filter(|c| c.entity_path() == &entity.into())
            .map(|c| columns_of(c))
            .collect::<BTreeSet<_>>()
    };
    let split = BTreeSet::from([BTreeSet::from([colors()]), BTreeSet::from([points()])]);
    let mixed = BTreeSet::from([BTreeSet::from([points(), colors()])]);

    let unfiltered = OwnChunkRule::new(ColumnSelector::Type(MyColor::name()));
    let outputs = collect(
        provider_of(inputs.clone()),
        &with_rules(huge.clone(), vec![unfiltered.clone()]),
    );
    assert_eq!(columns_by_entity(&outputs, "ent"), split);
    assert_eq!(columns_by_entity(&outputs, "__properties/x"), split);
    assert_eq!(cell_set(&inputs), cell_set(&outputs));

    let filtered_all = unfiltered.with_entity_filter(EntityPathFilter::all());
    let outputs = collect(provider_of(inputs.clone()), &with_rules(huge, vec![filtered_all]));
    assert_eq!(columns_by_entity(&outputs, "ent"), split);
    assert_eq!(columns_by_entity(&outputs, "__properties/x"), mixed);
    assert_eq!(cell_set(&inputs), cell_set(&outputs));
}

/// Data survival and idempotence on a larger heterogeneous fixture: every non-null cell of the
/// input is in the output exactly once, no output triggers a split at the same target, and a
/// second pass at the same settings never increases the chunk count.
#[test]
fn heterogeneous_fixture_survives_and_converges() {
    // Deterministic pseudo-random fixture.
    let mut state: u64 = 0x9E37_79B9_7F4A_7C15;
    let mut next = move |bound: u64| {
        state = state
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (state >> 33) % bound
    };

    let mut inputs: Vec<Arc<Chunk>> = Vec::new();
    let mut id: u128 = 1;
    for entity in ["a", "b", "c/d"] {
        let mut time: i64 = 0;
        for _ in 0..6 {
            let rows = 1 + next(4) as usize;
            let times: Vec<i64> = (0..rows).map(|i| time + i as i64).collect();
            time += rows as i64 + next(3) as i64;
            let per_row = 4 + next(28) as u32;
            inputs.push(match next(3) {
                0 => point_chunk(id, entity, &times, per_row),
                1 => mixed_chunk(id, entity, &times, per_row),
                _ => typed_and_untyped_chunk(id, entity, &times),
            });
            id += 1;
        }
        inputs.push(static_point_chunk(id, entity, 3));
        id += 1;
    }

    let mut sizes: Vec<u64> = inputs.iter().map(measured).collect();
    sizes.sort_unstable();
    let target = 3 * sizes[sizes.len() / 2];
    let s = with_rules(
        settings(target),
        vec![
            OwnChunkRule::new(ColumnSelector::Type(MyColor::name()))
                .with_entity_filter(EntityPathFilter::parse_forgiving("+ /a")),
        ],
    );

    let pass_1 = collect(provider_of(inputs.clone()), &s);
    assert_eq!(cell_set(&inputs), cell_set(&pass_1));
    for chunk in &pass_1 {
        assert!(chunk.num_rows() >= 1);
        if chunk.entity_path() == &"a".into() && !chunk.is_static() {
            let cols = columns_of(chunk);
            assert!(
                !(cols.contains(&colors()) && cols.len() > 1),
                "colors of entity `a` must sit in chunks of their own: {cols:?}"
            );
        }
        if chunk.num_rows() > 1 {
            assert!(!should_split_chunk(measured(chunk), target));
        }
    }

    let pass_2 = collect(provider_of(pass_1.clone()), &s);
    assert_eq!(cell_set(&pass_1), cell_set(&pass_2));
    assert!(pass_2.len() <= pass_1.len());
}

/// The split band helpers use exact integer arithmetic: a chunk is split when
/// `5 * size > 6 * max_bytes`, and `smallest_non_splitting_target` is the least target for
/// which that is false.
#[test]
fn split_band_helpers_are_exact() {
    assert!(!should_split_chunk(120, 100));
    assert!(should_split_chunk(121, 100));
    assert!(!should_split_chunk(0, 1));
    assert!(should_split_chunk(7, 5));
    assert!(!should_split_chunk(6, 5));

    for size in [1_u64, 5, 6, 7, 100, 120, 121, 1 << 20, (1 << 40) + 3] {
        let target = smallest_non_splitting_target(size);
        assert!(!should_split_chunk(size, target), "size={size} target={target}");
        if target > 0 {
            assert!(
                should_split_chunk(size, target - 1),
                "size={size} target-1={}",
                target - 1
            );
        }
    }
}
