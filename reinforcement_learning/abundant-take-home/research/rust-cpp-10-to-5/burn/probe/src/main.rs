use burn_core as burn;
use burn_core::module::{Module, Param};
use burn_core::tensor::{Device, Tensor};
use burn_store::{ModuleSnapshot, ModuleStore, PytorchStore, SafetensorsStore};
use serde_json::{Value, json};

#[derive(Module, Debug)]
struct Layer { weight: Param<Tensor<1>> }
#[derive(Module, Debug)]
struct Flow { flows: Vec<Option<Layer>> }
#[derive(Module, Debug)]
struct Encoder { layers: Vec<Layer> }
#[derive(Module, Debug)]
struct Model { flow: Flow, encoder: Encoder }

fn layer() -> Layer {
    Layer { weight: Param::from_data([-1.0f32], &Device::flex()) }
}
fn model() -> Model {
    Model { flow: Flow { flows: vec![Some(layer()), None, Some(layer()), None, Some(layer())] },
            encoder: Encoder { layers: vec![layer(), layer(), layer()] } }
}
fn values(m: &Model) -> Vec<f32> {
    m.flow.flows.iter().filter_map(|x| x.as_ref()).chain(m.encoder.layers.iter())
        .map(|x| x.weight.val().into_data().to_vec::<f32>().unwrap()[0]).collect()
}
fn run<S: ModuleStore>(mut store: S) -> Value {
    let mut m = model();
    let before_names: Vec<_> = m.collect(None, None, true).into_iter().map(|x| x.name).collect();
    let result = match m.load_from(&mut store) {
        Ok(r) => json!({"ok": true, "applied": r.applied, "missing": r.missing,
                       "unused": r.unused, "errors": format!("{:?}", r.errors)}),
        Err(e) => json!({"ok": false, "error": e.to_string()}),
    };
    let actual = values(&m);
    json!({"expected_names": before_names, "result": result, "values": actual,
           "values_match": actual == [10.0,12.0,14.0,20.0,22.0,24.0]})
}
fn main() {
    let mut observations = serde_json::Map::new();
    for map in [false, true] {
        observations.insert(format!("pytorch_global_{map}"), run(PytorchStore::from_file("mixed.pt").map_indices_contiguous(map)));
        observations.insert(format!("safetensors_global_{map}"), run(SafetensorsStore::from_file("mixed.safetensors").map_indices_contiguous(map)));
    }
    let pt = PytorchStore::from_file("mixed.pt").map_indices_contiguous(false)
        .with_key_remapping(r"^encoder\.layers\.2\.", "encoder.layers.1.")
        .with_key_remapping(r"^encoder\.layers\.4\.", "encoder.layers.2.");
    let st = SafetensorsStore::from_file("mixed.safetensors").map_indices_contiguous(false)
        .with_key_remapping(r"^encoder\.layers\.2\.", "encoder.layers.1.")
        .with_key_remapping(r"^encoder\.layers\.4\.", "encoder.layers.2.");
    observations.insert("pytorch_manual_control".into(), run(pt));
    observations.insert("safetensors_manual_control".into(), run(st));
    for name in ["pytorch_manual_control", "safetensors_manual_control"] {
        assert_eq!(observations[name]["values_match"], true, "invalid probe: control {name}");
        assert_eq!(observations[name]["result"]["ok"], true);
        assert_eq!(observations[name]["result"]["missing"], json!([]));
        assert_eq!(observations[name]["result"]["unused"], json!([]));
    }
    println!("{}", serde_json::to_string_pretty(&observations).unwrap());
}
