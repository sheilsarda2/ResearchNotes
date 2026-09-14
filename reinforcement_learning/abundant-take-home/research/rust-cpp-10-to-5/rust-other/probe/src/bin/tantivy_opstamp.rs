use tantivy::{Index, IndexWriter, doc, schema::{Schema, TEXT}, collector::Count, query::AllQuery};
use serde_json::json;
fn main() -> tantivy::Result<()> {
    let mut schema=Schema::builder(); let text=schema.add_text_field("text",TEXT);
    let index=Index::create_in_ram(schema.build());
    let mut writer:IndexWriter=index.writer_with_num_threads(1,15_000_000)?;
    let initial=writer.commit_opstamp();
    let first=writer.add_document(doc!(text=>"first"))?;
    let committed=writer.commit()?;
    let reported=writer.commit_opstamp();
    let before_rollback=writer.add_document(doc!(text=>"discard"))?;
    let rolled_back=writer.rollback()?;
    let after_rollback=writer.add_document(doc!(text=>"third"))?;
    let count=index.reader()?.searcher().search(&AllQuery,&Count)?;
    println!("{}",json!({"initial":initial,"first_document":first,"actual_commit":committed,"reported_commit":reported,"discarded_document":before_rollback,"rollback_return":rolled_back,"next_document_after_rollback":after_rollback,"committed_document_count":count,"rollback_loads_actual_commit_control":rolled_back==committed,"commit_accessor_correct":reported==committed}));
    assert_eq!(count,1,"rollback must preserve committed data");
    assert_eq!(rolled_back,committed,"rollback restores disk commit control");
    assert!(after_rollback>=committed,"rollback should not reuse pre-commit stamps");
    assert_eq!(reported,committed,"commit_opstamp must report last successful commit");
    Ok(())
}
