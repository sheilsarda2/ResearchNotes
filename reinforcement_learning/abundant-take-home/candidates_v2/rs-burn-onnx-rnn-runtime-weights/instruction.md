# burn-onnx: make the RNN family (GRU, LSTM, RNN) consume weights supplied as runtime graph inputs, and fix `layout=1`

You are working in `/workspace/repo`, a checkout of `tracel-ai/burn-onnx` at commit
`6fc6bacec1c5264d1160efd4e2c904591082e2f8` (2026-08-21; burn pinned to git rev `9cdb20df`).
burn-onnx converts ONNX models into Rust source for the Burn framework: `crates/onnx-ir` parses
the protobuf into an IR and runs type inference (`NodeProcessor`), `crates/burn-onnx` turns IR
nodes into Rust code (`NodeCodegen`) plus a `.bpk` weight file, `crates/onnx-tests` holds
end-to-end integration tests over committed `.onnx` fixtures, and `crates/onnx-official-tests`
runs the vendored upstream ONNX backend node corpus (`vendor/node/test_*`, onnx v1.19.0) against
the generated code, gated by `expectations.toml`. Read `AGENTS.md` and `DEVELOPMENT-GUIDE.md`
first; the operator specs are in `onnx-spec/ops/GRU.md`, `LSTM.md`, `RNN.md`.

## The defect

ONNX `GRU`, `LSTM` and `RNN` take their weights as *inputs*: `W` (input weights), `R`
(recurrence weights) and optional `B` (biases). A model may supply them as initializers
(known at import time) or as ordinary graph inputs (known only at run time). Every RNN-family
test in the upstream node corpus does the latter, and so do exported models whose weights are
fed in at inference time.

Today the importer accepts such a model and silently discards the weights. The generated
`forward` takes `W`, `R`, `B` as parameters and never reads them; the generated module still
declares the recurrent layer as a struct field whose gate parameters nothing initializes.
`Model::from_file(...)` then panics on load with "Missing tensors" for every gate weight, and
`Model::new(&device)` runs inference on randomly initialized weights. Separately, the
`layout=1` (batch-first) variants place the `num_directions` axis in the wrong position on
several tensors, which was unobservable while the weights were being dropped.

Fix both, in `crates/onnx-ir/src` and `crates/burn-onnx/src`.

## Required behavior

### 1. Runtime weights are consumed

For every `GRU`, `LSTM` and `RNN` node whose `W`, `R` (and `B`, when present) are graph inputs
rather than initializers, the generated model must compute the operator from the tensors
passed to `forward` at run time, for all of the following, in any combination:

- `direction` = `forward`, `reverse`, `bidirectional` (`num_directions` 1 or 2);
- `B` present or absent (absent means zero bias, per spec);
- `initial_h` (and `initial_c` for LSTM) present or absent, themselves runtime inputs;
- `layout` = 0 or 1;
- GRU `linear_before_reset` = 0 or 1;
- any subset of the declared outputs (`Y`, `Y_h`, `Y_c`), including only `Y_h`, or only `Y`,
  or `Y_h` + `Y_c` without `Y` (an omitted ONNX output has the empty name).

Weight layout follows the ONNX spec exactly. `W` has shape
`[num_directions, gates*hidden_size, input_size]`, `R` is `[num_directions, gates*hidden_size,
hidden_size]`, `B` is `[num_directions, 2*gates*hidden_size]` laid out as
`[Wb(gate 0), ..., Wb(gate n-1), Rb(gate 0), ..., Rb(gate n-1)]`. Gate order along axis 1 is
`z, r, h` for GRU (3 gates), `i, o, f, c` for LSTM (4 gates), a single gate for RNN. For
bidirectional nodes index 0 along axis 0 is the forward direction and index 1 the reverse
direction. The generated `forward` keeps its existing parameter convention: one parameter
per graph input (initializers excluded) in `graph.input` order, returning the declared
outputs in order.

