"""Close only the pre-registered, identity-checked Huey agent guard phase."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path.cwd()
OUT = ROOT / 'research/mini-tool-drain-fix-2026-09-13'
REGISTRY = ROOT / 'research/benchmark-interventions/2026-09-13-huey-mini-pipe-drain/intervention.json'
TRIAL = 'jobs/candidates-all14-efforts-20-20260913T183301Z/huey-sqlite-leases__LAUZTSQ'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound(entry):
    path = (ROOT / entry['path']).resolve()
    assert path.is_relative_to(ROOT / 'research') and digest(path) == entry['sha256']
    return json.loads(path.read_text())


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_trial_intervention import validate_registration
entry, _, _ = validate_registration(REGISTRY, root=ROOT)
assert entry['kind'] == 'mini_tool_pipe_drain_intervention'
assert entry['status'] == 'planned' and entry['trial'] == TRIAL
observed, proof = bound(entry['observation']), bound(entry['proof'])
assert proof['passed'] is True and proof['model_calls'] == 0 and proof['tests_run'] == 9
assert observed['trial'] == TRIAL and observed['stalled_for_sec'] >= 138
assert observed['tool_timeout_sec'] == 30 and observed['recorded_turns'] == 39
assert observed['pid_namespace'] == 'trial_container'
for name, expected in entry['identity']['files'].items():
    assert digest(ROOT / TRIAL / name) == expected == observed['files'][name]
assert not (ROOT / TRIAL / 'result.json').exists()
container = observed['container']
mounts = json.loads(subprocess.check_output(
    ['docker', 'inspect', '--format', '{{json .Mounts}}', container], text=True))
assert any(m['Source'] == str(ROOT / TRIAL / 'agent') for m in mounts)
runtime = json.loads((ROOT / TRIAL / 'benchmark-runtime.json').read_text())
payload = dict(observed, guard_sha256=runtime['process_guard_sha256'], apply=args.apply)
code = r'''
import hashlib, json, os, subprocess, sys
from pathlib import Path
observation = json.loads(sys.argv[1])
for name in ('guard', 'agent'):
    expected = observation[name]
    path = Path('/proc') / str(expected['pid'])
    fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
    assert fields[0] != 'Z' and fields[19] == expected['start_ticks']
assert os.readlink('/proc/%d/fd/4' % observation['agent']['pid']) == 'pipe:[3938292]'
phase = Path(observation['guard']['phase'])
assert phase.parent == Path('/tmp') and phase.name.startswith('benchmark-guard-')
assert not (phase / 'closed').exists()
guard = phase / 'guard.py'
assert hashlib.sha256(guard.read_bytes()).hexdigest() == observation['guard_sha256']
records = [json.loads(path.read_text()) for path in phase.glob('*.json')]
active = [record for record in records if not record.get('quiescent')]
assert len(active) == 1
assert active[0]['pid'] == observation['guard']['pid']
assert active[0]['identity'] == observation['guard']['start_ticks']
if not observation['apply']:
    print(json.dumps({'ready': True, 'guard': observation['guard'], 'action': 'guard_phase_close'}))
else:
    result = subprocess.run([sys.executable, str(guard), 'close', '--phase', str(phase)],
                            text=True, capture_output=True, timeout=25, check=True)
    data = json.loads(result.stdout)
    assert data['quiescent'] is True
    matching = [r for r in data['executions'] if r['pid'] == observation['guard']['pid']
                and r['identity'] == observation['guard']['start_ticks']]
    assert len(matching) == 1 and matching[0]['reason'] == 'cancelled'
    assert matching[0]['return_code'] == 124 and matching[0]['quiescent'] is True
    print(json.dumps(data))
'''
if args.apply:
    with (OUT / 'guard-close-intent.json').open('x') as output:
        json.dump({'started_at': datetime.now(timezone.utc).isoformat(),
                   'registry_sha256': digest(REGISTRY), 'trial': TRIAL}, output, indent=2)
result = subprocess.run(['docker', 'exec', container, 'python3', '-c', code,
                         json.dumps(payload)], capture_output=True, text=True, timeout=35)
record = {'observed_at': datetime.now(timezone.utc).isoformat(), 'apply': args.apply,
          'return_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr,
          'trial': TRIAL, 'registry_sha256': digest(REGISTRY)}
name = 'guard-close-result.json' if args.apply else 'guard-close-preflight.json'
with (OUT / name).open('x' if args.apply else 'w') as output:
    json.dump(record, output, indent=2)
    output.write('\n')
assert result.returncode == 0, 'Guard-close check failed; inspect the recorded result before any retry'
print(json.dumps({'record': str((OUT / name).relative_to(ROOT)), 'apply': args.apply,
                  'return_code': result.returncode}))
