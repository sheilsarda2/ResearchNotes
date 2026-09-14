use std::sync::Arc;

use re_chunk::{Chunk, RowId};
use re_entity_db::EntityDb;
use re_log_types::example_components::{MyPoint, MyPoints};
use re_log_types::{AbsoluteTimeRangeF, LogMsg, StoreId, StoreKind, TimePoint, Timeline};

#[test]
fn exported_selection_only_contains_selected_rows() -> anyhow::Result<()> {
    let mut db = EntityDb::new(StoreId::random(StoreKind::Recording, "demand_audit"));
    let frame = Timeline::new_sequence("frame");
    let mut builder = Chunk::builder("pose");
    for t in (2..=20).step_by(2) {
        builder = builder.with_archetype(
            RowId::new(),
            TimePoint::from_iter([(frame, t)]),
            &MyPoints::new([MyPoint::new(t as f32, 0.0)]),
        );
    }
    db.add_chunk(&Arc::new(builder.build()?))?;
    let static_chunk = Chunk::builder("static")
        .with_archetype(
            RowId::new(),
            TimePoint::default(),
            &MyPoints::new([MyPoint::new(99.0, 0.0)]),
        )
        .build()?;
    db.add_chunk(&Arc::new(static_chunk))?;

    let inspect = |selection| -> anyhow::Result<(Vec<i64>, usize)> {
        let mut times = Vec::new();
        let mut static_rows = 0;
        for message in db.to_messages(selection) {
            if let LogMsg::ArrowMsg(_, msg) = message? {
                let chunk = Chunk::from_arrow_msg(&msg)?;
                if chunk.is_static() {
                    static_rows += chunk.num_rows();
                } else if let Some(column) = chunk.timelines().get(frame.name()) {
                    times.extend_from_slice(column.times_raw());
                }
            }
        }
        times.sort();
        Ok((times, static_rows))
    };

    let original = inspect(None)?;
    assert_eq!(original, ((2..=20).step_by(2).collect(), 1));
    println!("positive_control_unfiltered={original:?}");
    let disjoint = inspect(Some((*frame.name(), AbsoluteTimeRangeF::new(21_i64, 30_i64))))?;
    assert_eq!(disjoint, (vec![], 1));
    println!("positive_control_disjoint={disjoint:?}");
    let cropped = inspect(Some((*frame.name(), AbsoluteTimeRangeF::new(4_i64, 7_i64))))?;
    println!("selected_4_to_7={cropped:?}");
    assert_eq!(inspect(None)?, original, "export must not mutate the database");
    assert_eq!(cropped, (vec![4, 6], 1), "selected export leaked rows outside [4, 7]");
    Ok(())
}
