use arrow_schema::{Field, ffi::FFI_ArrowSchema};
use serde_json::json;
fn tagged() -> FFI_ArrowSchema {
    // This crate constructed the schema, satisfying with_metadata's documented precondition.
    unsafe { FFI_ArrowSchema::try_new("z", vec![], None).unwrap().with_metadata([
        ("ARROW:extension:name", "geoarrow.wkb"),
        ("ARROW:extension:metadata", "{}"),
    ]).unwrap() }
}
fn main() {
    let scalar = tagged();
    let scalar_back = FFI_ArrowSchema::try_from(&Field::try_from(&scalar).unwrap()).unwrap();
    assert_eq!(scalar.metadata().unwrap(), scalar_back.metadata().unwrap());
    let original = FFI_ArrowSchema::try_new("i", vec![], Some(tagged())).unwrap();
    let field = Field::try_from(&original).unwrap();
    let restored = FFI_ArrowSchema::try_from(&field).unwrap();
    let input = original.dictionary().unwrap().metadata().unwrap();
    let output = restored.dictionary().unwrap().metadata().unwrap();
    println!("{}", json!({"scalar_metadata_control":true,"dictionary_original":input,"dictionary_restored":output,"roundtrip_equal":input==output,"rust_type":format!("{:?}",field.data_type())}));
    assert_eq!(input, output, "dictionary value field metadata must survive C Data schema import/export");
}
