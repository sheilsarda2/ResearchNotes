#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
cd /workspace/repo
export PYTHONPATH=/workspace/repo:/workspace/repo/test:/tests
python -m pytest -o addopts= -q /tests/test_checkpoint_task.py \
  test/task_test.py::TaskTest::test_task_to_str_to_task \
  test/task_test.py::TaskTest::test_task_from_str_insignificant \
  test/task_test.py::TaskTest::test_getpaths \
  test/task_test.py::TaskTest::test_flatten \
  test/worker_test.py::WorkerTest::test_cache_task_completion_config \
  test/worker_test.py::DynamicDependenciesTest::test_dynamic_dependencies \
  test/worker_test.py::DynamicDependenciesTest::test_dynamic_dependencies_other_module \
  test/worker_test.py::DynamicDependenciesTest::test_wrapped_dynamic_requirements \
  --junitxml=/logs/verifier/results.xml -ra
status=$?
python - "$status" <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
passed = False
try:
    suites = list(ET.parse('/logs/verifier/results.xml').getroot().iter('testsuite'))
    total = sum(int(s.get('tests', 0)) for s in suites)
    bad = sum(int(s.get(k, 0)) for s in suites for k in ('failures', 'errors', 'skipped'))
    passed = sys.argv[1] == '0' and total >= 40 and bad == 0
except Exception:
    pass
Path('/logs/verifier/reward.txt').write_text('1\n' if passed else '0\n')
PY
exit 0
