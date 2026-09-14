#!/usr/bin/env bash
# Verifier for py-rosbags-rosbag2-storage-writers (Harbor separate verifier, offline).
#
# Harbor re-materializes the [[artifacts]] entry at its original source path, so the
# agent's src/rosbags/ already sits at $REPO/src/rosbags when this runs. Everything
# else the verifier executes (upstream tests, hidden tests, fixtures, mcap CLI) comes
# from the verifier image. All paths are overridable so the script can be dry-run
# outside the container.
set -uo pipefail

REPO=${VERIFIER_REPO:-/workspace/repo}
PRISTINE=${VERIFIER_PRISTINE:-/pristine}
HIDDEN=${VERIFIER_HIDDEN:-/tests/hidden}
LOGS=${VERIFIER_LOGS:-/logs/verifier}
PY=${VERIFIER_PYTHON:-python}
SUBMISSION=${VERIFIER_SUBMISSION:-$REPO/src/rosbags}
export ROSBAG2_FIXTURES=${ROSBAG2_FIXTURES:-/tests/fixtures}
export MCAP_CLI=${MCAP_CLI:-/usr/local/bin/mcap}

mkdir -p "$LOGS"
printf '0\n' > "$LOGS/reward.txt"

# 1. Clean-room overlay: the submission replaces src/rosbags wholesale; upstream tests are
#    restored from the pristine copy shipped in the verifier image.
if [ "$SUBMISSION" != "$REPO/src/rosbags" ]; then
  rm -rf "$REPO/src/rosbags"
  mkdir -p "$REPO/src"
  cp -a "$SUBMISSION" "$REPO/src/rosbags"
fi
rm -rf "$REPO/tests"
cp -a "$PRISTINE/tests" "$REPO/tests"
find "$REPO/src" "$REPO/tests" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
find "$REPO/src" -name '*.pyc' -delete 2>/dev/null

cd "$REPO" || exit 1

"$PY" - "$REPO" "$HIDDEN" "$LOGS" <<'PY'
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

repo, hidden, logs = (Path(a) for a in sys.argv[1:4])
src = repo / 'src' / 'rosbags'
score: dict = {'groups': {}, 'anticheat': {}, 'build': {}}


def run(cmd, timeout=1800, env=None, cwd=None):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    return proc.returncode, proc.stdout + proc.stderr


# ---------------------------------------------------------------- anti-cheat
FORBIDDEN = [
    (r'_pytest', 'pytest internals'),
    (r'\bpytest\b', 'pytest'),
    (r'\batexit\b', 'atexit'),
    (r'/logs\b', 'verifier log path'),
    (r'reward\.txt|score\.json', 'reward files'),
    (r'\bsubprocess\b', 'subprocess'),
    (r'^\s*(import|from)\s+mcap\b', 'mcap-python import'),
    (r'\bmcap\s+doctor\b', 'mcap CLI'),
]
hits = []
for path in sorted(src.rglob('*')):
    if path.is_dir():
        continue
    rel = path.relative_to(repo).as_posix()
    if path.name == 'conftest.py' or path.name.startswith('test_') or path.suffix == '.pth':
        hits.append(f'{rel}: forbidden file')
        continue
    if path.suffix not in {'.py', '.typed', '.pyi'}:
        hits.append(f'{rel}: unexpected file type')
        continue
    if path.suffix == '.py':
        text = path.read_text(errors='replace')
        for pattern, label in FORBIDDEN:
            for m in re.finditer(pattern, text, flags=re.MULTILINE):
                line = text.count('\n', 0, m.start()) + 1
                hits.append(f'{rel}:{line}: {label}')
score['anticheat'] = {'hits': hits, 'passed': not hits}

