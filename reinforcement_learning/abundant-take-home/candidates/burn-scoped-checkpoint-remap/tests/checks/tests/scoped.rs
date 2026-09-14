use burn_core as burn;
use burn_core::module::{Module, Param};
use burn_core::tensor::{Device, Tensor};
use burn_pack::Tensor as PackTensor;
use burn_std::{Bytes, DType};
use burn_store::{IndexMappingPolicy, ModuleSnapshot, ModuleStore, PytorchStore, SafetensorsStore,
    map_indices_contiguous, map_indices_contiguous_with_policy};

fn tensors(names: &[&str]) -> Vec<PackTensor> {
    names.iter().enumerate().map(|(i,n)| PackTensor::new((*n).into(), DType::F32,
        [1], Some(100+i as u64), Bytes::from_bytes_vec((i as f32).to_le_bytes().to_vec()))).collect()
}
fn check_names(input: &[&str], expected: &[&str], policy: &IndexMappingPolicy) {
    let before = tensors(input);
    let (after, changes) = map_indices_contiguous_with_policy(before.clone(), policy);
    assert_eq!(after.iter().map(|x| x.name.as_str()).collect::<Vec<_>>(), expected);
    assert_eq!(after.len(), before.len());
    assert_eq!(changes, expected.iter().zip(input).map(|(n,o)| (n.to_string(),o.to_string())).collect::<Vec<_>>());
    for (a,b) in after.iter().zip(&before) {
        assert_eq!(&*a.to_bytes().unwrap(), &*b.to_bytes().unwrap());
        assert_eq!(a.dtype,b.dtype); assert_eq!(a.shape,b.shape); assert_eq!(a.param_id,b.param_id);
    }
}

#[test]
fn mixed_prefixes_preserve_values_and_input_order() {
    let p = IndexMappingPolicy::new(true).with_rule(r"^flow\.flows$",false).unwrap();
    check_names(&["flow.flows.4.weight","encoder.layers.10.weight","flow.flows.0.weight",
        "encoder.layers.2.bias","encoder.layers.2.weight","flow.flows.2.weight"],
        &["flow.flows.4.weight","encoder.layers.1.weight","flow.flows.0.weight",
        "encoder.layers.0.bias","encoder.layers.0.weight","flow.flows.2.weight"], &p);
}
#[test]
fn nested_rules_use_original_parent_indices() {
    let p = IndexMappingPolicy::new(true).with_rule(r"^stages\.4\.units$",false).unwrap();
    check_names(&["stages.4.units.9.weight","stages.2.units.7.weight","stages.4.units.3.weight",
        "stages.2.units.5.weight"], &["stages.1.units.9.weight","stages.0.units.1.weight",
        "stages.1.units.3.weight","stages.0.units.0.weight"], &p);
}
#[test]
fn last_rule_wins_with_default_and_invalid_regex() {
    assert!(IndexMappingPolicy::new(false).with_rule("[",true).is_err());
    let p = IndexMappingPolicy::new(false).with_rule(r"^encoder",true).unwrap()
        .with_rule(r"^encoder\.frozen$",false).unwrap();
    assert!(p.should_map("encoder.layers")); assert!(!p.should_map("encoder.frozen"));
    assert!(!p.should_map("decoder"));
    check_names(&["encoder.layers.8.w","encoder.frozen.8.w","decoder.8.w"],
        &["encoder.layers.0.w","encoder.frozen.8.w","decoder.8.w"], &p.clone());
}
#[test]
fn all_none_empty_root_and_non_numeric_compatibility() {
    let names = ["8.x","3.x","plain.weight","layer2.7.weight"];
    check_names(&names,&names,&IndexMappingPolicy::new(false));
    check_names(&names,&["1.x","0.x","plain.weight","layer2.0.weight"],&IndexMappingPolicy::new(true));
    check_names(&[],&[],&IndexMappingPolicy::new(true));
    let legacy = map_indices_contiguous(tensors(&names));
    let policy = map_indices_contiguous_with_policy(tensors(&names),&IndexMappingPolicy::new(true));
    assert_eq!(legacy.1,policy.1);
    check_names(&names,&["1.x","0.x","plain.weight","layer2.7.weight"],
        &IndexMappingPolicy::new(false).with_rule("^$",true).unwrap());
}

