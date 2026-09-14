#!/usr/bin/env python
"""Clean-room verifier for py-xarray-zarr-align-chunks.

Runs inside the offline verifier image after Harbor re-materialises the agent's
``xarray/`` package at /workspace/repo/xarray. Everything the harness executes
(pytest configuration, upstream tests, hidden tests, reference reader) comes
from the verifier image, never from the submission.

Groups (all must pass with exact counts for reward 1):
  pr_tests_chunks      upstream test_backends_chunks.py (PR #10336 + #10516)
  pr_tests_backends    the four upstream test_backends.py tests the PRs added/changed
  crosscheck           authored differential checks against zarr-python + NumPy
                       (independent of the PR tests; see test_zarr_align_crosscheck.py)
  upstream_regression  the zarr selection of upstream test_backends.py, minus the
                       node ids that skip/xfail under the pinned wheels
Environment overrides (for local simulation): VERIFY_REPO, VERIFY_PRISTINE,
VERIFY_HIDDEN, VERIFY_LOGS, VERIFY_NPROC.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(os.environ.get("VERIFY_REPO", "/workspace/repo"))
PRISTINE = Path(os.environ.get("VERIFY_PRISTINE", "/opt/pristine/repo"))
HIDDEN = Path(os.environ.get("VERIFY_HIDDEN", "/tests/hidden"))
LOGS = Path(os.environ.get("VERIFY_LOGS", "/logs/verifier"))
NPROC = os.environ.get("VERIFY_NPROC", "4")
PY = sys.executable

LOGS.mkdir(parents=True, exist_ok=True)
EXPECTED = json.loads((HIDDEN / "expected.json").read_text())

SCORE: dict = {
    "task": "py-xarray-zarr-align-chunks",
    "reward": 0,
    "build": {"status": "pending", "steps": []},
    "anti_cheat": {"status": "pending", "checks": []},
    "groups": {},
    "independent_of_pr_tests": EXPECTED["independent_of_pr_tests"],
    "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}

# Patterns whose line count in the package (tests excluded) may not grow
# relative to the pristine tree. Legitimate pre-existing uses are tolerated.
PATTERNS = [
    r"_pytest",
    r"\bimport pytest\b",
    r"\bfrom pytest\b",
    r"\batexit\b",
    r"/logs",
    r"\breward\b",
    r"sys\.modules\[",
    r"numpy\.testing",
    r"np\.testing",
    r"\bmonkeypatch\b",
    r"unittest\.mock",
    r"importlib\.reload",
    r"\bbuiltins\.",
    r"sitecustomize|usercustomize",
    r"PYTEST_",
    r"assert_(identical|equal|allclose|array_equal)\s*=",
    r"zarr\.(open_group|open_array|open)\s*=",
    r"MemoryStore\s*=|LocalStore\s*=",
]
FORBIDDEN_SUFFIXES = {".pth", ".so", ".pyd", ".dylib"}
PROTECTED_DIRS = ["xarray/testing"]  # assertion helpers used by the hidden tests


def write_score() -> None:
    SCORE["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (LOGS / "score.json").write_text(json.dumps(SCORE, indent=2) + "\n")
    (LOGS / "reward.txt").write_text(f"{SCORE['reward']}\n")


def fail(section: str, message: str) -> None:
    SCORE[section]["status"] = "failed"
    SCORE[section].setdefault("error", message)
    SCORE["reward"] = 0
    print(f"[verify] {section} FAILED: {message}")
    write_score()
    sys.exit(0)


def step(msg: str) -> None:
    print(f"[verify] {msg}")
    SCORE["build"]["steps"].append(msg)


def pattern_counts(root: Path) -> dict[str, int]:
    counts = {p: 0 for p in PATTERNS}
    pkg = root / "xarray"
    for path in pkg.rglob("*.py"):
        rel = path.relative_to(pkg)
        if rel.parts and rel.parts[0] == "tests":
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for p in PATTERNS:
            counts[p] += len(re.findall(p, text))
    return counts


def tree_digest(root: Path, rel: str) -> dict[str, str]:
    import hashlib

    out = {}
    base = root / rel
    if not base.exists():
        return out
    for path in sorted(base.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            out[str(path.relative_to(base))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def run(cmd: list[str], log_name: str, timeout: int) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    with (LOGS / log_name).open("w") as fh:
        fh.write("$ " + " ".join(cmd) + "\n")
        fh.flush()
        try:
            return subprocess.run(cmd, cwd=REPO, env=env, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            fh.write(f"\n[verify] TIMEOUT after {timeout}s\n")
            return subprocess.CompletedProcess(cmd, returncode=124)


def parse_junit(path: Path) -> dict:
    result = {"collected": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0, "xfail": 0,
              "failed_ids": [], "skipped_ids": []}
    if not path.exists():
        result["error"] = 1
        result["failed_ids"] = ["<no junit xml produced>"]
        return result
    root = ET.parse(path).getroot()
    for tc in root.iter("testcase"):
        parts = tc.get("classname", "").split(".")
        file, rest = "", ""
        for i, p in enumerate(parts):
            if p.startswith("test_"):
                file = "/".join(parts[: i + 1]) + ".py"
                rest = "::".join(parts[i + 1 :])
                break
        nid = file + ("::" + rest if rest else "") + "::" + tc.get("name", "")
        result["collected"] += 1
        sk = tc.find("skipped")
        if sk is not None:
            if sk.get("type") == "pytest.xfail":
                result["xfail"] += 1
            else:
                result["skipped"] += 1
            result["skipped_ids"].append(nid)
        elif tc.find("error") is not None:
            result["error"] += 1
            result["failed_ids"].append(nid)
        elif tc.find("failure") is not None:
            result["failed"] += 1
            result["failed_ids"].append(nid)
        else:
            result["passed"] += 1
    result["failed_ids"] = result["failed_ids"][:40]
    result["skipped_ids"] = result["skipped_ids"][:40]
    return result


def run_group(name: str, args: list[str], expected: int, xdist: bool = False) -> bool:
    junit = LOGS / f"{name}.xml"
    cmd = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--timeout=900",
           "-W", "ignore::pytest.PytestCacheWarning", f"--junitxml={junit}", *args]
    if xdist:
        cmd += ["-n", NPROC]
    t0 = time.time()
    proc = run(cmd, f"{name}.log", timeout=2400)
    res = parse_junit(junit)
    res["exit_code"] = proc.returncode
    res["seconds"] = round(time.time() - t0, 1)
    res["expected"] = expected
    ok = (
        proc.returncode == 0
        and res["collected"] == expected
        and res["passed"] == expected
        and res["failed"] == 0
        and res["error"] == 0
        and res["skipped"] == 0
        and res["xfail"] == 0
    )
    res["ok"] = ok
    SCORE["groups"][name] = res
    print(f"[verify] group {name}: {'ok' if ok else 'FAIL'} "
          f"passed={res['passed']} failed={res['failed']} error={res['error']} "
          f"skipped={res['skipped']} xfail={res['xfail']} collected={res['collected']} expected={expected} "
          f"exit={proc.returncode} ({res['seconds']}s)")
    write_score()
    return ok


def main() -> None:
    write_score()
    pkg = REPO / "xarray"
    if not (pkg / "__init__.py").exists():
        fail("build", f"no submission package at {pkg}")
    step(f"submission package present at {pkg}")

    # ---- clean room: restore everything the harness executes from pristine ----
    for rel in ("conftest.py", "pyproject.toml", "setup.py"):
        src = PRISTINE / rel
        if src.exists():
            shutil.copy2(src, REPO / rel)
    if (REPO / "properties").exists():
        shutil.rmtree(REPO / "properties")
    if (PRISTINE / "properties").exists():
        shutil.copytree(PRISTINE / "properties", REPO / "properties")
    shutil.rmtree(pkg / "tests", ignore_errors=True)
    shutil.copytree(PRISTINE / "xarray" / "tests", pkg / "tests")
    for cache in pkg.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    step("restored conftest.py, pyproject.toml, setup.py, properties/ and xarray/tests from pristine")

    # ---- anti-cheat ----
    checks = SCORE["anti_cheat"]["checks"]
    problems = []
    stray_conftests = [str(p.relative_to(REPO)) for p in pkg.rglob("conftest.py")
                       if p.relative_to(pkg).parts[:1] != ("tests",)]
    checks.append({"check": "no conftest.py inside package", "hits": stray_conftests})
    if stray_conftests:
        problems.append(f"conftest.py inside package: {stray_conftests}")
    forbidden = [str(p.relative_to(REPO)) for p in pkg.rglob("*")
                 if p.is_file() and (p.suffix in FORBIDDEN_SUFFIXES or p.name in ("sitecustomize.py", "usercustomize.py"))]
    checks.append({"check": "no .pth/.so/.pyd/sitecustomize files", "hits": forbidden})
    if forbidden:
        problems.append(f"forbidden files: {forbidden}")
    base_counts = pattern_counts(PRISTINE)
    sub_counts = pattern_counts(REPO)
    grown = {p: (base_counts[p], sub_counts[p]) for p in PATTERNS if sub_counts[p] > base_counts[p]}
    checks.append({"check": "no new harness-tampering patterns vs pristine", "grown": grown})
    if grown:
        problems.append(f"patterns grew vs pristine: {grown}")
    for rel in PROTECTED_DIRS:
        if tree_digest(PRISTINE, rel) != tree_digest(REPO, rel):
            problems.append(f"protected directory modified: {rel}")
        checks.append({"check": f"{rel} unchanged", "ok": tree_digest(PRISTINE, rel) == tree_digest(REPO, rel)})
    if problems:
        fail("anti_cheat", "; ".join(problems))
    SCORE["anti_cheat"]["status"] = "ok"
    step("anti-cheat checks passed")

    # ---- install hidden tests ----
    shutil.copy2(HIDDEN / "test_backends_chunks.py", pkg / "tests" / "test_backends_chunks.py")
    shutil.copy2(HIDDEN / "test_zarr_align_crosscheck.py", pkg / "tests" / "test_zarr_align_crosscheck.py")
    patch = HIDDEN / "test_backends_align.patch"
    chk = subprocess.run(["git", "apply", "--check", str(patch)], cwd=REPO, capture_output=True, text=True)
    if chk.returncode != 0:
        fail("build", f"hidden test patch does not apply to pristine test_backends.py: {chk.stderr[:500]}")
    subprocess.run(["git", "apply", str(patch)], cwd=REPO, check=True)
    step("installed hidden tests (test_backends_chunks.py, test_zarr_align_crosscheck.py, test_backends.py hunks)")

    # ---- import sanity: the tests must exercise the submitted package ----
    probe = run([PY, "-c", "import xarray, zarr, dask; print(xarray.__file__); print(xarray.__version__); print(zarr.__version__)"],
                "import_probe.log", timeout=300)
    text = (LOGS / "import_probe.log").read_text()
    if probe.returncode != 0 or str(pkg) not in text:
        fail("build", f"submitted package does not import from {pkg}: {text[-800:]}")
    SCORE["build"]["import_probe"] = text.strip().splitlines()[-3:]
    SCORE["build"]["status"] = "ok"
    step("import probe ok")

    # ---- groups ----
    deselect = [line.strip() for line in (HIDDEN / "deselect_upstream.txt").read_text().splitlines() if line.strip()]
    deselect_args: list[str] = []
    for nid in deselect:
        deselect_args += ["--deselect", nid]
    focused_deselect: list[str] = []
    for nid in EXPECTED["focused_deselect"]:
        focused_deselect += ["--deselect", nid]

    ok = True
    ok &= run_group("pr_tests_chunks", ["xarray/tests/test_backends_chunks.py"], EXPECTED["pr_tests_chunks"])
    ok &= run_group("pr_tests_backends",
                    ["xarray/tests/test_backends.py", "-k", EXPECTED["focused_keyword"], *focused_deselect],
                    EXPECTED["pr_tests_backends"])
    ok &= run_group("crosscheck", ["xarray/tests/test_zarr_align_crosscheck.py"], EXPECTED["crosscheck"])
    ok &= run_group("upstream_regression",
                    ["xarray/tests/test_backends.py", "-k", EXPECTED["regression_keyword"], *deselect_args],
                    EXPECTED["upstream_regression"], xdist=True)

    SCORE["reward"] = int(bool(ok))
    write_score()
    print(f"[verify] reward={SCORE['reward']}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # any harness error is a 0, recorded
        SCORE["build"]["status"] = "error"
        SCORE["build"]["error"] = f"{type(exc).__name__}: {exc}"
        SCORE["reward"] = 0
        write_score()
        print(f"[verify] harness error: {exc}")
