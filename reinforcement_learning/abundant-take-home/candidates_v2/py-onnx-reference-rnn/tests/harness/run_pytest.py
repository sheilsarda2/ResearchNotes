#!/usr/bin/env python
"""Run one upstream pytest file with exact outcome accounting.

Usage: run_pytest.py --out report.json --expect-collected N --expect-passed N --expect-skipped N
                     [--deselect-exact NODEID ...] -- <pytest args>
Exit 0 iff no failures/errors/xfails and the counts match exactly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest


class Accounting:
    def __init__(self, deselect_exact):
        self.deselect_exact = set(deselect_exact)
        self.collected = 0
        self.deselected = 0
        self.outcomes = {"passed": 0, "failed": 0, "skipped": 0, "xfail": 0, "error": 0}
        self.failed_ids = []

    def pytest_collection_modifyitems(self, session, config, items):  # noqa: ARG002
        keep, drop = [], []
        for it in items:
            (drop if it.nodeid in self.deselect_exact else keep).append(it)
        if drop:
            config.hook.pytest_deselected(items=drop)
            items[:] = keep
        self.deselected = len(drop)

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report):
        if hasattr(report, "wasxfail"):
            self.outcomes["xfail"] += 1
            return
        if report.when == "call":
            if report.passed:
                self.outcomes["passed"] += 1
            elif report.failed:
                self.outcomes["failed"] += 1
                self.failed_ids.append(report.nodeid)
            elif report.skipped:
                self.outcomes["skipped"] += 1
        elif report.failed:
            self.outcomes["error"] += 1
            self.failed_ids.append(report.nodeid)
        elif report.skipped and report.when == "setup":
            self.outcomes["skipped"] += 1


def main():
    argv = sys.argv[1:]
    sep = argv.index("--")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--expect-collected", type=int, required=True)
    ap.add_argument("--expect-passed", type=int, required=True)
    ap.add_argument("--expect-skipped", type=int, required=True)
    ap.add_argument("--deselect-exact", action="append", default=[])
    args = ap.parse_args(argv[:sep])
    acc = Accounting(args.deselect_exact)
    status = pytest.main(argv[sep + 1 :], plugins=[acc])
    ok = (
        status in (0, 1)
        and acc.collected == args.expect_collected
        and acc.outcomes["passed"] == args.expect_passed
        and acc.outcomes["skipped"] == args.expect_skipped
        and acc.outcomes["failed"] == 0
        and acc.outcomes["error"] == 0
        and acc.outcomes["xfail"] == 0
    )
    report = {
        "ok": bool(ok),
        "exit_code": int(status),
        "collected": acc.collected,
        "deselected_exact": acc.deselected,
        "expected": {"collected": args.expect_collected, "passed": args.expect_passed, "skipped": args.expect_skipped},
        "outcomes": acc.outcomes,
        "failed": acc.failed_ids[:200],
    }
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    print("run_pytest:", "PASS" if ok else "FAIL", json.dumps({k: report[k] for k in ("collected", "outcomes")}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