The generated struct must not declare parameters for such a node, so that
`Model::from_file(bpk_path, &device)` loads without missing tensors and `Model::new(&device)`
produces the same outputs. The existing behavior for initializer weights (build-time weights
baked into the `.bpk` file) must be preserved, and the two paths must agree numerically on
the same weights.

### 2. `layout=1` semantics (all three ops, both weight paths)

With `layout=0`: `X` is `[seq_length, batch_size, input_size]`, `Y` is
`[seq_length, num_directions, batch_size, hidden_size]`, and `initial_h`, `initial_c`, `Y_h`,
`Y_c` are `[num_directions, batch_size, hidden_size]`.

With `layout=1`: `X` is `[batch_size, seq_length, input_size]`, `Y` is
`[batch_size, seq_length, num_directions, hidden_size]`, and `initial_h`, `initial_c`, `Y_h`,
`Y_c` are `[batch_size, num_directions, hidden_size]`. Every one of these tensors moves the
direction axis, on the way in and on the way out, for unidirectional and bidirectional nodes
alike. (`layout` exists since opset 14; the corpus models use opset 22 and the fixtures opset
14.)

### 3. Direction semantics

`reverse` processes the sequence from the last time step to the first; `Y[t]` is the hidden
state after consuming `X[t]` in that order, so `Y` for `reverse` equals `forward` run on the
time-reversed sequence with its `Y` reversed again, and `Y_h` is the state after consuming
`X[0]`. `bidirectional` stacks an independent forward pass (direction index 0) and reverse
pass (direction index 1). Note that `onnx.reference.ReferenceEvaluator` (available in your
image) silently ignores `direction="reverse"` and raises for `bidirectional`; the verifier's
reference is onnxruntime, which implements the spec.

### 4. Import-time validation

- `W`, `R` and `B` of one node must be uniformly build-time (all initializers) or uniformly
  run-time (all graph inputs). A model that mixes them (for example `W` as an initializer with
  `R` as a graph input) must be rejected during import with a `ProcessError`-style error
  message; the `onnx2burn` CLI must exit non-zero on it. Constant lifting for these inputs
  must therefore be all-or-nothing: with a constant `W` next to a runtime `R`, `W` must stay a
  named input rather than being lifted to a static value. The absence of the optional `B` is
  not a mixture.
- `sequence_lens` (input 4) on all three ops and the LSTM peephole input `P` remain
  unsupported and must keep being rejected with an error (non-zero `onnx2burn` exit).
- GRU `clip` and non-default GRU activations remain rejected as today; those paths are out of
  scope and unchanged.

### 5. Upstream corpus

Promote exactly these eleven rows of `crates/onnx-official-tests/expectations.toml` from
`fail-compare` to `pass`, and make them pass: `test_gru_batchwise`, `test_gru_defaults`,
`test_gru_seq_length`, `test_gru_with_initial_bias`, `test_lstm_batchwise`,
`test_lstm_defaults`, `test_lstm_with_initial_bias`, `test_rnn_seq_length`,
`test_simple_rnn_batchwise`, `test_simple_rnn_defaults`, `test_simple_rnn_with_initial_bias`.
The outcome of every other corpus row must be unchanged: the verifier uses its own copy of
`expectations.toml` with exactly these promotions, and its drift check
(`verify_fail_compare_still_fails`) requires every remaining `fail-compare` row to still fail.
`test_lstm_with_peepholes` stays `skip-codegen`.

### 6. Numerics and regressions

Outputs are compared element-wise with Burn's `Tolerance::default()` (relative 0.5 %,
absolute 1e-5) against onnxruntime and against the upstream expected outputs; the hidden
integration tests also compare tensor sums with `float_cmp` at `(1e-5, 2 ulps)`, as the
existing `crates/onnx-tests/tests/{gru,lstm,rnn}/mod.rs` tests do. All existing tests in
`crates/onnx-tests`, `crates/onnx-ir/tests` and `crates/onnx-official-tests` must keep
passing. Generated code must compile on the default `flex` (CPU) backend; keep the snapshot
tests inside `crates/burn-onnx/src` and the unit tests inside `crates/onnx-ir/src` green and
add tests as the repository's conventions ask, though the verifier does not run those
in-source tests.

