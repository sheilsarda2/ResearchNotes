#!/bin/bash
set -uo pipefail
cd /workspace/repo
# Restore regressions nested inside the submitted runtime package.
rm -rf huey/tests
cp -a /opt/pristine-huey-tests huey/tests || exit 1
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
export HUEY_SLOW_TESTS=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
unset PYTEST_ADDOPTS
python -m pytest -q -c /dev/null --confcutdir=/tests \
    /tests/test_outbox.py \
    huey/tests/test_api.py huey/tests/test_registry.py \
    huey/tests/test_serializer.py huey/tests/test_immediate.py \
    huey/tests/test_storage.py::TestSqliteStorage \
    huey/tests/test_consumer.py::TestConsumerIntegration \
    huey/tests/test_consumer.py::TestConsumerConfig \
    --junitxml=/logs/verifier/results.xml \
    > /logs/verifier/pytest.log 2>&1
pytest_status=$?
cat /logs/verifier/pytest.log
python - "$pytest_status" <<'PY'
import json
import pathlib
import sys
import xml.etree.ElementTree as ET
logs = pathlib.Path('/logs/verifier')
status = int(sys.argv[1])
try:
    tree = ET.parse(logs/'results.xml')
    cases = tree.findall('.//testcase')
    failures = sum(case.find('failure') is not None for case in cases)
    errors = sum(case.find('error') is not None for case in cases)
    skipped = sum(case.find('skipped') is not None for case in cases)
    passed = status == 0 and bool(cases) and not (failures or errors or skipped)
    diagnostics = dict(exit_code=status, collected=len(cases), failures=failures,
                       errors=errors, skipped=skipped, reward=int(passed))
except Exception as exc:
    passed = False
    diagnostics = dict(exit_code=status, reward=0, verifier_error=repr(exc))
(logs/'diagnostics.json').write_text(json.dumps(diagnostics, indent=2)+'\n')
(logs/'reward.txt').write_text('1\n' if passed else '0\n')
print(json.dumps(diagnostics))
PY
