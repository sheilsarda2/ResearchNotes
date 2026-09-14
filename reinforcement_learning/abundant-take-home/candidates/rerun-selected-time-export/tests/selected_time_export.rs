use std::{collections::BTreeMap, sync::Arc};

use arrow::{array::Array, array::ArrayData};
use re_chunk::{Chunk, RowId};
use re_entity_db::EntityDb;
use re_log_encoding::{DecoderApp, Encoder};
use re_log_types::example_components::{MyColor, MyLabel, MyPoint, MyPoints};
use re_log_types::{
    AbsoluteTimeRangeF, LogMsg, SetStoreInfo, StoreId, StoreInfo, StoreKind, StoreSource,
    TimePoint, Timeline, TimelineName,
};

#[derive(Debug, PartialEq)]
struct Row {
    entity: String,
    times: BTreeMap<String, i64>,
    timeline_types: BTreeMap<String, re_log_types::TimeType>,
    components: BTreeMap<String, Option<ArrayData>>,
}

type Rows = BTreeMap<RowId, Row>;

fn append_rows(rows: &mut Rows, chunk: &Chunk) {
    chunk.sanity_check().unwrap();
    for (i, id) in chunk.row_ids().enumerate() {
        let row = Row {
            entity: chunk.entity_path().to_string(),
            times: chunk
                .timelines()
                .iter()
                .map(|(name, col)| (name.to_string(), col.times_raw()[i]))
                .collect(),
            timeline_types: chunk
                .timelines()
                .iter()
                .map(|(name, col)| (name.to_string(), col.timeline().typ()))
                .collect(),
            components: chunk
                .components()
                .values()
                .map(|column| {
                    let value = (!column.list_array.is_null(i))
                        .then(|| column.list_array.value(i).to_data());
                    (format!("{:?}", column.descriptor), value)
                })
                .collect(),
        };
        assert!(
            rows.insert(id, row).is_none(),
            "an exported row was duplicated"
        );
    }
}

fn inspect(messages: &[LogMsg]) -> Rows {
    let mut rows = Rows::new();
    for message in messages {
        if let LogMsg::ArrowMsg(_, msg) = message {
            let chunk = Chunk::from_arrow_msg(msg).unwrap();
            assert!(!chunk.is_empty(), "selection emitted an empty chunk");
            append_rows(&mut rows, &chunk);
        }
    }
    rows
}

fn messages(db: &EntityDb, selection: Option<(&str, AbsoluteTimeRangeF)>) -> Vec<LogMsg> {
    db.to_messages(selection.map(|(name, range)| (TimelineName::try_new(name).unwrap(), range)))
        .collect::<Result<_, _>>()
        .unwrap()
}

fn fixture(chunks: Vec<Chunk>, kind: StoreKind) -> (EntityDb, Rows) {
    let mut db = EntityDb::new(StoreId::random(kind, "selected_time_export"));
    let mut expected = Rows::new();
    for chunk in chunks {
        append_rows(&mut expected, &chunk);
        db.add_chunk(&Arc::new(chunk)).unwrap();
    }
    assert_eq!(
        inspect(&messages(&db, None)),
        expected,
        "unfiltered positive control"
    );
    (db, expected)
}

fn points(entity: &str, timeline: &str, times: &[i64]) -> Chunk {
    let mut builder = Chunk::builder(entity);
    for (i, &time) in times.iter().enumerate() {
        builder = builder.with_archetype(
            RowId::new(),
            TimePoint::from_iter([(
                Timeline::new_sequence(TimelineName::try_new(timeline).unwrap()),
                time,
            )]),
            &MyPoints::new([MyPoint::new(i as f32, time as f32)]),
        );
    }
    builder.build().unwrap()
}

fn selected(expected: &Rows, timeline: &str, min: i64, max: i64) -> Rows {
    // Expected values are reconstructed directly from fixture rows, independently of chunk slicing.
    expected
        .iter()
        .filter(|(_, row)| {
            row.times.is_empty()
                || row
                    .times
                    .get(timeline)
                    .is_some_and(|t| min <= *t && *t <= max)
        })
        .map(|(id, row)| {
            (
                *id,
                Row {
                    entity: row.entity.clone(),
                    times: row.times.clone(),
                    timeline_types: row.timeline_types.clone(),
                    components: row.components.clone(),
                },
            )
        })
        .collect()
}

