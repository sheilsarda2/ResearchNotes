# Selective layer-index mapping for checkpoint imports

Burn's automatic index mapper currently renumbers every numeric path prefix together. A model imported from PyTorch can need `flow.flows.{0,2,4}` to keep its numbering while `encoder.layers.{0,2,4}` becomes `encoder.layers.{0,1,2}`. Turning the global flag on or off cannot satisfy both parts. Explicit per-index key renames work, but the automatic mapper needs a policy that can vary by prefix.

Implement this capability in the checkout at `/workspace/repo`. Add and export this public API from `burn_store` (under the existing `std` feature):

```rust
IndexMappingPolicy::new(default_map: bool) -> IndexMappingPolicy
policy.with_rule(prefix_pattern: impl AsRef<str>, map: bool)
    -> Result<IndexMappingPolicy, regex::Error>
policy.should_map(prefix: &str) -> bool
```

The policy must be cloneable. Rules use Rust regex syntax, in insertion order, with the last matching rule taking precedence. An unmatched prefix uses `default_map`. Invalid regex syntax returns an error. Prefixes are the path before a numeric segment, without its trailing dot: the prefix for `stages.4.units.9.weight` at index `9` is `stages.4.units`. A leading numeric segment has the empty prefix. Users can anchor patterns when they want an exact prefix.

Add `map_indices_contiguous_with_policy(tensors, &policy)` alongside the existing free function. It takes and returns the same tensor and transformation types as `map_indices_contiguous`. For each selected prefix, map observed numeric indices in ascending numeric order to `0,1,...`; preserve unselected indices. All nested policy matches use the input names before any numeric index is changed. Preserve input tensor order, values, shapes, dtypes and parameter IDs, and return one `(new_name, input_name)` transformation per input tensor, including identities. Empty input and paths without numeric segments remain supported.

Add `.with_index_mapping_policy(policy)` to both `PytorchStore` and `SafetensorsStore`, including Safetensors memory storage. Stores must apply explicit key-remapping patterns first, then the index policy using those resulting names. Model loading, direct tensor access and key enumeration must agree. Preserve the current default behavior (PyTorch maps all prefixes; Safetensors maps none), the old free function, and `.map_indices_contiguous(bool)`. A later policy setter replaces a boolean setting; a later boolean setter replaces scoped rules with that global choice. Configure stores before their first read; reconfiguration of a populated cache is not a required part of this task.

For example, either of these policies should import the mixed model above:

```rust
let policy = IndexMappingPolicy::new(true)
    .with_rule(r"^flow\.flows$", false)?;
let policy = IndexMappingPolicy::new(false)
    .with_rule(r"^encoder\.layers$", true)?;
```

Use the existing store machinery so actual PyTorch and Safetensors checkpoints load the correct parameter values and produce accurate missing/unused reports. Maintain the existing explicit-key-remapping behavior. Choose your internal implementation freely.

The environment contains the pinned upstream checkout, Rust, and cached locked dependencies. `/opt/burn-checks/Cargo.toml` is a small dependency harness pointing at this checkout; you can create your own checks around it without building unrelated GPU/example targets. On ARM64, the environment config enables the FP16 CPU target feature needed by the upstream Flex backend.

Only `/workspace/repo/crates/burn-store/src` is transferred to a fresh verifier. Changes to manifests, dependencies, tests, or other crates are not submitted. The verifier uses independent tests and protected copies of relevant upstream key-remapper regressions with networking disabled. This task concerns index mapping; it does not ask for other checkpoint format or durability changes.
