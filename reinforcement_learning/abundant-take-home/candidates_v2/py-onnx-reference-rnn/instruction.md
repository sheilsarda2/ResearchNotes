# Implement the recurrent operator family (RNN, GRU, LSTM) in the ONNX reference evaluator

## Context

`/workspace/repo` is a checkout of `onnx/onnx` (version 1.24.0 development line, default-domain opset 29), installed in editable mode: edits to Python files take effect immediately and no rebuild is needed for Python-only changes. `git status` is clean at the start.

`onnx.reference.ReferenceEvaluator` executes an ONNX model node by node with pure-numpy operator implementations. Each operator of the default domain is a class deriving from `onnx.reference.op_run.OpRun`, defined in a module under `onnx/reference/ops/` and registered by being imported in `onnx/reference/ops/_op_list.py` (and listed in its `__all__`); the evaluator resolves implementations through `onnx.reference.ops.load_op(domain, op_type, version)`. Read `onnx/reference/op_run.py`, `onnx/reference/ops/_op_list.py` and a few existing operators (for example `op_conv.py`, `op_scan.py`, `op_reverse_sequence.py`) for the conventions.

Three operators of the default domain currently have no implementation: **`RNN`**, **`GRU`** and **`LSTM`**. Evaluating any model containing one of them fails with `RuntimeImplementationError: No registered implementation for operator 'LSTM' ...`. Your job is to implement and register them so that the reference evaluator computes them exactly as the ONNX operator specification defines.

## Deliverable and boundaries

- Only the directory `onnx/reference/` is collected and evaluated. Put the implementations and their registration there. Anything you change elsewhere in the repository is discarded.
- Pure Python with **numpy** only (plus the Python standard library and the `onnx` package itself). Do not add third-party dependencies. **`onnxruntime` is not installed in this environment, and the implementation must not import, shell out to, or otherwise depend on any external runtime**; the verifier runs the submission with such imports blocked.
- Do not touch the compiled parts of the package; the operator schemas already exist (`onnx.defs.get_schema("LSTM", 22)` etc.) and `onnx/reference` must keep working for every other operator: the complete upstream reference-evaluator test suites (`tests/python/reference_evaluator_test.py` and `tests/python/backend_reference_test.py`) are re-run from pristine copies.
- The ONNX backend node tests for these three operators (`onnx/backend/test/case/node/{rnn,lstm,gru}.py`) are not present in this checkout. The verifier restores the upstream versions and runs them through your implementation, comparing against the official expected outputs. Derive expected values from the specification below, not from memory of any particular implementation.

## Public interface

1. `from onnx.reference.ops import load_op` then `load_op("", "RNN")`, `load_op("", "GRU")`, `load_op("", "LSTM")` must each return a class deriving from `OpRun`. `load_op("", op_type, version)` must resolve for every default-domain opset version in which the operator exists (RNN, GRU and LSTM exist since opset 1; their current schema is `since_version` 22; the verifier uses models importing opsets **7, 14 and 22**). Follow the `_op_list.py` naming convention: a class named exactly like the operator is valid for every opset; a class named `<Op>_<since_version>` is used for opset versions `>= since_version` until the next such class.
2. `onnx.reference.ReferenceEvaluator(model).run(None, feeds)` must produce the outputs for single-node and multi-node models using these operators.
3. Class contract (this is how the evaluator drives every `OpRun`):
   - `OpRun.__init__(self, onnx_node, run_params)` loads every node attribute onto `self` (for example `self.direction`, `self.hidden_size`, `self.layout`, `self.activations`, `self.activation_alpha`, `self.activation_beta`, `self.clip`, `self.linear_before_reset`, `self.input_forget`), filling in the schema default when the node does not set the attribute and `None` for optional attributes without a default.
   - The evaluator calls `self._run(*inputs, **overridden_attributes)`. Inputs arrive positionally in schema order (`X, W, R, B, sequence_lens, initial_h` and, for LSTM, `initial_c, P`); an optional input that the node omits, either by leaving it off the end of the input list or by giving it the empty name `""`, arrives as `None`. Attribute names may be passed as keyword arguments (this happens when the node is inside a function body with attribute references), so `_run` must accept every attribute of the schema as an optional keyword.
   - `_run` returns a **tuple** with exactly `len(node.output)` numpy arrays in schema output order (`Y`, `Y_h`, and for LSTM `Y_c`). An output the node names `""` still occupies its position (the evaluator discards it). A node may request only `Y`, only `Y_h` (as `["", "Y_h"]`), or any prefix/subset expressed this way.

