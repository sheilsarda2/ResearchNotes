"""Read-only terminal audit; writes only this new diagnosis.json."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(BASE / 'timing-regression-diagnosis-001'))
from read_only_observer import read_shared_pair


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_map(path):
    return {p.relative_to(path).as_posix(): sha(p) for p in sorted(path.rglob('*')) if p.is_file()}


def ref(path):
    return {'path': str(path.relative_to(ROOT)), 'sha256': sha(path)}


run = BASE / 'paired-regrades/run-001'
prior = BASE / 'continuation-confirmed-004'
case = run / '01-D3tzpaW'
before = {'paired_run': file_map(run), 'continuation': file_map(prior)}
result = json.loads((case / 'result.json').read_bytes())
summary = json.loads((run / 'summary.json').read_bytes())
continuation = json.loads((prior / 'summary.json').read_bytes())
inputs = json.loads((run / 'inputs.json').read_bytes())
record = inputs['records'][0]
assert len(summary['regrades']) == 1 and not summary['validation_passed']
assert result['status'] == 'error' and result['reward'] is None and result['groups'] == {}
assert result['error_type'] == 'ValueError' and result['error'].startswith('inspect-limits failed;')
assert result['cleanup'] == {'claim_absent': True, 'container_absent': True,
                            'errors': ['collect-output:1', 'collect-verifier:1'],
                            'stopped_before_collection': True}
commands = [(e['label'], e['returncode']) for e in result['lifecycle'] if e['event'] == 'command']
assert commands == [('create', 0), ('start', 0), ('inspect-limits', 1), ('stop', 0),
                    ('stopped', 0), ('collect-output', 1), ('collect-verifier', 1),
                    ('remove', 0), ('absence', 0)]
error = (case / 'artifacts/03-inspect-limits.log').read_text()
assert 'map has no entry for key "StorageOpt"' in error
assert continuation['finished_at'] and not continuation['passed']
assert continuation['error'] == 'paired-full-regrades failed; no later stage or retry is authorized'
assert continuation['stages']['paired-full-regrades']['exit_code'] == 1
assert not (case / 'artifacts/case/raw-result.json').exists()
assert file_map(Path(record['source'])) == record['source_files'] == file_map(case / 'source-capture')
identities = []
for pid, expected in [(60710, '6029740'), (45109, '6343448')]:
    proc = Path('/proc') / str(pid) / 'stat'
    observed = proc.read_text().rsplit(')', 1)[1].split()[19] if proc.exists() else None
    assert observed != expected, 'Exact prior process is still live'
    identities.append({'pid': pid, 'expected_start_ticks': expected,
                       'observed_start_ticks': observed, 'exact_identity_absent': True})
pair = read_shared_pair(ROOT / 'jobs/candidate-campaigns-shared.control.json')
own = pair['state']['participants'].get(result['admission_key'])
assert not own or not own['trials']
container = subprocess.run(['docker', 'ps', '-aq', '--filter', 'name=^/' + result['container_name'] + '$'],
                           text=True, capture_output=True, timeout=15)
assert container.returncode == 0 and not container.stdout.strip()
supplied = []
for name in subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', 'c1ae968', '.'], cwd=ROOT, text=True).splitlines():
    expected = hashlib.sha256(subprocess.check_output(['git', 'show', 'c1ae968:./' + name], cwd=ROOT)).hexdigest()
    actual = sha(ROOT / name)
    assert actual == expected, 'Supplied file changed: ' + name
    supplied.append({'path': name, 'sha256': actual})
assert len(supplied) == 36
after = {'paired_run': file_map(run), 'continuation': file_map(prior)}
assert before == after
out = {
    'schema_version': 1, 'kind': 'paired_optional_storage_inspection_failure_diagnosis',
    'at': datetime.now(timezone.utc).isoformat(), 'passed': True, 'model_calls': 0,
    'new_control_runs': 0, 'new_verifier_runs': 0, 'counted_sweep_trials': 0,
    'cause': 'Docker omitted optional HostConfig.StorageOpt; the strict Go dot lookup aborted setup.',
    'repair': 'Use Go index only for StorageOpt. Preserve required-field extraction and all exact limit/quota checks.',
    'failed_continuation': ref(prior / 'summary.json'), 'failed_paired_summary': ref(run / 'summary.json'),
    'failed_case_result': ref(case / 'result.json'), 'failed_inspection_log': ref(case / 'artifacts/03-inspect-limits.log'),
    'exact_commands': commands, 'source_was_copied_to_container': False, 'verifier_started': False,
    'original_source_unchanged': True, 'staged_source_unchanged': True,
    'original_reward': result['original_reward'], 'paired_reward': None, 'groups': {},
    'historical_cleanup': result['cleanup'], 'processes': identities,
    'fresh_terminal_absence': {'at': pair['read_at'], 'admission_key': result['admission_key'],
                              'own_participant': own, 'claim_absent': True,
                              'container_name': result['container_name'], 'container_absent': True,
                              'docker_exit_code': container.returncode,
                              'docker_stdout': container.stdout, 'docker_stderr': container.stderr,
                              'shared_state_sha256': pair['shared_state_sha256'],
                              'shared_control_sha256': pair['shared_control_sha256'],
                              'read_attempts': pair['attempts'], 'recovered_gaps': pair['recovered_gaps']},
    'preserved_trees': {'paired_run': str(run.relative_to(ROOT)), 'continuation': str(prior.relative_to(ROOT))},
    'file_sha256_before': before, 'file_sha256_after': after,
    'supplied_files': supplied, 'supplied_files_unchanged': True,
    'generator': ref(Path(__file__)),
    'read_only_observer': ref(BASE / 'timing-regression-diagnosis-001/read_only_observer.py')
}
target = HERE / 'diagnosis.json'
with target.open('x') as stream:
    stream.write(json.dumps(out, indent=2, sort_keys=True) + '\n')
print(json.dumps({'path': str(target.relative_to(ROOT)), 'sha256': sha(target), 'passed': True}))
