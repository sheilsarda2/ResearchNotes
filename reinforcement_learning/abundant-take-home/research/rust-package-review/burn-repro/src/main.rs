use burn_core::tensor::{Device, Tensor};
use burn_core as burn;
use burn_core::module::{Module, Param};

#[derive(Module, Debug)]
struct Model { weight: Param<Tensor<1>> }
use burn_pack::Tensor as PackTensor;
use burn_std::{Bytes, DType};
use burn_store::{map_indices_contiguous, BurnpackStore, ModuleSnapshot, KeyRemapper};
use serde_json::json;

fn tensors(names: &[&str]) -> Vec<PackTensor> {
    names.iter().map(|name| PackTensor::new((*name).into(), DType::F32,
        [1], None, Bytes::from_bytes_vec(1.0f32.to_le_bytes().to_vec()))).collect()
}

fn main() {
    let names = ["flow.flows.0.weight", "flow.flows.2.weight", "flow.flows.4.weight",
                 "encoder.layers.0.weight", "encoder.layers.2.weight", "encoder.layers.4.weight"];
    let expected = ["flow.flows.0.weight", "flow.flows.2.weight", "flow.flows.4.weight",
                    "encoder.layers.0.weight", "encoder.layers.1.weight", "encoder.layers.2.weight"];
    let (all, _) = map_indices_contiguous(tensors(&names));
    let mapped: Vec<_> = all.iter().map(|t| t.name.as_str()).collect();
    let (control, _) = map_indices_contiguous(tensors(&names[3..]));
    let control_names: Vec<_> = control.iter().map(|t| t.name.as_str()).collect();
    assert_eq!(control_names, &expected[3..]);

    let (manual, _) = KeyRemapper::new()
        .add_pattern(r"^encoder\.layers\.2\.", "encoder.layers.1.").unwrap()
        .add_pattern(r"^encoder\.layers\.4\.", "encoder.layers.2.").unwrap()
        .remap(tensors(&names));
    let manual_names: Vec<_> = manual.iter().map(|t| t.name.as_str()).collect();
    assert_eq!(manual_names, expected);

    let device = Device::flex();
    let dir = tempfile::tempdir().unwrap();
    let old = Model { weight: Param::from_tensor(Tensor::<1>::from_floats([1.0], &device)) };
    let new = Model { weight: Param::from_tensor(Tensor::<1>::from_floats([9.0], &device)) };
    let path = dir.path().join("model");
    old.save_into(&mut BurnpackStore::from_file(&path).auto_extension(false)).unwrap();
    let suffixed = path.with_extension("bpk");
    let actual = if path.exists() { path.clone() } else { suffixed.clone() };
    let old_bytes = std::fs::read(&actual).unwrap();
    let refused = new.save_into(&mut BurnpackStore::from_file(&path).auto_extension(false)).is_err();
    let content_changed = old_bytes != std::fs::read(&actual).unwrap();
    let control_path = dir.path().join("control");
    old.save_into(&mut BurnpackStore::from_file(&control_path)).unwrap();
    let control_refused = new.save_into(&mut BurnpackStore::from_file(&control_path)).is_err();
    assert!(control_refused);
    println!("{}", serde_json::to_string_pretty(&json!({
       "upstream_commit": "1414c8a14e5169ef5e5fc67f9b8ab01a25d6352d",
       "scoped_index_remapping": {"input": names, "required_mixed_names": expected,
           "mapping_true_output": mapped, "mapping_true_satisfies_mixed": mapped == expected,
           "mapping_false_satisfies_mixed": names == expected,
           "all_collapsible_positive_control": control_names == expected[3..],
           "existing_explicit_per_index_workaround_satisfies_mixed": manual_names == expected},
       "overwrite_guard": {"auto_extension_false": {"requested_file_exists": path.exists(),
           "unexpected_bpk_exists": suffixed.exists(), "second_save_refused": refused,
           "old_checkpoint_replaced": content_changed},
           "default_auto_extension_positive_control_refuses_second_save": control_refused}
    })).unwrap());
}