## Operator specification (ONNX default domain, opset 22 schema; semantics unchanged since opset 14, which introduced `layout`; opset 7 to 13 schemas have no `layout` attribute, i.e. layout 0)

Notation: `seq_length` = number of time steps, `batch_size`, `input_size`, `hidden_size`; `num_directions` = 2 if `direction == "bidirectional"` else 1. `*` is matrix product, `(.)` element-wise product, `^T` transpose.

### Inputs (all tensors of type `T`, except `sequence_lens`)

| index | name | RNN shape | GRU shape | LSTM shape | optional | default when absent |
|---|---|---|---|---|---|---|
| 0 | `X` | `[seq_length, batch_size, input_size]` (layout 0) | same | same | no | |
| 1 | `W` | `[num_directions, hidden_size, input_size]` | `[num_directions, 3*hidden_size, input_size]` (gates `z, r, h` stacked along axis 1) | `[num_directions, 4*hidden_size, input_size]` (gates `i, o, f, c`) | no | |
| 2 | `R` | `[num_directions, hidden_size, hidden_size]` | `[num_directions, 3*hidden_size, hidden_size]` | `[num_directions, 4*hidden_size, hidden_size]` | no | |
| 3 | `B` | `[num_directions, 2*hidden_size]` = `[Wb, Rb]` | `[num_directions, 6*hidden_size]` = `[Wb_z, Wb_r, Wb_h, Rb_z, Rb_r, Rb_h]` | `[num_directions, 8*hidden_size]` = `[Wb_i, Wb_o, Wb_f, Wb_c, Rb_i, Rb_o, Rb_f, Rb_c]` | yes | zeros |
| 4 | `sequence_lens` | `[batch_size]`, int32 | same | same | yes | every sequence has length `seq_length` |
| 5 | `initial_h` | `[num_directions, batch_size, hidden_size]` (layout 0) | same | same | yes | zeros |
| 6 | `initial_c` | | | `[num_directions, batch_size, hidden_size]` (layout 0) | yes | zeros |
| 7 | `P` | | | `[num_directions, 3*hidden_size]` = `[P_i, P_o, P_f]` peephole weights | yes | zeros |

Direction index 0 of `W`, `R`, `B`, `P`, `initial_h`, `initial_c` holds the forward parameters; index 1 (bidirectional only) holds the backward parameters.

### Attributes

| name | type | default | applies to |
|---|---|---|---|
| `direction` | string | `"forward"` | all; one of `forward`, `reverse`, `bidirectional` |
| `hidden_size` | int | none (derive from `R.shape[-1]` when unset) | all |
| `layout` | int | 0 | all (opset >= 14) |
| `activations` | strings | RNN: `["Tanh", "Tanh"]`; GRU: `f=Sigmoid, g=Tanh`; LSTM: `f=Sigmoid, g=Tanh, h=Tanh` | all; RNN takes 1 (or 2 if bidirectional) names, GRU 2 (or 4), LSTM 3 (or 6), listed per direction |
| `activation_alpha`, `activation_beta` | floats | none | all; consumed in the order of `activations` |
| `clip` | float | none | all |
| `linear_before_reset` | int | 0 | GRU |
| `input_forget` | int | 0 | LSTM |

### Outputs (type `T`, all optional)

| index | name | shape (layout 0) | shape (layout 1) |
|---|---|---|---|
| 0 | `Y` | `[seq_length, num_directions, batch_size, hidden_size]` | `[batch_size, seq_length, num_directions, hidden_size]` |
| 1 | `Y_h` | `[num_directions, batch_size, hidden_size]` | `[batch_size, num_directions, hidden_size]` |
| 2 | `Y_c` (LSTM) | `[num_directions, batch_size, hidden_size]` | `[batch_size, num_directions, hidden_size]` |