fn assert_selection(
    db: &EntityDb,
    expected: &Rows,
    timeline: &str,
    range: AbsoluteTimeRangeF,
    min: i64,
    max: i64,
) {
    let before = messages(db, None);
    let exported = messages(db, Some((timeline, range)));
    assert!(
        exported
            .iter()
            .all(|message| message.store_id() == db.store_id())
    );
    assert_eq!(inspect(&exported), selected(expected, timeline, min, max));
    assert_eq!(
        messages(db, None),
        before,
        "selected export changed the original database"
    );
    let bytes = Encoder::encode(exported.into_iter().map(Ok)).unwrap();
    let decoded = DecoderApp::decode_lazy(bytes.as_slice())
        .collect::<Result<Vec<_>, _>>()
        .unwrap();
    assert_eq!(
        inspect(&decoded),
        selected(expected, timeline, min, max),
        "RRD roundtrip changed rows or sparse values"
    );
    assert!(
        decoded
            .iter()
            .all(|message| message.store_id() == db.store_id())
    );
    let mut reloaded = EntityDb::new(db.store_id().clone());
    for message in &decoded {
        reloaded.add_log_msg(message).unwrap();
    }
    assert_eq!(
        inspect(&messages(&reloaded, None)),
        selected(expected, timeline, min, max),
        "export cannot be reloaded into EntityDb"
    );
}

#[test]
fn sorted_closed_interval_and_point_boundaries() {
    let (db, expected) = fixture(
        vec![points("pose", "frame", &[-4, -1, 0, 2, 4, 6, 8, 10])],
        StoreKind::Recording,
    );
    assert_selection(
        &db,
        &expected,
        "frame",
        AbsoluteTimeRangeF::new(0_i64, 6_i64),
        0,
        6,
    );
    assert_selection(
        &db,
        &expected,
        "frame",
        AbsoluteTimeRangeF::point(4_i64),
        4,
        4,
    );
}

#[test]
fn unsorted_duplicate_times_keep_every_matching_row() {
    let (db, expected) = fixture(
        vec![points("pose", "frame", &[8, 2, 7, 4, 4, -3, 6, 20])],
        StoreKind::Recording,
    );
    assert_selection(
        &db,
        &expected,
        "frame",
        AbsoluteTimeRangeF::new(4_i64, 7_i64),
        4,
        7,
    );
}

#[test]
fn sparse_empty_lists_and_secondary_timelines_stay_aligned() {
    let mut builder = Chunk::builder("sparse/points");
    for (i, time) in [9, 5, 1, 6, 3, 8, 4].into_iter().enumerate() {
        let mut value = if i == 1 {
            MyPoints::new([] as [MyPoint; 0])
        } else if i % 3 == 0 {
            MyPoints::update_fields()
        } else {
            MyPoints::new((0..i).map(|n| MyPoint::new(n as f32, i as f32)))
        };
        if i % 2 == 0 {
            value = value.with_colors([MyColor(i as u32)]);
        }
        if i == 3 {
            value = value.with_labels([MyLabel("selected".into()), MyLabel("labels".into())]);
        }
        if i == 5 {
            value = value.with_labels([] as [MyLabel; 0]);
        }
        builder = builder.with_archetype(
            RowId::new(),
            TimePoint::from_iter([
                (Timeline::new_sequence("frame"), time),
                (Timeline::new_duration("sensor"), 100 - 7 * i as i64),
                (
                    Timeline::new_timestamp("wall_clock"),
                    1_000_000_000 + 101 * i as i64,
                ),
            ]),
            &value,
        );
    }
    let (db, expected) = fixture(vec![builder.build().unwrap()], StoreKind::Recording);
    assert_selection(
        &db,
        &expected,
        "frame",
        AbsoluteTimeRangeF::new(4_i64, 6_i64),
        4,
        6,
    );
    assert_selection(
        &db,
        &expected,
        "sensor",
        AbsoluteTimeRangeF::new(58_i64, 86_i64),
        58,
        86,
    );
}

