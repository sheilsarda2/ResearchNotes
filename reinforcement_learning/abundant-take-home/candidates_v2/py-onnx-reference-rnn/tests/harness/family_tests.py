#!/usr/bin/env python
"""Verifier harness for the RNN operator family (RNN, GRU, LSTM) of onnx.reference.

Phase 1 runs the submitted reference evaluator with ``onnxruntime`` blocked from
import; phase 2 imports onnxruntime and compares.  Groups (all exact counts):

  node_tests        official ONNX backend node tests for the family, expected outputs
  ort_node_models   the same node-test models, reference vs onnxruntime
  ort_generated     generated float32 attribute/input/output/opset combinations
  dtype_float16     generated float16 models, reference vs onnxruntime (float16 kernels)
  dtype_float64     generated float64 models, reference vs onnxruntime run in float32
  error_cases       malformed direction attribute / num_directions mismatch must raise

Writes a JSON report; exit status 0 iff every group passes.
"""
from __future__ import annotations

import argparse
import importlib.abc
import json
import signal
import sys
import time
import traceback

import numpy as np

FAMILY = ("RNN", "GRU", "LSTM")
GATES = {"RNN": 1, "GRU": 3, "LSTM": 4}
PEEPHOLES = 3
IR_FOR_OPSET = {7: 3, 14: 7, 22: 10}
DIMS = [(1, 1, 1, 1), (3, 2, 4, 5), (4, 3, 2, 3), (2, 4, 5, 2), (5, 1, 3, 4), (3, 3, 3, 3)]
TOL = {
    "node_tests": None,  # per-case rtol/atol from the upstream TestCase
    "ort_node_models": (1e-4, 1e-5),
    "ort_generated": (1e-4, 1e-5),
    "dtype_float16": (2e-2, 2e-3),
    "dtype_float64": (1e-4, 1e-5),
    "error_cases": None,  # must raise RuntimeError or ValueError
}
NP_DTYPE = {"float32": np.float32, "float16": np.float16, "float64": np.float64}


class _Blocker(importlib.abc.MetaPathFinder):
    """Refuse to import onnxruntime (and aliases) while the submission runs."""

    blocked = ("onnxruntime", "ort", "onnxruntime_extensions", "torch", "tensorflow", "jax")

    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root in self.blocked:
            raise ImportError(f"import of {fullname!r} is blocked inside the reference evaluator run")
        return None


class Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise Timeout("per-case timeout")


def with_timeout(seconds, fn, *a, **k):
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(seconds)
    try:
        return fn(*a, **k)
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)


# --------------------------------------------------------------------------- models
def tensor_type(dtype):
    from onnx import TensorProto

    return {"float32": TensorProto.FLOAT, "float16": TensorProto.FLOAT16, "float64": TensorProto.DOUBLE}[dtype]