### Equations, one direction, time step `t` (`H_{t-1}` is `initial_h` for the first step; `C_{t-1}` is `initial_c`)

RNN (default `f = Tanh`):

- `H_t = f(X_t * W^T + H_{t-1} * R^T + Wb + Rb)`

GRU (default `f = Sigmoid`, `g = Tanh`):

- `z_t = f(X_t * W_z^T + H_{t-1} * R_z^T + Wb_z + Rb_z)`
- `r_t = f(X_t * W_r^T + H_{t-1} * R_r^T + Wb_r + Rb_r)`
- `h_t = g(X_t * W_h^T + (r_t (.) H_{t-1}) * R_h^T + Rb_h + Wb_h)` when `linear_before_reset == 0`
- `h_t = g(X_t * W_h^T + (r_t (.) (H_{t-1} * R_h^T + Rb_h)) + Wb_h)` when `linear_before_reset != 0`
- `H_t = (1 - z_t) (.) h_t + z_t (.) H_{t-1}`

LSTM (default `f = Sigmoid`, `g = Tanh`, `h = Tanh`):

- `i_t = f(X_t * W_i^T + H_{t-1} * R_i^T + P_i (.) C_{t-1} + Wb_i + Rb_i)`
- `f_t = f(X_t * W_f^T + H_{t-1} * R_f^T + P_f (.) C_{t-1} + Wb_f + Rb_f)`
- `c_t = g(X_t * W_c^T + H_{t-1} * R_c^T + Wb_c + Rb_c)`
- `C_t = f_t (.) C_{t-1} + i_t (.) c_t`
- `o_t = f(X_t * W_o^T + H_{t-1} * R_o^T + P_o (.) C_t + Wb_o + Rb_o)`
- `H_t = o_t (.) h(C_t)`

Activation functions named by the schema: `Relu(x) = max(0, x)`, `Tanh`, `Sigmoid(x) = 1/(1+e^-x)`, and optionally `Affine(x) = alpha*x + beta`, `LeakyRelu`, `ThresholdedRelu`, `ScaledTanh(x) = alpha*Tanh(beta*x)`, `HardSigmoid(x) = min(max(alpha*x + beta, 0), 1)`, `Elu`, `Softsign(x) = x/(1+|x|)`, `Softplus(x) = log(1+e^x)`. The `activation_alpha`/`activation_beta` entries pair with the `activations` entries in order.

### Direction and time ordering

- `forward`: consume `X[0], X[1], ..., X[seq_length-1]`. `Y[t]` is the hidden state after consuming `X[t]`; `Y_h` (and `Y_c`) is the state after the last step.
- `reverse`: consume `X[seq_length-1], ..., X[0]` with the same parameters (direction index 0). **`Y` stays aligned with the time axis of `X`**: `Y[t]` is the state produced when `X[t]` was consumed, so the entry with the most accumulated history is `Y[0]`. `Y_h`/`Y_c` are the state after the final consumed step, i.e. after `X[0]`.
- `bidirectional`: `num_directions == 2`. Direction index 0 is a forward pass with `W[0], R[0], B[0], P[0], initial_h[0], initial_c[0]`; direction index 1 is a reverse pass with index-1 parameters. `Y[:, 0]` / `Y_h[0]` / `Y_c[0]` hold the forward results, `Y[:, 1]` / `Y_h[1]` / `Y_c[1]` the reverse results.
- The number of directions carried by `W` (its first axis) must agree with the `direction` attribute.

### Layout

`layout == 0` is the shape convention in the tables above. `layout == 1` moves `batch_size` to the front for `X`, `initial_h`, `initial_c`, `Y`, `Y_h`, `Y_c` exactly as listed in the output table: `X` is `[batch_size, seq_length, input_size]`, `initial_h`/`initial_c` are `[batch_size, num_directions, hidden_size]`, `Y` is `[batch_size, seq_length, num_directions, hidden_size]` and `Y_h`/`Y_c` are `[batch_size, num_directions, hidden_size]`. `W`, `R`, `B`, `P`, `sequence_lens` are unaffected. Numerically, layout 1 must equal layout 0 computed on the transposed tensors.