#[test]
fn multiple_chunks_entities_static_rows_and_absent_timeline() {
    let static_chunk = Chunk::builder("calibration")
        .with_archetype(
            RowId::new(),
            TimePoint::default(),
            &MyPoints::new([MyPoint::new(91.0, 92.0)])
                .with_labels([MyLabel("static calibration".into())]),
        )
        .build()
        .unwrap();
    let (db, expected) = fixture(
        vec![
            points("a", "frame", &[-10, -5, 2, 4]),
            points("a", "frame", &[5, 9, 10]),
            points("b", "frame", &[3, 5, 7]),
            points("unrelated", "clock", &[3, 4, 5]),
            static_chunk,
        ],
        StoreKind::Recording,
    );
    assert_selection(
        &db,
        &expected,
        "frame",
        AbsoluteTimeRangeF::new(3_i64, 5_i64),
        3,
        5,
    );
    assert_selection(
        &db,
        &expected,
        "missing",
        AbsoluteTimeRangeF::new(3_i64, 5_i64),
        3,
        5,
    );
}

#[test]
fn disjoint_interior_gap_and_full_coverage() {
    let (db, expected) = fixture(
        vec![
            points("a", "frame", &[1, 2, 10, 11]),
            points("b", "frame", &[2, 10]),
        ],
        StoreKind::Recording,
    );
    for (min, max) in [(3_i64, 8_i64), (20, 30), (-20, -10), (1, 11), (-100, 100)] {
        assert_selection(
            &db,
            &expected,
            "frame",
            AbsoluteTimeRangeF::new(min, max),
            min,
            max,
        );
    }
}

#[test]
fn fractional_selection_keeps_existing_floor_ceil_conversion() {
    let (db, expected) = fixture(
        vec![points("a", "frame", &[-4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6])],
        StoreKind::Recording,
    );
    assert_selection(
        &db,
        &expected,
        "frame",
        AbsoluteTimeRangeF::new(-1.2, 3.2),
        -2,
        4,
    );
    assert_selection(
        &db,
        &expected,
        "frame",
        AbsoluteTimeRangeF::point(1.5),
        1,
        2,
    );
}

#[test]
fn store_information_and_blueprint_activation_survive_selection() {
    for kind in [StoreKind::Recording, StoreKind::Blueprint] {
        let (mut db, expected) = fixture(vec![points("metadata", "frame", &[0, 3, 7, 9])], kind);
        let info = SetStoreInfo {
            row_id: *RowId::new(),
            info: StoreInfo::new(
                db.store_id().clone(),
                StoreSource::Other("export fixture".into()),
            ),
        };
        db.set_store_info(info.clone());
        let exported = messages(&db, Some(("frame", AbsoluteTimeRangeF::new(3_i64, 7_i64))));
        assert_eq!(exported.first(), Some(&LogMsg::SetStoreInfo(info)));
        let activations: Vec<_> = exported
            .iter()
            .filter_map(|message| {
                if let LogMsg::BlueprintActivationCommand(cmd) = message {
                    Some(cmd)
                } else {
                    None
                }
            })
            .collect();
        assert_eq!(activations.len(), usize::from(kind == StoreKind::Blueprint));
        if let Some(command) = activations.first() {
            assert_eq!(command.blueprint_id, *db.store_id());
            assert!(command.make_active && command.make_default);
        }
        assert_selection(
            &db,
            &expected,
            "frame",
            AbsoluteTimeRangeF::new(3_i64, 7_i64),
            3,
            7,
        );
    }
}

#[test]
fn deterministic_varied_chunks_match_row_predicate() {
    for seed in 0..12_i64 {
        let times: Vec<_> = (0..23_i64)
            .map(|i| ((i * 17 + seed * 11) % 29) - 14)
            .collect();
        let (db, expected) = fixture(
            vec![points("sample", "frame", &times)],
            StoreKind::Recording,
        );
        assert_selection(
            &db,
            &expected,
            "frame",
            AbsoluteTimeRangeF::new(-seed, seed),
            -seed,
            seed,
        );
    }
}