def make_case(spec):
    """Build (model, feeds, meta) from a case spec dict.  Deterministic by spec['seed']."""
    import onnx
    from onnx import helper

    op = spec["op"]
    seq, batch, inp, hid = spec["dims"]
    nd = 2 if spec["direction"] == "bidirectional" else 1
    g = GATES[op]
    dt = NP_DTYPE[spec["dtype"]]
    rng = np.random.default_rng(spec["seed"])

    def rnd(shape, scale):
        return (rng.uniform(-1.0, 1.0, size=shape) * scale).astype(np.float32).astype(dt)

    layout = spec.get("layout", 0)
    feeds = {}
    feeds["X"] = rnd((batch, seq, inp) if layout == 1 else (seq, batch, inp), 1.0)
    feeds["W"] = rnd((nd, g * hid, inp), 1.0 / np.sqrt(inp))
    feeds["R"] = rnd((nd, g * hid, hid), 1.0 / np.sqrt(hid))
    inputs = ["X", "W", "R"]
    optional = ["B", "sequence_lens", "initial_h"] + (["initial_c", "P"] if op == "LSTM" else [])
    present = {
        "B": spec.get("B", False),
        "sequence_lens": spec.get("sequence_lens", False),
        "initial_h": spec.get("initial_h", False),
        "initial_c": spec.get("initial_c", False),
        "P": spec.get("P", False),
    }
    if present["B"]:
        feeds["B"] = rnd((nd, 2 * g * hid), 0.5)
    if present["sequence_lens"]:
        feeds["sequence_lens"] = np.full((batch,), seq, dtype=np.int32)
    if present["initial_h"]:
        feeds["initial_h"] = rnd((batch, nd, hid) if layout == 1 else (nd, batch, hid), 1.0)
    if present["initial_c"]:
        feeds["initial_c"] = rnd((batch, nd, hid) if layout == 1 else (nd, batch, hid), 1.0)
    if present["P"]:
        feeds["P"] = rnd((nd, PEEPHOLES * hid), 0.5)
    last = max([i for i, n in enumerate(optional) if present[n]], default=-1)
    for n in optional[: last + 1]:
        inputs.append(n if present[n] else "")

    attrs = {"hidden_size": hid}
    if spec["direction"] != "forward":
        attrs["direction"] = spec["direction"]
    if spec["opset"] >= 14 and layout == 1:
        attrs["layout"] = 1
    if op == "GRU" and spec.get("linear_before_reset", 0):
        attrs["linear_before_reset"] = 1
    if spec.get("activations"):
        attrs["activations"] = spec["activations"]
    if spec.get("activation_alpha"):
        attrs["activation_alpha"] = spec["activation_alpha"]
    if spec.get("activation_beta"):
        attrs["activation_beta"] = spec["activation_beta"]

    outputs = list(spec["outputs"])
    node = helper.make_node(op, inputs, outputs, **attrs)
    T = tensor_type(spec["dtype"])
    vi_in = [helper.make_tensor_value_info(n, T if n != "sequence_lens" else onnx.TensorProto.INT32, list(feeds[n].shape)) for n in inputs if n]
    # spec output shapes: Y [seq, nd, batch, hid] (layout 0) or [batch, seq, nd, hid] (layout 1);
    # Y_h / Y_c [nd, batch, hid] (layout 0) or [batch, nd, hid] (layout 1)
    y_shape = [batch, seq, nd, hid] if layout == 1 else [seq, nd, batch, hid]
    h_shape = [batch, nd, hid] if layout == 1 else [nd, batch, hid]
    vi_out = [helper.make_tensor_value_info(n, T, y_shape if i == 0 else h_shape) for i, n in enumerate(outputs) if n]
    graph = helper.make_graph([node], f"{op}_generated", vi_in, vi_out)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", spec["opset"])], producer_name="family_tests")
    model.ir_version = IR_FOR_OPSET[spec["opset"]]
    onnx.checker.check_model(model)
    meta = {"layout": layout, "out_roles": {n: i for i, n in enumerate(outputs) if n}}
    return model, feeds, meta


def generated_specs():
    """Deterministic list of (name, spec, group)."""
    specs = []
    idx = 0

    def add(group, **spec):
        nonlocal idx
        spec.setdefault("dtype", "float32")
        spec.setdefault("opset", 22)
        spec.setdefault("layout", 0)
        spec["dims"] = DIMS[idx % len(DIMS)] if "dims" not in spec else spec["dims"]
        spec["seed"] = 1000 + idx
        outs = ",".join(o or "_" for o in spec["outputs"])
        flags = "".join(k[0] for k in ("B", "sequence_lens", "initial_h", "initial_c", "P") if spec.get(k))
        name = (
            f"gen{idx:03d}_{spec['op']}_{spec['direction']}_L{spec['layout']}_in{flags or '-'}_out{outs}"
            f"_{spec['dtype']}_op{spec['opset']}"
        )
        if spec.get("linear_before_reset"):
            name += "_lbr"
        if spec.get("activations"):
            name += "_act" + "-".join(spec["activations"])
        specs.append((name, spec, group))
        idx += 1

    OUTS = {
        "RNN": [["Y", "Y_h"], ["", "Y_h"], ["Y"]],
        "GRU": [["Y", "Y_h"], ["", "Y_h"], ["Y"]],
        "LSTM": [["Y", "Y_h", "Y_c"], ["", "Y_h", "Y_c"], ["Y"], ["", "", "Y_c"]],
    }
    dirs = ("forward", "reverse", "bidirectional")
    # core matrix: op x direction x layout x B x initial_h  (outputs variant cycles)
    k = 0
    for op in FAMILY:
        for d in dirs:
            for layout in (0, 1):
                for b in (False, True):
                    for h in (False, True):
                        add("ort_generated", op=op, direction=d, layout=layout, B=b, initial_h=h, outputs=OUTS[op][k % len(OUTS[op])])
                        k += 1
    # output arity sweep
    for op in FAMILY:
        for outs in OUTS[op]:
            for d in ("forward", "bidirectional"):
                add("ort_generated", op=op, direction=d, B=True, initial_h=True, outputs=outs)
    # LSTM cell state and peepholes
    for c in (False, True):
        for p in (False, True):
            for d in dirs:
                for layout in (0, 1):
                    add("ort_generated", op="LSTM", direction=d, layout=layout, B=True, initial_h=True, initial_c=c, P=p, outputs=["Y", "Y_h", "Y_c"])
    # GRU linear_before_reset
    for d in dirs:
        for layout in (0, 1):
            for b in (False, True):
                add("ort_generated", op="GRU", direction=d, layout=layout, B=b, initial_h=True, linear_before_reset=1, outputs=["Y", "Y_h"])
    # RNN Affine activation with per-direction alpha/beta
    for d in dirs:
        nd = 2 if d == "bidirectional" else 1
        for layout in (0, 1):
            add("ort_generated", op="RNN", direction=d, layout=layout, B=True, outputs=["Y", "Y_h"],
                activations=["Affine"] * nd, activation_alpha=[0.8] * nd, activation_beta=[0.1] * nd)
    # explicit default activations
    DEFAULT_ACT = {"RNN": ["Tanh"], "GRU": ["Sigmoid", "Tanh"], "LSTM": ["Sigmoid", "Tanh", "Tanh"]}
    for op in FAMILY:
        for d in dirs:
            nd = 2 if d == "bidirectional" else 1
            add("ort_generated", op=op, direction=d, B=True, outputs=OUTS[op][0], activations=DEFAULT_ACT[op] * nd)
    # sequence_lens supplied (all equal to seq_length)
    for op in FAMILY:
        for d in dirs:
            add("ort_generated", op=op, direction=d, B=True, sequence_lens=True, initial_h=True, outputs=OUTS[op][0])
    # older opsets
    for op in FAMILY:
        for opset in (7, 14):
            for d in ("forward", "bidirectional"):
                add("ort_generated", op=op, direction=d, opset=opset, B=True, initial_h=True, outputs=OUTS[op][0])
    # dtypes
    for dtype, group in (("float16", "dtype_float16"), ("float64", "dtype_float64")):
        for op in FAMILY:
            for d in dirs:
                for layout in (0, 1):
                    add(group, op=op, direction=d, layout=layout, dtype=dtype, B=True, initial_h=True, outputs=OUTS[op][0], dims=(3, 2, 3, 4))
    return specs