## What is verified

The verifier runs in a separate offline container with a pristine copy of this repository
plus hidden material. Only `crates/onnx-ir/src` and `crates/burn-onnx/src` are collected from
your container (nothing else you edit is transferred, including `Cargo.toml`, `Cargo.lock`,
`build.rs` files, tests, fixtures and `expectations.toml`, so no new dependencies are
possible). It restores every other file from its pristine tree, overlays your two source
directories, and runs, offline:

1. `crates/onnx-tests` including four hidden tests from the upstream change: single-direction
   GRU, LSTM and RNN models whose `W`/`R`/`B` are graph inputs, constructed through
   `Model::from_file` and compared to reference outputs; and a bidirectional GRU with
   `linear_before_reset=1` whose runtime-weight and initializer-weight variants must agree
   element-wise. Exact count: 601 passing, none failing or ignored.
2. `crates/onnx-official-tests` with the eleven promoted rows, the drift checks, and 24
   additional RNN-family cases in the same upstream node-test format whose expected outputs
   were produced by onnxruntime 1.30 (CPU): runtime weights and initializer weights, all three
   ops, all three directions, with and without `B`, with and without initial states, `layout`
   0 and 1, `linear_before_reset` 0 and 1, and each output subset. For `layout=1` cases the
   onnxruntime reference was evaluated on the `layout=0` form of the same model and permuted
   per the shapes in section 2 (onnxruntime has no batch-first kernel); that permutation was
   checked against the upstream `*_batchwise` expected outputs. Exact count: 858 passing.
3. `crates/onnx-ir/tests` integration suites (`basic`, `custom_ops`, `edge_cases`,
   `external_data`, `infrastructure`, `noop_elimination`, `opset_compliance`,
   `simplification`): exact count 553 passing.
4. `onnx2burn` must exit non-zero on a GRU whose `W` is an initializer while `R` and `B` are
   graph inputs, on an LSTM with `sequence_lens`, and on the corpus `test_lstm_with_peepholes`
   model, and must exit zero on a GRU with runtime weights.
5. Anti-cheat: the submitted sources must contain no symlinks and no references to the
   verifier's paths, to onnxruntime or the `ort` crate, or to the corpus data files
   (`test_data_set`, `vendor/node`, `output_N.pb`, `expectations.toml`).

Reward is 1 only if every group passes; per-group results are written to
`/logs/verifier/score.json`.

## Environment notes

- The network is off. Build and test with `cargo ... --offline` (`CARGO_NET_OFFLINE=true` is
  set); all dependencies are pre-fetched and the test targets are pre-built, so incremental
  builds are fast. `cargo insta` is installed for inline snapshot review.
- `python3` (on `PATH`, from `/opt/py`) has `onnx==1.19.0` and `numpy` only: no torch, no
  onnxruntime, no `uv`. Run the repository's fixture generators as plain scripts
  (`python3 crates/onnx-tests/tests/gru/gru_reverse.py` style; those that import torch cannot
  run here) and build new test models with `onnx.helper`. Remember the `ReferenceEvaluator`
  caveats in section 3 when you use it as ground truth.
- onnxruntime is not available to you as a Python package. The `ort` crate appears in
  `crates/burn-onnx/Cargo.toml` only as a dev-dependency of the feature-gated export tests; the
  import path and the generated code must not depend on it.
- `cargo run -p burn-onnx --bin onnx2burn -- model.onnx out_dir` generates code for one model;
  `cargo test -p onnx-official-tests -- test_gru_batchwise` runs a single corpus row after
  you promote it.
