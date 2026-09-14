#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
cd /workspace/repo
python -m pytest -o addopts= -q /tests/test_snapshots.py tests/test_core.py tests/test_fanout.py --junitxml=/logs/verifier/results.xml > /logs/verifier/pytest.log 2>&1
status=$?
cat /logs/verifier/pytest.log
python - "$status" <<'PY'
import json, pathlib, sys, xml.etree.ElementTree as ET
root = pathlib.Path('/logs/verifier')
try:
    cases = list(ET.parse(root/'results.xml').iter('testcase'))
    counts = {'tests':len(cases), 'failed':sum(c.find('failure') is not None for c in cases),
              'errors':sum(c.find('error') is not None for c in cases),
              'skipped':sum(c.find('skipped') is not None for c in cases)}
    passed = int(sys.argv[1]) == 0 and counts['tests'] > 0 and not any(counts[k] for k in ('failed','errors','skipped'))
except Exception as error:
    counts = {'parse_error':str(error)}
    passed = False
(root/'summary.json').write_text(json.dumps({'passed':passed, **counts}, indent=2))
(root/'reward.txt').write_text('1\n' if passed else '0\n')
PY
