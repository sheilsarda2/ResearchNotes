#!/usr/bin/env python3
"""Turn the verifier's key=value state file into score.json and reward.txt."""

import json
import sys
from pathlib import Path

state_path, log_dir = Path(sys.argv[1]), Path(sys.argv[2])
kv = {}
for line in state_path.read_text().splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        kv[k] = v


def flag(name):
    return kv.get(name) == "true"


def num(name):
    try:
        return int(kv.get(name, "0"))
    except ValueError:
        return 0


groups = {
    "build": {"ok": flag("build_ok"), "seconds": num("build_seconds")},
    "anticheat": {"ok": flag("anticheat_ok"),
                  "hits_file": "anticheat_hits.txt" if not flag("anticheat_ok") else None},
    "pytorch_unit": {"ok": flag("pytorch_unit_ok"), "passed": num("pytorch_unit_passed"),
                     "failed": num("pytorch_unit_failed"), "ignored": num("pytorch_unit_ignored"),
                     "expected": 108},
    "safetensors_unit": {"ok": flag("safetensors_unit_ok"), "passed": num("safetensors_unit_passed"),
                         "failed": num("safetensors_unit_failed"), "expected": 52},
    "integration": {"ok": flag("integration_ok"), "passed": num("integration_passed"),
                    "failed": num("integration_failed"), "expected": 20},
    "pytorch_tests_crate": {"ok": flag("pytorch_tests_crate_ok"), "passed": num("pytorch_tests_crate_passed"),
                            "failed": num("pytorch_tests_crate_failed"), "expected": 37},
    "fixture_matrix": {"ok": flag("matrix_ok"), "passed": num("matrix_pass"), "failed": num("matrix_fail"),
                       "expected": num("matrix_expected"), "manifest_ok": flag("manifest_ok"),
                       "tensor_rows": num("manifest_tensor_rows")},
    "reject_cases": {"ok": flag("reject_ok"), "passed": num("reject_pass"), "failed": num("reject_fail"),
                     "expected": num("reject_expected")},
}
required = ["build", "anticheat", "pytorch_unit", "safetensors_unit", "integration",
            "pytorch_tests_crate", "fixture_matrix", "reject_cases"]
reward = 1 if all(groups[g]["ok"] for g in required) and flag("manifest_ok") else 0

failures = {}
for name in ("pytorch_unit", "safetensors_unit", "integration", "pytorch_tests_crate"):
    p = log_dir / f"{name}_failures.txt"
    if p.exists():
        failures[name] = p.read_text().splitlines()[:60]
for name, fname in (("fixture_matrix", "fixture_matrix.txt"), ("reject_cases", "reject_cases.txt")):
    p = log_dir / fname
    if p.exists():
        bad = [l for l in p.read_text().splitlines() if l.startswith("FAIL ")]
        if bad:
            failures[name] = bad[:60]

score = {"reward": reward, "groups": groups, "failures": failures, "raw": kv}
(log_dir / "score.json").write_text(json.dumps(score, indent=2) + "\n")
(log_dir / "reward.txt").write_text(f"{reward}\n")
print(json.dumps({"reward": reward, **{g: groups[g]["ok"] for g in required}}))
