# Independent review: Burn scoped checkpoint remapping

Reviewed September 13, 2026 at 10:38 UTC by the separate Rust/object_store worker. Scope was read-only inspection of the task instruction, reference patch, protected tests and fixtures, source archive, Docker recipes, transfer declaration, and saved direct evidence. No build was run and no candidate files were changed. The coordinator was building the verifier image during review.

**Verdict: no blocking instruction/reference/isolation defect found.** The reference matches the stated behavior, and the package is suitable for the coordinator's remaining isolated baseline/reference and final Harbor gates. One specific backward-compatibility coverage gap was reported promptly and the coordinator addressed it with three separate untouched-default controls; the addendum below records their read-only review.

## Contract and reference

The user demand is tied to [Burn #4716](https://github.com/tracel-ai/burn/issues/4716) and its [maintainer-proposed per-prefix policy](https://github.com/tracel-ai/burn/issues/4716#issuecomment-5576304561). The task correctly distinguishes the missing automatic policy from the existing working explicit-key-remapping workaround. Its bounded API fixes regex semantics, last-match precedence, original-prefix matching for nested indices, setter precedence, and the configured-before-first-read lifecycle. That gives independently testable public behavior without prescribing the algorithm. No checkpoint durability or unrelated reader redesign is required.

The reference changes four files under `crates/burn-store/src`: the shared remapper, public exports, and the PyTorch/Safetensors store implementations. It compiles rules once, checks them in reverse insertion order, and uses the old mapper's original-prefix index collection/application. Only selected prefixes receive index maps; the existing numeric ordering and original-name lookup remain intact. The old free function delegates to the all-selected policy. Moving owned tensors through the unchanged application step only changes names, preserving their order and metadata. Leading numeric segments use the empty prefix as specified.

Both stores apply explicit remapping before the scoped mapping in the shared cache-population path. `keys`, `get_tensor`, and model loading consume that cache, supporting consistent results. The policy takes precedence when present; a boolean setter clears it. File and memory Safetensors construction paths are covered by the new state field, and policy imports/fields are appropriately gated by `std`. Existing defaults remain true for PyTorch and false for Safetensors in the inspected reference. Cache clearing added by setters is incidental behavior beyond the required pre-read lifecycle and is not imposed by hidden tests.

## Independent behavior checks

Nine protected tests exercise real public APIs. Free-function tests verify mixed selected/unselected prefixes, numeric rather than lexicographic ordering (indices 2 and 10), duplicate index occurrences across weight/bias, preserved input order/bytes/dtype/shape/parameter IDs, original-parent nested matching, last matching rule, invalid regex, cloning, all/none policies, root indices, nonnumeric names, empty input, and legacy free-function equivalence.

Loader tests use genuine small `.pt` ZIP/pickle tensor checkpoints and Safetensors files, with distinct scalar values. Inspection confirmed the Safetensors names/shapes/dtypes/offsets and the expected PyTorch tensor reconstruction records. The tests check actual model values, key enumeration, direct tensor lookup, both policy default directions, Safetensors file/memory loading, explicit remapping before policy, boolean/policy setter order, and meaningful missing/unused reports. There is no timing dependency, network service, GPU target, or reference-patch matching.

The twelve protected upstream tests match the archived original `keyremapper.rs` test module exactly except the external imports, the `cfg(test)` gate, and the qualified public bridge path. They preserve original key-remapping and nested global-index controls independently of any submitted changes to embedded unit tests.

Coverage improvement sent to coordinator: add bare-store default checks on `mixed.pt` and `mixed.safetensors`. The current nine tests explicitly configure every store, so an implementation that accidentally changes the constructor defaults could pass. Checking keys and one representative value for unconfigured PyTorch, Safetensors file, and Safetensors memory would cover this stated contract without new fixtures or implementation constraints. Additional dtype/shape variants would be optional breadth; source inspection shows the reference leaves all fields besides names untouched.

## Package, artifact transfer, and caches

The agent and verifier build from matching clean archives, pinned Rust 1.98.1 image digest, identical harness manifests/locks, and the same ARM-only Cargo target configuration. Source archive SHA-256 is `acc08ecc6e83756f77ef4a3a8fd899157f89dba3fcbc40510efc3188b82ed08f`; it contains both upstream licenses and no hidden scoped tests, solution patch, or solution script. The verifier lock also matches the image-build smoke harness lock, so its dependency graph is prefetched by the baseline build. Runtime Cargo is offline.

Only `/workspace/repo/crates/burn-store/src` is declared as a submitted artifact. The separate verifier independently holds baseline source, manifests, dependency caches, protected tests, fixtures, and runner. The recipe copies hidden tests only in the verifier image. As documented, this source boundary does not claim complete isolation from deliberately hostile Rust code with filesystem/process access.

`test.sh` initializes reward 0, unsets compiler wrappers, forces `cargo clean -p burn-store`, and requires a successful clean and both test commands. Cleaning the submitted package prevents older transferred timestamps from reusing its precompiled baseline library. Tests compile against the workspace path dependency, so there is no alternate installed implementation. Protected `#[test]` counts and successful summaries must total all 21 tests, with zero failed/ignored tests; a missing API, compile failure, test failure, or missing collection cannot produce reward 1 through the ordinary runner path. The coordinator's final Harbor run still needs to demonstrate the declared source transfer end to end.

## Evidence and remaining gates

`construction/reference-final.log` records all nine new tests and twelve upstream controls passing directly. Earlier unchanged-upstream integration evidence in `research/rust-cpp-10-to-5/burn/store-probe-final.json` shows both global boolean choices fail the mixed model while explicit remapping loads all expected values for both checkpoint formats. Thus the demand is supported by a current behavioral gap and positive controls, not solely by the absence of the new public API. The untouched baseline will necessarily fail to compile the new-API suite; that is expected for this feature task.

At this review, isolated Docker baseline/reference diagnostics and final Harbor records were still pending and provenance accurately reported validation in progress. Do not treat this static review or the direct reference log as those results. Medium difficulty is a reasonable label, but neither required agent horizon nor model headroom has been measured.

Review snapshot at 2026-09-13T10:38:24Z: reference patch SHA-256 `a8206b26445978ab49fa1813d2bf1c019a108bffea3b7d78ff64763fbadbb86b`; nine-test scoped file SHA-256 `5c0a134060f318057018e4d100515eb1b92bffd57e24d2fed25c34f1337dda3f`; verifier shell SHA-256 `2772a5d31f52e35aadead0d896e5eacdb2387f194c7ded3a03b2c8178d6fea66`. Later coordinator fixes/validation are outside this snapshot and should be linked in an addendum if requested.

## Addendum: default controls reviewed

At 2026-09-13T10:39:17.991067+00:00, the coordinator added a separate `store_defaults` target with three public-API tests: bare PyTorch maps both prefixes, bare Safetensors file preserves both prefixes, and bare Safetensors memory preserves both prefixes. Each compares exact keys, a representative tensor value, and an absent opposite-policy key. The runner executes this target separately before the new-API scoped target, so untouched baseline can still run these positive controls. It includes all three exit statuses plus clean status, and expected collection is now 24 tests (9 scoped + 3 defaults + 12 upstream). This resolves the concrete review finding. These additions were inspected read-only; execution and final image refresh remain coordinator-owned.

Default-controls SHA-256: `04706d6d34d82f220b729261b9bc1c1fadc495626aba2f841f374427d0c54c2a`. Updated verifier SHA-256: `0043d4a0409300c03448f737b4f9411e43eb274c24f05a27e2b2f8a7126fc2ae`.