def error_specs():
    """Cases that must raise RuntimeError or ValueError from construction or run()."""
    out = []
    for op in FAMILY:
        base = dict(op=op, direction="forward", layout=0, dtype="float32", opset=22, B=True, initial_h=True,
                    outputs=["Y", "Y_h"] if op != "LSTM" else ["Y", "Y_h", "Y_c"], dims=(3, 2, 4, 5))
        out.append((f"err_{op}_unknown_direction", dict(base, seed=7, bad_direction="sideways")))
        out.append((f"err_{op}_num_directions_mismatch", dict(base, seed=8, direction="bidirectional", w_directions=1)))
    return out


def make_error_case(spec):
    """Build a model that is well-formed at the proto level but semantically invalid for the operator."""
    from onnx import helper

    spec = dict(spec)
    bad_direction = spec.pop("bad_direction", None)
    w_directions = spec.pop("w_directions", None)
    model, feeds, meta = make_case(spec)
    node = model.graph.node[0]
    if bad_direction is not None:
        for a in node.attribute:
            if a.name == "direction":
                a.s = bad_direction.encode()
                break
        else:
            node.attribute.append(helper.make_attribute("direction", bad_direction))
    if w_directions is not None:
        # attribute says bidirectional (2 directions) but W/R/B/initial_h carry only `w_directions`
        for name in ("W", "R", "B", "initial_h", "initial_c", "P"):
            if name in feeds:
                feeds[name] = np.ascontiguousarray(feeds[name][:w_directions] if name != "initial_h" and name != "initial_c" else feeds[name][:w_directions])
    return model, feeds


# --------------------------------------------------------------------------- runners
def run_reference(model, feeds):
    from onnx.reference import ReferenceEvaluator

    sess = ReferenceEvaluator(model)
    outs = sess.run(None, feeds)
    return [np.asarray(o) for o in outs]


