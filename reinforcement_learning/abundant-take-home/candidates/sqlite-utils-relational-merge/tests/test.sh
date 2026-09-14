#!/usr/bin/env bash
set -uo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
cd /workspace/repo || exit 1
python - <<'PY'
import json
from pathlib import Path
import pytest

class Results:
    def __init__(self):
        self.collected = 0
        self.outcomes = {"passed": 0, "failed": 0, "skipped": 0, "xfail": 0}
    def pytest_collection_finish(self, session):
        self.collected = len(session.items)
    def pytest_runtest_logreport(self, report):
        if hasattr(report, "wasxfail"):
            self.outcomes["xfail"] += 1
        if report.skipped:
            self.outcomes["skipped"] += 1
        elif report.failed:
            self.outcomes["failed"] += 1
        elif report.when == "call":
            self.outcomes["passed"] += 1

results = Results()
status = pytest.main([
    "-q", "-p", "no:cacheprovider", "--tb=short", "--junitxml=/logs/verifier/results.xml",
    "/tests/test_relational_merge.py", "/tests/upstream",
], plugins=[results])
passed = (
    status == 0 and results.collected > 0 and results.outcomes["passed"] == results.collected
    and not any(results.outcomes[k] for k in ("failed", "skipped", "xfail"))
)
Path("/logs/verifier/diagnostics.json").write_text(json.dumps({
    "exit_code": int(status), "collected": results.collected,
    "outcomes": results.outcomes, "reward": int(passed),
}, indent=2) + "\n")
Path("/logs/verifier/reward.txt").write_text(str(int(passed)) + "\n")
raise SystemExit(0 if passed else 1)
PY