# ---------------------------------------------------------------- build / import
rc, out = run([sys.executable, '-m', 'compileall', '-q', str(src)])
score['build']['compileall'] = rc == 0
rc2, out2 = run([sys.executable, '-c', (
    'import rosbags.rosbag2, rosbags.highlevel, rosbags.convert; '
    'from rosbags.rosbag2.storage_mcap import McapWriter, McapReader; '
    'from rosbags.rosbag2.storage_sqlite3 import Sqlite3Writer, Sqlite3Reader; '
    'import rosbags.rosbag2, pathlib; '  # rosbags is a namespace package: check a real module
    f'assert pathlib.Path(rosbags.rosbag2.__file__).resolve().parent.parent == pathlib.Path({str(src)!r}).resolve(), rosbags.rosbag2.__file__'
)])
score['build']['import'] = rc2 == 0
score['build']['log'] = (out + out2)[-4000:]
score['build']['passed'] = score['build']['compileall'] and score['build']['import']

# ---------------------------------------------------------------- pytest groups
FEATURE_NODES = [
    'tests/rosbags/rosbag2/test_writer.py',
    'tests/rosbags/rosbag2/test_roundtrip.py',
    'tests/rosbags/rosbag2/test_storage_mcap.py::test_write_empty',
    'tests/rosbags/rosbag2/test_storage_mcap.py::test_write_schema',
    'tests/rosbags/rosbag2/test_storage_mcap.py::test_write_channel',
    'tests/rosbags/rosbag2/test_storage_mcap.py::test_write_message',
    'tests/rosbags/rosbag2/test_storage_mcap.py::test_write_multichunk',
]
REGRESSION_ARGS = [
    'tests',
    '--ignore=tests/rosbags/rosbag2/test_writer.py',
    '--ignore=tests/rosbags/rosbag2/test_roundtrip.py',
    *[f'--deselect={n}' for n in FEATURE_NODES[2:]],
]
GROUPS = [
    # name, expected collected, pytest args, cwd
    ('upstream_feature', 12, FEATURE_NODES, repo),
    ('upstream_regression', 134, REGRESSION_ARGS, repo),
    ('hidden_mcap_differential', 47, [str(hidden / 'test_mcap_storage_writer.py')], hidden),
    ('hidden_sqlite3_differential', 22, [str(hidden / 'test_sqlite3_storage_writer.py')], hidden),
    ('fixture_read_regression', 8, [str(hidden / 'test_rosbag2_fixtures_read.py')], hidden),
]

env = dict(os.environ)
env['PYTHONDONTWRITEBYTECODE'] = '1'
for name, expected, args, cwd in GROUPS:
    junit = logs / f'{name}.xml'
    cmd = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-o', 'addopts=',
           '--tb=short', '-rfEs', f'--junitxml={junit}', *args]
    try:
        rc, out = run(cmd, timeout=1500, env=env, cwd=str(cwd))
    except subprocess.TimeoutExpired as exc:
        rc, out = 124, f'timeout: {exc}'
    (logs / f'{name}.log').write_text(out)
    collected = passed = failed = errors = skipped = 0
    if junit.exists():
        for suite in ET.parse(junit).getroot().iter('testsuite'):
            collected += int(suite.get('tests', 0))
            failed += int(suite.get('failures', 0))
            errors += int(suite.get('errors', 0))
            skipped += int(suite.get('skipped', 0))
        passed = collected - failed - errors - skipped
    ok = rc == 0 and collected == expected and passed == expected and not (failed or errors or skipped)
    score['groups'][name] = {
        'expected': expected, 'collected': collected, 'passed': passed, 'failed': failed,
        'errors': errors, 'skipped': skipped, 'exit_code': rc, 'passed_group': ok,
        'fraction': (passed / expected) if expected else 0.0,
    }
    print(f'[{name}] collected={collected}/{expected} passed={passed} failed={failed} errors={errors} skipped={skipped} rc={rc}')

reward = int(score['anticheat']['passed'] and score['build']['passed'] and all(g['passed_group'] for g in score['groups'].values()))
score['reward'] = reward
(logs / 'score.json').write_text(json.dumps(score, indent=2) + '\n')
(logs / 'reward.txt').write_text(f'{reward}\n')
print('anticheat:', 'ok' if score['anticheat']['passed'] else hits)
print('build:', score['build']['passed'])
print('reward:', reward)
raise SystemExit(0 if reward else 1)
PY