#[derive(Module, Debug)]
struct Layer { weight: Param<Tensor<1>> }
#[derive(Module, Debug)]
struct Flow { flows: Vec<Option<Layer>> }
#[derive(Module, Debug)]
struct Encoder { layers: Vec<Layer> }
#[derive(Module, Debug)]
struct Model { flow: Flow, encoder: Encoder }
fn layer() -> Layer { Layer { weight: Param::from_data([-1.0f32], &Device::flex()) } }
fn model() -> Model { Model {
    flow: Flow { flows: vec![Some(layer()),None,Some(layer()),None,Some(layer())] },
    encoder: Encoder { layers: vec![layer(),layer(),layer()] }
} }
fn check_load<S: ModuleStore>(mut s: S) {
    let expected_keys = ["encoder.layers.0.weight","encoder.layers.1.weight","encoder.layers.2.weight",
        "flow.flows.0.weight","flow.flows.2.weight","flow.flows.4.weight"];
    assert_eq!(s.keys().unwrap(),expected_keys);
    let tensor=s.get_tensor("encoder.layers.1.weight").unwrap().unwrap();
    assert_eq!(&*tensor.to_bytes().unwrap(),&22.0f32.to_le_bytes());
    assert!(s.get_tensor("encoder.layers.4.weight").unwrap().is_none());
    let mut m = model();
    let r = m.load_from(&mut s).unwrap();
    assert!(r.errors.is_empty()); assert!(r.missing.is_empty()); assert!(r.unused.is_empty());
    assert_eq!(r.applied.len(),6);
    let values = m.flow.flows.iter().filter_map(|x| x.as_ref()).chain(m.encoder.layers.iter())
        .map(|x| x.weight.val().into_data().try_to_vec::<f32>().unwrap()[0]).collect::<Vec<_>>();
    assert_eq!(values,[10.0,12.0,14.0,20.0,22.0,24.0]);
}
fn check_partial<S: ModuleStore>(mut s: S) {
    let mut m=model();let r=m.load_from(&mut s).unwrap();
    assert!(r.errors.is_empty());
    assert_eq!(r.applied.len(),5);
    assert_eq!(r.missing.iter().map(|x|x.0.as_str()).collect::<Vec<_>>(),["flow.flows.4.weight"]);
    assert_eq!(r.unused,["encoder.layers.3.weight"]);
    assert_eq!(m.flow.flows[4].as_ref().unwrap().weight.val().into_data().try_to_vec::<f32>().unwrap(),[-1.0]);
}
#[test]
fn partial_load_reports_scoped_missing_and_unused_names() {
    check_partial(PytorchStore::from_file(fixture("partial.pt")).allow_partial(true)
        .with_index_mapping_policy(preserve_flows()));
    check_partial(SafetensorsStore::from_file(fixture("partial.safetensors")).allow_partial(true)
        .with_index_mapping_policy(preserve_flows()));
}
fn fixture(name: &str) -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures").join(name)
}
fn preserve_flows() -> IndexMappingPolicy {
    IndexMappingPolicy::new(true).with_rule(r"^flow\.flows$",false).unwrap()
}
fn collapse_encoder() -> IndexMappingPolicy {
    IndexMappingPolicy::new(false).with_rule(r"^encoder\.layers$",true).unwrap()
}
#[test]
fn pytorch_real_load_supports_both_default_directions() {
    for p in [preserve_flows(),collapse_encoder()] {
        check_load(PytorchStore::from_file(fixture("mixed.pt")).with_index_mapping_policy(p));
    }
}
#[test]
fn safetensors_file_and_memory_real_load() {
    for p in [preserve_flows(),collapse_encoder()] {
        check_load(SafetensorsStore::from_file(fixture("mixed.safetensors")).with_index_mapping_policy(p.clone()));
        check_load(SafetensorsStore::from_bytes(Some(std::fs::read(fixture("mixed.safetensors")).unwrap()))
            .with_index_mapping_policy(p));
    }
}
#[test]
fn explicit_key_remapping_precedes_index_policy_in_both_stores() {
    // Original checkpoint prefix is renamed to a model prefix before scoped mapping.
    let p = IndexMappingPolicy::new(false).with_rule(r"^encoder\.layers$",true).unwrap();
    check_load(PytorchStore::from_file(fixture("renamed.pt"))
        .with_key_remapping(r"^source_encoder\.","encoder.").with_index_mapping_policy(p.clone()));
    check_load(SafetensorsStore::from_file(fixture("renamed.safetensors"))
        .with_key_remapping(r"^source_encoder\.","encoder.").with_index_mapping_policy(p));
}
#[test]
fn boolean_compatibility_and_last_setter_wins() {
    // A later bool replaces scoped rules; explicit per-index mapping remains usable.
    check_load(PytorchStore::from_file(fixture("mixed.pt"))
        .with_index_mapping_policy(IndexMappingPolicy::new(true)).map_indices_contiguous(false)
        .with_key_remapping(r"^encoder\.layers\.2\.","encoder.layers.1.")
        .with_key_remapping(r"^encoder\.layers\.4\.","encoder.layers.2."));
    check_load(SafetensorsStore::from_file(fixture("mixed.safetensors"))
        .with_index_mapping_policy(IndexMappingPolicy::new(true)).map_indices_contiguous(false)
        .with_key_remapping(r"^encoder\.layers\.2\.","encoder.layers.1.")
        .with_key_remapping(r"^encoder\.layers\.4\.","encoder.layers.2."));
    check_load(PytorchStore::from_file(fixture("mixed.pt")).map_indices_contiguous(false)
        .with_index_mapping_policy(preserve_flows()));
    check_load(SafetensorsStore::from_file(fixture("mixed.safetensors")).map_indices_contiguous(true)
        .with_index_mapping_policy(collapse_encoder()));
}