def run_ort(model, feeds, meta, dtype):
    """Run onnxruntime; layout=1 is emulated through the spec's transposition; float64 through float32."""
    import onnx
    import onnxruntime as ort

    m = onnx.ModelProto()
    m.CopyFrom(model)
    node = m.graph.node[0]
    f = dict(feeds)
    if dtype == "float64":
        for vi in list(m.graph.input) + list(m.graph.output):
            if vi.type.tensor_type.elem_type == onnx.TensorProto.DOUBLE:
                vi.type.tensor_type.elem_type = onnx.TensorProto.FLOAT
        f = {k: (v.astype(np.float32) if v.dtype == np.float64 else v) for k, v in f.items()}
    if meta["layout"] == 1:
        del node.attribute[:]
        for a in model.graph.node[0].attribute:
            if a.name != "layout":
                node.attribute.append(a)
        for name in ("X", "initial_h", "initial_c"):
            if name in f:
                f[name] = np.ascontiguousarray(np.swapaxes(f[name], 0, 1))
        for vi in m.graph.input:
            if vi.name in ("X", "initial_h", "initial_c"):
                d = vi.type.tensor_type.shape.dim
                d[0].dim_value, d[1].dim_value = d[1].dim_value, d[0].dim_value
    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(m.SerializeToString(), so, providers=["CPUExecutionProvider"])
    outs = sess.run(None, f)
    if meta["layout"] == 1:
        fixed = []
        for vi, o in zip(m.graph.output, outs):
            role = meta["out_roles"][vi.name]
            fixed.append(np.transpose(o, (2, 0, 1, 3)) if role == 0 else np.swapaxes(o, 0, 1))
        outs = fixed
    return [np.asarray(o) for o in outs]


def compare(got, ref, rtol, atol, check_dtype=True):
    if len(got) != len(ref):
        return f"output count {len(got)} != {len(ref)}"
    for i, (g, r) in enumerate(zip(got, ref)):
        if check_dtype and g.dtype != r.dtype:
            return f"output {i} dtype {g.dtype} != {r.dtype}"
        if g.shape != r.shape:
            return f"output {i} shape {g.shape} != {r.shape}"
        try:
            np.testing.assert_allclose(g.astype(np.float64), r.astype(np.float64), rtol=rtol, atol=atol)
        except AssertionError as e:
            return f"output {i}: " + str(e).strip().splitlines()[0][:200] + " | max abs diff " + repr(float(np.max(np.abs(g.astype(np.float64) - r.astype(np.float64)))))
    return None


