#!/usr/bin/env python
"""Aggregate verifier group reports into score.json and reward.txt."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load(path):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"unreadable report: {e}"}


def main():
    log = Path(sys.argv[1])
    groups = {}

    pre = load(log / "precheck.json") or {"ok": False, "error": "precheck missing"}
    groups["submission_present"] = {"ok": bool(pre.get("submission_present")), "detail": pre.get("submission_source")}
    groups["anti_cheat"] = {"ok": bool(pre.get("anti_cheat_ok")), "hits": pre.get("anti_cheat_hits", [])}
    groups["build_status"] = {"ok": bool(pre.get("import_ok")), "detail": pre.get("import_error")}

    fam = load(log / "family.json")
    if fam is None:
        groups["family_harness"] = {"ok": False, "detail": "family.json missing (harness crashed or timed out)"}
    else:
        for name, g in fam.get("groups", {}).items():
            groups[name] = {
                "ok": bool(g.get("ok")),
                "passed": g.get("passed"),
                "expected": g.get("expected"),
                "failed": dict(list(g.get("failed", {}).items())[:25]),
            }
        groups["no_reference_backend_leak"] = {"ok": not fam.get("blocked_modules_seen_after_phase1"), "detail": fam.get("blocked_modules_seen_after_phase1")}

    for key, fname in (("upstream_reference_evaluator_test", "pytest_refeval.json"), ("upstream_backend_reference_test_cpu", "pytest_backend.json")):
        r = load(log / fname)
        if r is None:
            groups[key] = {"ok": False, "detail": f"{fname} missing (pytest crashed or timed out)"}
        else:
            groups[key] = {"ok": bool(r.get("ok")), "collected": r.get("collected"), "expected": r.get("expected"), "outcomes": r.get("outcomes"), "failed": r.get("failed", [])[:25]}

    reward = int(all(g["ok"] for g in groups.values()))
    score = {"reward": reward, "groups": groups, "onnx": (fam or {}).get("onnx"), "onnxruntime": (fam or {}).get("onnxruntime")}
    (log / "score.json").write_text(json.dumps(score, indent=2, sort_keys=True) + "\n")
    (log / "reward.txt").write_text(f"{reward}\n")
    print("score:", json.dumps({k: v["ok"] for k, v in groups.items()}), "reward", reward)
    return 0


if __name__ == "__main__":
    sys.exit(main())
