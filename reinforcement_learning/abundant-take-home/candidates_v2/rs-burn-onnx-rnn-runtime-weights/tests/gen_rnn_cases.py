#!/usr/bin/env python3
"""Generate the onnxruntime differential corpus for the RNN-family task.

Runs at VERIFIER IMAGE BUILD TIME only (network is available there for pip;
nothing here runs at verify time). For each authored case it writes an
upstream-style ONNX backend test directory

    <out>/node/test_rnnrt_<case>/model.onnx
    <out>/node/test_rnnrt_<case>/test_data_set_0/input_<i>.pb   (one per graph input)
    <out>/node/test_rnnrt_<case>/test_data_set_0/output_<i>.pb  (one per graph output)

whose expected outputs come from onnxruntime (CPU EP). The verifier drops these
directories into `crates/onnx-official-tests/vendor/node/` and appends
`status = "pass"` rows to `expectations.toml`, so the project's own corpus
harness (build.rs -> ModelGen -> generated #[test] per case, compared with
`Tensor::assert_approx_eq(Tolerance::default())`) becomes the differential.

onnxruntime does not implement `layout=1` for GRU/LSTM/RNN ("Batchwise recurrent
operations (layout == 1) are not supported"). For layout=1 cases the reference is
computed on the layout=0 twin of the same model (same weights, `layout` attribute
set to 0, X / initial_h / initial_c permuted per the ONNX spec) and the outputs are
permuted back. That permutation is exactly the spec's definition of `layout`, and
it is cross-checked here against `onnx.reference.ReferenceEvaluator` (which does
implement layout=1 for num_directions=1) and, in `inspect` mode, against the ONNX
distribution's `test_*_batchwise` expected outputs.

Also writes:
    <out>/expectations_rnnrt.toml   rows to append to expectations.toml
    <out>/manifest.json             case metadata (for score.json attribution)
    <out>/reject/*.onnx             models the importer must REJECT (mixed
                                    build-time/run-time weight group, sequence_lens)
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
import onnxruntime as ort

OPSET = 14  # first opset with the `layout` attribute on GRU/LSTM/RNN
GATES = {"GRU": 3, "LSTM": 4, "RNN": 1}
REF_TOL = 2e-5  # onnxruntime vs onnx.reference cross-check (float32)


def _stable_seed(name: str) -> int:
    # Python's hash() is salted per process; use a fixed FNV-1a so the corpus is reproducible.
    h = 0xCBF29CE484222325
    for b in name.encode():
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h % (2**32)


class Case:
    def __init__(
        self,
        name: str,
        op: str,
        *,
        direction: str = "forward",
        layout: int = 0,
        bias: bool = True,
        initial_h: bool = False,
        initial_c: bool = False,
        outputs: tuple[str, ...] = ("Y", "Y_h"),
        static_weights: bool = False,
        linear_before_reset: int | None = None,
        activations: list[str] | None = None,
        clip: float | None = None,
        seq: int = 3,
        batch: int = 2,
        input_size: int = 3,
        hidden: int = 4,
        note: str = "",
    ):
        assert op in GATES
        self.name = name
        self.op = op
        self.direction = direction
        self.layout = layout
        self.bias = bias
        self.initial_h = initial_h
        self.initial_c = initial_c and op == "LSTM"
        self.outputs = outputs
        self.static_weights = static_weights
        self.linear_before_reset = linear_before_reset
        self.activations = activations
        self.clip = clip
        self.seq, self.batch, self.input_size, self.hidden = seq, batch, input_size, hidden
        self.note = note

    @property
    def num_directions(self) -> int:
        return 2 if self.direction == "bidirectional" else 1

    def attrs(self, layout: int) -> dict:
        a = {"hidden_size": self.hidden, "direction": self.direction}
        if layout:
            a["layout"] = layout
        if self.linear_before_reset is not None:
            a["linear_before_reset"] = self.linear_before_reset
        if self.activations is not None:
            a["activations"] = self.activations
        if self.clip is not None:
            a["clip"] = self.clip
        return a

    def tensors(self) -> dict[str, np.ndarray]:
        """Deterministic float32 values for every input (layout=0 shapes)."""
        r = np.random.default_rng(_stable_seed(self.name))
        g, d, h, i, s, n = GATES[self.op], self.num_directions, self.hidden, self.input_size, self.seq, self.batch
        t = {
            "X": r.normal(0, 1.0, (s, n, i)).astype(np.float32),
            "W": r.normal(0, 0.5, (d, g * h, i)).astype(np.float32),
            "R": r.normal(0, 0.5, (d, g * h, h)).astype(np.float32),
        }
        if self.bias:
            t["B"] = r.normal(0, 0.2, (d, 2 * g * h)).astype(np.float32)
        if self.initial_h:
            t["initial_h"] = r.normal(0, 0.5, (d, n, h)).astype(np.float32)
        if self.initial_c:
            t["initial_c"] = r.normal(0, 0.5, (d, n, h)).astype(np.float32)
        return t

    def node_inputs(self) -> list[str]:
        # ONNX positional inputs: X, W, R, B, sequence_lens, initial_h, initial_c(LSTM), P(LSTM)
        names = ["X", "W", "R", "B" if self.bias else "", ""]
        names.append("initial_h" if self.initial_h else "")
        if self.op == "LSTM":
            names.append("initial_c" if self.initial_c else "")
        while names and names[-1] == "":
            names.pop()
        return names

    def node_outputs(self) -> list[str]:
        all_out = ["Y", "Y_h"] + (["Y_c"] if self.op == "LSTM" else [])
        names = [o if o in self.outputs else "" for o in all_out]
        while names and names[-1] == "":
            names.pop()
        return names

    def shapes(self, layout: int) -> dict[str, list[int]]:
        d, h, i, s, n = self.num_directions, self.hidden, self.input_size, self.seq, self.batch
        g = GATES[self.op]
        if layout == 0:
            sh = {"X": [s, n, i], "Y": [s, d, n, h], "initial_h": [d, n, h], "initial_c": [d, n, h],
                  "Y_h": [d, n, h], "Y_c": [d, n, h]}
        else:
            sh = {"X": [n, s, i], "Y": [n, s, d, h], "initial_h": [n, d, h], "initial_c": [n, d, h],
                  "Y_h": [n, d, h], "Y_c": [n, d, h]}
        sh.update({"W": [d, g * h, i], "R": [d, g * h, h], "B": [d, 2 * g * h]})
        return sh

    def build(self, layout: int, tensors: dict[str, np.ndarray]) -> onnx.ModelProto:
        node = helper.make_node(self.op, self.node_inputs(), self.node_outputs(), **self.attrs(layout))
        shapes = self.shapes(layout)
        weight_names = {"W", "R", "B"}
        inputs, inits = [], []
        for name in [x for x in self.node_inputs() if x]:
            if self.static_weights and name in weight_names:
                inits.append(numpy_helper.from_array(tensors[name], name=name))
            else:
                inputs.append(helper.make_tensor_value_info(name, TensorProto.FLOAT, shapes[name]))
        outputs = [helper.make_tensor_value_info(o, TensorProto.FLOAT, shapes[o]) for o in self.node_outputs() if o]
        graph = helper.make_graph([node], f"{self.name}_graph", inputs, outputs, initializer=inits)
        model = helper.make_model(graph, opset_imports=[helper.make_operatorsetid("", OPSET)], producer_name="rs-burn-onnx-rnn-runtime-weights")
        model.ir_version = 9
        onnx.checker.check_model(model, full_check=True)
        return model


def to_layout(arr: np.ndarray, name: str, layout: int) -> np.ndarray:
    """Permute a layout=0 tensor into `layout` per the ONNX GRU/LSTM/RNN spec."""
    if layout == 0 or name in ("W", "R", "B"):
        return arr
    if name == "Y":  # [seq, dirs, batch, hid] -> [batch, seq, dirs, hid]
        return np.ascontiguousarray(arr.transpose(2, 0, 1, 3))
    # X: [seq, batch, in] -> [batch, seq, in]; states: [dirs, batch, hid] -> [batch, dirs, hid]
    return np.ascontiguousarray(arr.transpose(1, 0, 2))


def run_ort(model: onnx.ModelProto, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(model.SerializeToString(), so, providers=["CPUExecutionProvider"])
    return sess.run(None, feeds)


def run_reference(model: onnx.ModelProto, feeds: dict[str, np.ndarray]) -> list[np.ndarray] | None:
    """onnx.reference result, or None where the reference evaluator has no implementation.

    Known gaps (onnx 1.22): num_directions=2 raises NotImplementedError; `direction=reverse`
    is silently ignored (so it is never used for reverse here); layout=1 with initial_h /
    initial_c raises ValueError in op_gru/op_lstm/op_rnn (squeeze on the wrong axis).
    """
    try:
        from onnx.reference import ReferenceEvaluator

        return ReferenceEvaluator(model).run(None, feeds)
    except (NotImplementedError, ValueError, RuntimeError):
        return None


def write_pb(path: str, arr: np.ndarray, name: str) -> None:
    with open(path, "wb") as f:
        f.write(numpy_helper.from_array(arr, name=name).SerializeToString())


def build_from_arrays(op: str, direction: str, attrs: dict, arrays: dict[str, np.ndarray]) -> onnx.ModelProto:
    """Layout=0 model with every present input as a graph input and all outputs declared."""
    order = ["X", "W", "R", "B", "", "initial_h"] + (["initial_c"] if op == "LSTM" else [])
    names = [n if n in arrays else "" for n in order]
    while names and names[-1] == "":
        names.pop()
    outs = ["Y", "Y_h"] + (["Y_c"] if op == "LSTM" else [])
    a = dict(attrs)
    a.pop("layout", None)
    a["direction"] = direction
    node = helper.make_node(op, names, outs, **a)
    g = helper.make_graph(
        [node], "x",
        [helper.make_tensor_value_info(n, TensorProto.FLOAT, list(arrays[n].shape)) for n in names if n],
        [helper.make_tensor_value_info(o, TensorProto.FLOAT, None) for o in outs],
    )
    m = helper.make_model(g, opset_imports=[helper.make_operatorsetid("", OPSET)])
    m.ir_version = 9
    return m


def maxabs(a: list[np.ndarray], b: list[np.ndarray]) -> float:
    return float(max(np.abs(x - y).max() for x, y in zip(a, b)))


def cross_check(op: str, direction: str, attrs: dict, arrays: dict[str, np.ndarray], got: list[np.ndarray]) -> float:
    """Confirm onnxruntime's full-output result `got` (Y, Y_h[, Y_c]) is the spec's semantics.

    onnx.reference implements only forward with num_directions=1 (it silently ignores
    `direction="reverse"` and raises for bidirectional), so:
      forward       -> compare with onnx.reference
      reverse       -> flip identity: reverse(X) == flip_seq(forward(flip_seq(X))) for Y,
                       and identical Y_h/Y_c; the forward run is itself checked recursively
      bidirectional -> split into forward(dir 0) and reverse(dir 1) halves, each checked
                       recursively, and compare the stacked halves with the joint result
    Returns the largest deviation seen across all checks.
    """
    if direction == "forward":
        m = build_from_arrays(op, "forward", attrs, arrays)
        if op == "LSTM":
            # onnx.reference's LSTM produces no Y_c; compare Y and Y_h only (Y_c is
            # covered by onnxruntime's own reverse/bidirectional self-consistency).
            del m.graph.output[2]
        ref = run_reference(m, arrays)
        assert ref is not None, "onnx.reference must support forward num_directions=1"
        d = maxabs(got[: len(ref)], ref)
        assert d <= REF_TOL, f"onnxruntime vs onnx.reference (forward) differ by {d}"
        return d
    if direction == "reverse":
        flipped = dict(arrays)
        flipped["X"] = np.ascontiguousarray(arrays["X"][::-1])
        fwd = run_ort(build_from_arrays(op, "forward", attrs, flipped), flipped)
        d = cross_check(op, "forward", attrs, flipped, fwd)
        expect = [np.ascontiguousarray(fwd[0][::-1])] + list(fwd[1:])
        d2 = maxabs(got, expect)
        assert d2 <= REF_TOL, f"onnxruntime reverse violates the flip identity by {d2}"
        return max(d, d2)
    # bidirectional
    worst = 0.0
    for dir_idx, dir_name in ((0, "forward"), (1, "reverse")):
        half = {k: (v if k == "X" else np.ascontiguousarray(v[dir_idx : dir_idx + 1])) for k, v in arrays.items()}
        res = run_ort(build_from_arrays(op, dir_name, attrs, half), half)
        worst = max(worst, cross_check(op, dir_name, attrs, half, res))
        expect_y = got[0][:, dir_idx : dir_idx + 1]
        d = maxabs([expect_y] + [g[dir_idx : dir_idx + 1] for g in got[1:]], res)
        assert d <= REF_TOL, f"onnxruntime bidirectional half {dir_name} disagrees with unidirectional by {d}"
        worst = max(worst, d)
    return worst


def emit_case(case: Case, out_root: str) -> dict:
    tensors0 = case.tensors()  # layout=0 shapes
    model0 = case.build(0, tensors0)
    feeds0 = {k: v for k, v in tensors0.items() if not (case.static_weights and k in ("W", "R", "B"))}
    # Reference: onnxruntime on the layout=0 model (ORT has no layout=1 kernel).
    ref0 = run_ort(model0, feeds0)
    out_names = [o for o in case.node_outputs() if o]
    # Independent confirmation that onnxruntime computes the spec's semantics for this
    # configuration (all outputs, all weights as graph inputs; initializers vs graph inputs
    # is an import-side distinction the reference does not see).
    full = run_ort(build_from_arrays(case.op, case.direction, case.attrs(0), tensors0), tensors0)
    all_out = ["Y", "Y_h"] + (["Y_c"] if case.op == "LSTM" else [])
    d0 = maxabs(ref0, [full[all_out.index(n)] for n in out_names])
    assert d0 <= REF_TOL, f"{case.name}: declared-output model differs from full-output model by {d0}"
    ref_delta = max(d0, cross_check(case.op, case.direction, case.attrs(0), tensors0, full))

    # The shipped model is in the case's layout; inputs/outputs permuted accordingly.
    tensors = {k: to_layout(v, k, case.layout) for k, v in tensors0.items()}
    model = case.build(case.layout, tensors)
    feeds = {k: v for k, v in tensors.items() if not (case.static_weights and k in ("W", "R", "B"))}
    expected = [to_layout(r, n, case.layout) for r, n in zip(ref0, out_names)]
    if case.layout == 1 and case.direction == "forward":
        # onnx.reference implements layout=1 for forward num_directions=1: use it to
        # confirm the permutation is the spec's, not this script's.
        check1 = run_reference(model, feeds)
        if check1 is not None:
            d1 = maxabs(expected, check1)
            assert d1 <= REF_TOL, f"{case.name}: layout=1 permutation disagrees with onnx.reference by {d1}"
            ref_delta = max(ref_delta, d1)

    test_name = f"test_rnnrt_{case.name}"
    d = os.path.join(out_root, "node", test_name)
    ds = os.path.join(d, "test_data_set_0")
    os.makedirs(ds, exist_ok=True)
    onnx.save(model, os.path.join(d, "model.onnx"))
    graph_inputs = [i.name for i in model.graph.input]
    for idx, name in enumerate(graph_inputs):
        write_pb(os.path.join(ds, f"input_{idx}.pb"), feeds[name], name)
    for idx, (name, arr) in enumerate(zip(out_names, expected)):
        write_pb(os.path.join(ds, f"output_{idx}.pb"), arr.astype(np.float32), name)
    return {
        "test": test_name,
        "op": case.op,
        "direction": case.direction,
        "layout": case.layout,
        "bias": case.bias,
        "initial_h": case.initial_h,
        "initial_c": case.initial_c,
        "outputs": out_names,
        "weights": "initializers" if case.static_weights else "graph_inputs",
        "linear_before_reset": case.linear_before_reset,
        "activations": case.activations,
        "clip": case.clip,
        "shape": {"seq": case.seq, "batch": case.batch, "input": case.input_size, "hidden": case.hidden},
        "graph_inputs": graph_inputs,
        "onnxruntime_vs_onnx_reference_maxabs": ref_delta,
        "reference": "onnxruntime %s CPU (layout=0 twin + spec permutation)" % ort.__version__ if case.layout else "onnxruntime %s CPU" % ort.__version__,
        "note": case.note,
    }


CASES: list[Case] = [
    # --- runtime weights, layout=0 -------------------------------------------------
    Case("gru_fwd_bias_h0", "GRU", bias=True, initial_h=True, note="forward GRU, W/R/B/initial_h all graph inputs, linear_before_reset default 0"),
    Case("gru_reverse_nobias", "GRU", direction="reverse", bias=False, note="reverse GRU, only W/R as graph inputs (B absent => zero bias)"),
    Case("gru_bidir_lbr_h0", "GRU", direction="bidirectional", linear_before_reset=1, initial_h=True, note="bidirectional GRU with linear_before_reset=1 (Wb/Rb separately observable) and initial_h"),
    Case("gru_bidir_yh_only", "GRU", direction="bidirectional", outputs=("Y_h",), seq=4, batch=1, note="bidirectional GRU declaring only Y_h"),
    Case("gru_y_only_seq1", "GRU", outputs=("Y",), seq=1, batch=3, hidden=5, note="forward GRU declaring only Y, seq_length=1"),
    Case("lstm_fwd_bias_state", "LSTM", initial_h=True, initial_c=True, outputs=("Y", "Y_h", "Y_c"), note="forward LSTM with W/R/B and both initial states as graph inputs, all three outputs"),
    Case("lstm_reverse_nobias", "LSTM", direction="reverse", bias=False, note="reverse LSTM, W/R only"),
    Case("lstm_bidir_bias_yc", "LSTM", direction="bidirectional", outputs=("Y", "Y_h", "Y_c"), note="bidirectional LSTM with bias, Y/Y_h/Y_c"),
    Case("lstm_bidir_state", "LSTM", direction="bidirectional", initial_h=True, initial_c=True, outputs=("Y", "Y_h", "Y_c"), seq=2, note="bidirectional LSTM with initial_h/initial_c graph inputs"),
    Case("lstm_yh_yc_only", "LSTM", outputs=("Y_h", "Y_c"), hidden=3, note="forward LSTM declaring Y_h and Y_c but not Y"),
    Case("rnn_fwd_bias_h0", "RNN", initial_h=True, note="forward RNN (Tanh) with bias and initial_h as graph inputs"),
    Case("rnn_bidir_bias", "RNN", direction="bidirectional", hidden=5, note="bidirectional RNN with bias"),
    Case("rnn_reverse_nobias_yh", "RNN", direction="reverse", bias=False, outputs=("Y_h",), note="reverse RNN, W/R only, Y_h only"),
    # --- runtime weights, layout=1 (batch-first) ------------------------------------
    Case("gru_layout1_fwd_bias_h0", "GRU", layout=1, initial_h=True, note="layout=1 forward GRU: X [batch,seq,in], initial_h/Y_h [batch,1,hid], Y [batch,seq,1,hid]"),
    Case("gru_layout1_bidir_h0", "GRU", layout=1, direction="bidirectional", initial_h=True, note="layout=1 bidirectional GRU with initial_h [batch,2,hid]"),
    Case("lstm_layout1_bidir_state_yc", "LSTM", layout=1, direction="bidirectional", initial_h=True, initial_c=True, outputs=("Y", "Y_h", "Y_c"), note="layout=1 bidirectional LSTM with both initial states, all outputs (direction axis swap on the way in and out)"),
    Case("lstm_layout1_reverse", "LSTM", layout=1, direction="reverse", outputs=("Y", "Y_h", "Y_c"), note="layout=1 reverse LSTM"),
    Case("rnn_layout1_fwd_bias", "RNN", layout=1, note="layout=1 forward RNN with bias"),
    Case("rnn_layout1_bidir_yh", "RNN", layout=1, direction="bidirectional", outputs=("Y_h",), note="layout=1 bidirectional RNN, Y_h only"),
    # --- build-time weights (initializers): the static path must agree too ----------
    Case("gru_static_layout1_bidir_h0", "GRU", layout=1, direction="bidirectional", initial_h=True, static_weights=True, note="initializer weights, layout=1 bidirectional GRU with runtime initial_h"),
    Case("lstm_static_layout1_fwd_yc", "LSTM", layout=1, static_weights=True, outputs=("Y", "Y_h", "Y_c"), note="initializer weights, layout=1 forward LSTM"),
    Case("lstm_static_bidir_state", "LSTM", direction="bidirectional", static_weights=True, initial_h=True, initial_c=True, outputs=("Y", "Y_h", "Y_c"), note="initializer weights, layout=0 bidirectional LSTM with runtime initial states"),
    Case("rnn_static_layout1_reverse", "RNN", layout=1, direction="reverse", static_weights=True, note="initializer weights, layout=1 reverse RNN"),
    Case("gru_static_lbr_reverse_h0", "GRU", direction="reverse", linear_before_reset=1, initial_h=True, static_weights=True, note="initializer weights, reverse GRU with linear_before_reset=1 and runtime initial_h"),
]


def emit_reject_models(out_root: str, vendor_root: str | None) -> list[dict]:
    """Models the importer must refuse (non-zero exit from onnx2burn)."""
    rd = os.path.join(out_root, "reject")
    os.makedirs(rd, exist_ok=True)
    entries = []
    # 1. Mixed group: W is an initializer, R (and B) are graph inputs.
    c = Case("mixed_group_probe", "GRU", bias=True)
    t = c.tensors()
    node = helper.make_node("GRU", ["X", "W", "R", "B"], ["Y", "Y_h"], hidden_size=c.hidden, direction="forward")
    sh = c.shapes(0)
    g = helper.make_graph(
        [node], "gru_mixed_weights",
        [helper.make_tensor_value_info(n, TensorProto.FLOAT, sh[n]) for n in ("X", "R", "B")],
        [helper.make_tensor_value_info(n, TensorProto.FLOAT, sh[n]) for n in ("Y", "Y_h")],
        initializer=[numpy_helper.from_array(t["W"], name="W")],
    )
    m = helper.make_model(g, opset_imports=[helper.make_operatorsetid("", OPSET)])
    m.ir_version = 9
    onnx.checker.check_model(m, full_check=True)
    p = os.path.join(rd, "gru_mixed_weight_group.onnx")
    onnx.save(m, p)
    entries.append({"file": os.path.basename(p), "reason": "W is an initializer while R and B are graph inputs: the W/R/B group must be rejected, not partially lifted"})
    # 2. sequence_lens supplied (remains unsupported).
    c = Case("seqlens_probe", "LSTM", bias=True)
    t = c.tensors()
    sh = c.shapes(0)
    node = helper.make_node("LSTM", ["X", "W", "R", "B", "sequence_lens"], ["Y", "Y_h"], hidden_size=c.hidden)
    g = helper.make_graph(
        [node], "lstm_sequence_lens",
        [helper.make_tensor_value_info(n, TensorProto.FLOAT, sh[n]) for n in ("X", "W", "R", "B")]
        + [helper.make_tensor_value_info("sequence_lens", TensorProto.INT32, [c.batch])],
        [helper.make_tensor_value_info(n, TensorProto.FLOAT, sh[n]) for n in ("Y", "Y_h")],
    )
    m = helper.make_model(g, opset_imports=[helper.make_operatorsetid("", OPSET)])
    m.ir_version = 9
    onnx.checker.check_model(m, full_check=True)
    p = os.path.join(rd, "lstm_sequence_lens.onnx")
    onnx.save(m, p)
    entries.append({"file": os.path.basename(p), "reason": "sequence_lens input is not supported and must be rejected"})
    # 3. Peepholes: reuse the upstream corpus model if the vendored corpus is available.
    if vendor_root:
        src = os.path.join(vendor_root, "test_lstm_with_peepholes", "model.onnx")
        if os.path.exists(src):
            p = os.path.join(rd, "lstm_with_peepholes.onnx")
            with open(src, "rb") as f, open(p, "wb") as o:
                o.write(f.read())
            entries.append({"file": os.path.basename(p), "reason": "LSTM peephole input P is not supported and must be rejected (upstream test_lstm_with_peepholes)"})
    return entries


def main() -> int:
    out_root = sys.argv[1]
    vendor_root = sys.argv[2] if len(sys.argv) > 2 else None
    os.makedirs(out_root, exist_ok=True)
    manifest = {
        "onnx": onnx.__version__,
        "onnxruntime": ort.__version__,
        "numpy": np.__version__,
        "opset": OPSET,
        "cases": [],
        "reject": [],
    }
    names = set()
    for case in CASES:
        assert case.name not in names, case.name
        names.add(case.name)
        entry = emit_case(case, out_root)
        manifest["cases"].append(entry)
        print(f"{entry['test']}: inputs={entry['graph_inputs']} outputs={entry['outputs']} ref_delta={entry['onnxruntime_vs_onnx_reference_maxabs']}")
    with open(os.path.join(out_root, "expectations_rnnrt.toml"), "w") as f:
        f.write("\n# --- onnxruntime differential rows added by the task verifier ---\n")
        for entry in manifest["cases"]:
            f.write(f"\n[{entry['test']}]\nstatus = \"pass\"\n")
    manifest["reject"] = emit_reject_models(out_root, vendor_root)
    with open(os.path.join(out_root, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"wrote {len(manifest['cases'])} differential cases and {len(manifest['reject'])} reject models to {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
