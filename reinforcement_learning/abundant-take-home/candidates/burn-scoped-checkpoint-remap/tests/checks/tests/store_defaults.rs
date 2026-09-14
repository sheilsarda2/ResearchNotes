use burn_store::{ModuleStore, PytorchStore, SafetensorsStore};

fn fixture(name: &str) -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures").join(name)
}

fn check_default<S: ModuleStore>(mut store: S, contiguous: bool) {
    let indices = if contiguous { [0, 1, 2] } else { [0, 2, 4] };
    let expected: Vec<String> = ["encoder.layers", "flow.flows"].iter()
        .flat_map(|prefix| indices.iter().map(move |index| format!("{prefix}.{index}.weight")))
        .collect();
    assert_eq!(store.keys().unwrap(), expected);
    let middle = format!("encoder.layers.{}.weight", indices[1]);
    let tensor = store.get_tensor(&middle).unwrap().unwrap();
    assert_eq!(&*tensor.to_bytes().unwrap(), &22.0f32.to_le_bytes());
    let absent = if contiguous { "encoder.layers.4.weight" } else { "encoder.layers.1.weight" };
    assert!(store.get_tensor(absent).unwrap().is_none());
}

#[test]
fn pytorch_default_remains_all_indices() {
    check_default(PytorchStore::from_file(fixture("mixed.pt")), true);
}

#[test]
fn safetensors_file_default_remains_no_indices() {
    check_default(SafetensorsStore::from_file(fixture("mixed.safetensors")), false);
}

#[test]
fn safetensors_memory_default_remains_no_indices() {
    check_default(SafetensorsStore::from_bytes(Some(std::fs::read(fixture("mixed.safetensors")).unwrap())), false);
}