def load_family_node_cases():
    from onnx.backend.test.loader import load_model_tests

    cases = []
    for case in load_model_tests(kind="node"):
        if case.model is None:
            continue
        if any(n.op_type in FAMILY and n.domain in ("", "ai.onnx") for n in case.model.graph.node):
            cases.append(case)
    cases.sort(key=lambda c: c.name)
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--expected-node-cases", type=int, default=18)
    ap.add_argument("--case-timeout", type=int, default=120)
    args = ap.parse_args()

    report = {"groups": {}, "phase1_seconds": None, "phase2_seconds": None, "python": sys.version}
    t0 = time.time()

    # ---------------- phase 1: submission runs with onnxruntime blocked
    blocker = _Blocker()
    sys.meta_path.insert(0, blocker)
    assert "onnxruntime" not in sys.modules
    import onnx  # noqa: F401  (must import after the blocker; onnx itself never imports onnxruntime)

    node_cases = load_family_node_cases()
    node_results = {}  # name -> (model, feeds, meta, expected_outputs, rtol, atol, ref_outputs or error)
    for case in node_cases:
        model = case.model
        assert len(case.data_sets) == 1, case.name
        inputs, expected = case.data_sets[0]
        feeds = {vi.name: np.asarray(x) for vi, x in zip(model.graph.input, inputs)}
        node = next(n for n in model.graph.node if n.op_type in FAMILY)
        layout = next((a.i for a in node.attribute if a.name == "layout"), 0)
        meta = {"layout": int(layout), "out_roles": {n: i for i, n in enumerate(node.output) if n}}
        try:
            got = with_timeout(args.case_timeout, run_reference, model, feeds)
            node_results[case.name] = (model, feeds, meta, [np.asarray(e) for e in expected], case.rtol, case.atol, got, None)
        except BaseException as e:  # noqa: BLE001
            node_results[case.name] = (model, feeds, meta, [np.asarray(e) for e in expected], case.rtol, case.atol, None, f"{type(e).__name__}: {str(e)[:300]}")

    gen_results = []  # (name, group, model, feeds, meta, dtype, got or None, error)
    for name, spec, group in generated_specs():
        try:
            model, feeds, meta = make_case(spec)
        except Exception:  # noqa: BLE001
            gen_results.append((name, group, None, None, None, spec["dtype"], None, "harness model construction failed: " + traceback.format_exc()[-300:]))
            continue
        try:
            got = with_timeout(args.case_timeout, run_reference, model, feeds)
            gen_results.append((name, group, model, feeds, meta, spec["dtype"], got, None))
        except BaseException as e:  # noqa: BLE001
            gen_results.append((name, group, model, feeds, meta, spec["dtype"], None, f"{type(e).__name__}: {str(e)[:300]}"))

    err_results = []  # (name, outcome)  outcome: None if raised RuntimeError/ValueError else description
    for name, spec in error_specs():
        try:
            model, feeds = make_error_case(spec)
        except Exception:  # noqa: BLE001
            err_results.append((name, "harness model construction failed: " + traceback.format_exc()[-300:]))
            continue
        try:
            with_timeout(args.case_timeout, run_reference, model, feeds)
            err_results.append((name, "no exception raised"))
        except NotImplementedError as e:  # includes RuntimeImplementationError: the operator is simply missing
            err_results.append((name, f"raised {type(e).__name__} (operator not implemented): {str(e)[:120]}"))
        except (RuntimeError, ValueError):
            err_results.append((name, None))
        except BaseException as e:  # noqa: BLE001
            err_results.append((name, f"raised {type(e).__name__} instead of RuntimeError/ValueError: {str(e)[:200]}"))

    sys.meta_path.remove(blocker)
    report["phase1_seconds"] = round(time.time() - t0, 2)
    leaked = sorted(m for m in sys.modules if m.split(".")[0] in _Blocker.blocked)
    report["blocked_modules_seen_after_phase1"] = leaked

    # ---------------- phase 2: compare against expected outputs and onnxruntime
    t1 = time.time()
    import onnxruntime  # noqa: F401

    report["onnxruntime"] = onnxruntime.__version__
    report["onnx"] = onnx.__version__

    groups = {g: {"expected": 0, "passed": 0, "failed": {}} for g in TOL}
    groups["node_tests"]["expected"] = args.expected_node_cases
    groups["ort_node_models"]["expected"] = args.expected_node_cases
    if len(node_results) != args.expected_node_cases:
        groups["node_tests"]["failed"]["__count__"] = f"loaded {len(node_results)} family node cases, expected {args.expected_node_cases}"
    for name, (model, feeds, meta, expected, rtol, atol, got, err) in sorted(node_results.items()):
        if err is not None:
            groups["node_tests"]["failed"][name] = err
            groups["ort_node_models"]["failed"][name] = "reference run failed"
            continue
        e = compare(got, expected, rtol, atol)
        if e is None:
            groups["node_tests"]["passed"] += 1
        else:
            groups["node_tests"]["failed"][name] = e
        try:
            ort_out = run_ort(model, feeds, meta, "float32")
            e = compare(got, ort_out, *TOL["ort_node_models"])
        except Exception as ex:  # noqa: BLE001
            e = f"onnxruntime failed: {type(ex).__name__}: {str(ex)[:200]}"
        if e is None:
            groups["ort_node_models"]["passed"] += 1
        else:
            groups["ort_node_models"]["failed"][name] = e

    for name, group, model, feeds, meta, dtype, got, err in gen_results:
        groups[group]["expected"] += 1
        if err is not None:
            groups[group]["failed"][name] = err
            continue
        try:
            ort_out = run_ort(model, feeds, meta, dtype)
        except Exception as ex:  # noqa: BLE001
            groups[group]["failed"][name] = f"onnxruntime failed: {type(ex).__name__}: {str(ex)[:200]}"
            continue
        rtol, atol = TOL[group]
        if dtype == "float64":
            e = None
            for i, g in enumerate(got):
                if g.dtype != np.float64:
                    e = f"output {i} dtype {g.dtype} != float64"
                    break
            e = e or compare(got, ort_out, rtol, atol, check_dtype=False)
        else:
            e = compare(got, ort_out, rtol, atol)
        if e is None:
            groups[group]["passed"] += 1
        else:
            groups[group]["failed"][name] = e

    for name, outcome in err_results:
        groups["error_cases"]["expected"] += 1
        if outcome is None:
            groups["error_cases"]["passed"] += 1
        else:
            groups["error_cases"]["failed"][name] = outcome

    for g in groups.values():
        g["ok"] = g["expected"] > 0 and g["passed"] == g["expected"] and not g["failed"]
    report["groups"] = groups
    report["phase2_seconds"] = round(time.time() - t1, 2)
    report["ok"] = all(g["ok"] for g in groups.values()) and not leaked
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    summary = {k: f"{v['passed']}/{v['expected']}" for k, v in groups.items()}
    print("family_tests:", "PASS" if report["ok"] else "FAIL", summary, f"phase1={report['phase1_seconds']}s phase2={report['phase2_seconds']}s")
    for k, v in groups.items():
        for name, why in list(v["failed"].items())[:8]:
            print(f"  [{k}] {name}: {why}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