### Types

`T` is any floating-point tensor type of the schema (`float16`, `float32`, `float64`, `bfloat16`). Outputs must have the **same dtype as `X`** (for example float64 inputs yield float64 outputs, float16 inputs yield float16 outputs; compute in a wider type if you wish, but return the input dtype).

### Scope of attributes and inputs that the verifier exercises

- `direction`: all three values. `layout`: 0 and 1. Opsets 7, 14, 22.
- Every optional input present or absent in any combination, including omission by the empty name `""` in the middle of the input list. `sequence_lens`, when supplied, always equals `seq_length` for every batch entry; sequences shorter than `seq_length` are **out of scope** (never supplied) but the input must be accepted.
- All output subsets: `Y` alone, `Y_h` alone (`["", "Y_h"]`), `Y` and `Y_h`, and for LSTM `Y_c` in any combination such as `["", "", "Y_c"]`.
- GRU: `linear_before_reset` 0 and 1. LSTM: peepholes `P` present and absent, `initial_c` present and absent.
- `activations`: RNN with `Tanh` (default) and with `Affine` (`activation_alpha`/`activation_beta` given per direction); GRU and LSTM with the attribute unset or set explicitly to the default names. Other activation names, `clip` and `input_forget` are **not exercised** by the verifier but the attributes must be accepted without error when they carry their defaults.
- dtypes: float32 (exact comparison against official expected outputs and against an independent implementation at rtol 1e-4 / atol 1e-5), float16 (rtol 2e-2 / atol 2e-3) and float64 (must return float64; compared against a float32 reference at rtol 1e-4 / atol 1e-5).

### Error behavior

Constructing or running the operator must raise `RuntimeError` or `ValueError` (either is accepted) when `direction` is not one of `forward`, `reverse`, `bidirectional`, or when the first axis of `W` disagrees with the number of directions implied by `direction` (for example `direction="bidirectional"` with `W.shape[0] == 1`).

## What the verifier does

All of the following must pass for a reward of 1; per-group results are logged for attribution.

1. The 18 official ONNX backend node tests for RNN, GRU and LSTM (`test_simple_rnn_*`, `test_rnn_seq_length`, `test_gru_*`, `test_lstm_*`, 6 per operator, covering defaults, initial bias, longer sequences, peepholes, `layout=1`, `reverse` and `bidirectional`), run through `ReferenceEvaluator` and compared with the official expected outputs at the upstream tolerances.
2. The same 18 models and 200 generated models (the combinations listed under *Scope* above) compared against an independent reference implementation that is only available inside the verifier.
3. The 6 error cases above (2 per operator). An exception signalling a missing implementation (`NotImplementedError` and its subclasses) does not count.
4. The unmodified upstream `tests/python/reference_evaluator_test.py` (including the upstream LSTM regression test `test_lstm_y_c`, 12 parametrizations covering `Y_c`, both layouts, all directions, float32 and float64) and `tests/python/backend_reference_test.py` (cpu), with exact pass counts, so nothing else in `onnx/reference` may regress.
5. Static checks on the submitted `onnx/reference/`: no compiled extensions, `.pth`, `conftest.py`, `sitecustomize.py`, pickle/npy/npz/onnx/pb/json data files, and no occurrence in `.py` files of any of these tokens: `import onnxruntime` / `from onnxruntime`, `importlib`, `import_module(`, `__import__`, `_pytest`, `atexit`, `/logs`, `subprocess`, `os.system`, `os.popen`, `os.exec`, `ctypes`, `sys.meta_path`, `sys.modules`, `sys.path`, `os.environ`, `inspect.`, `onnx.backend.test`, `backend.test.case`, `socket`, `urllib`, `pickle`, `marshal`, `exec(`. None of these occur in the existing `onnx/reference` tree, and none is needed for a numpy implementation.
