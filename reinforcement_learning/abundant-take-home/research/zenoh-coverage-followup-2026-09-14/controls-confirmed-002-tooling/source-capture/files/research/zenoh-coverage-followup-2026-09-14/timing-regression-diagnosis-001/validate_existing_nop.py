"""Independently validate an existing normal nop; never launch or rewrite a run."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(BASE))
import validate_controls_immutable as original
import read_only_observer as observer


def ref(path):
    return {'path': str(path.relative_to(ROOT)), 'sha256': original.digest(path)}


def process_identity(pid):
    path = Path('/proc') / str(pid) / 'stat'
    try:
        return path.read_text().rsplit(') ', 1)[1].split()[19]
    except FileNotFoundError:
        return None


def main():
    old = BASE / 'harbor-controls-final'
    output = BASE / 'nop-independent-validation-001'
    assert not output.exists(), 'Never overwrite independent validation'
    before = original.hashes(old)
    summary_path = old / 'summary.json'
    summary = original.read(summary_path)
    identity_path = ROOT / summary['run_identity']
    identity = original.read(identity_path)
    assert original.digest(identity_path) == summary['run_identity_sha256']
    assert summary['finished_at'] and summary['passed'] is False
    assert summary['inputs_unchanged'] is summary['tooling_unchanged'] is True
    assert identity['inputs'] == original.frozen_inputs()
    assert identity['tooling_sha256'] == original.tooling_hashes()
    row = summary['controls']['nop']
    assert row['passed'] is False and row['exit_code'] == 0
    assert row['error_type'] == 'AssertionError' and row['error'] == 'Admission observation failed; child was reaped before continuing'
    expected_error = "[Errno 2] No such file or directory: '" + original.admission_control()['shared_pool'].replace('.json', '.state.json') + "'"
    assert row['observation_errors'] == [{'error_type': 'FileNotFoundError', 'error': expected_error}]
    results = list((old / 'jobs' / row['job']).glob('*/result.json'))
    assert len(results) == 1
    result_path = results[0]
    raw = original.read(result_path)
    verified = original.validate_result(result_path, 'nop', identity['task_checksum'], identity['tooling_sha256'])
    admission_path = old / 'nop-admission-observations.jsonl'
    admission = original.validate_admission(admission_path, old / 'control.json')
    samples = [json.loads(line) for line in admission_path.read_text().splitlines()]
    owners = {(key, p['pid'], p['identity']) for s in samples for key, p in s['own_participants'].items() if p['trials']}
    assert len(owners) == 1
    key, pid, ticks = next(iter(owners))
    assert key == str(pid) + ':' + ticks and all(s['runner_pid'] == pid for s in samples)
    assert all(set(p['trials']) == {raw['trial_name']} for s in samples for p in s['own_participants'].values() if p['trials'])
    assert original.epoch(samples[-1]['at']) > original.epoch(raw['finished_at'])
    actual_ticks = process_identity(pid)
    assert actual_ticks != ticks, 'Original runner is still alive'
    output.mkdir()
    terminal = observer.observe_once(output / 'terminal-observation.jsonl', old / 'control.json', pid,
        shared_control=Path(original.admission_control()['shared_pool']), gap_path=output / 'recovered-read-gaps.jsonl', terminal=True)
    assert terminal['terminal_sample'] is True and terminal['own_participants'] == {}
    assert terminal['shared_max_active'] == 14 and terminal['total_claims'] <= 14
    command = ['docker', 'ps', '--all', '--no-trunc', '--format', '{{json .}}']
    docker = subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
    containers = [json.loads(line) for line in docker.stdout.splitlines()]
    name = raw['trial_name'].lower()
    matches = [c for c in containers if name in (c.get('Names', '') + ' ' + c.get('Labels', '')).lower()]
    assert matches == [], 'An original trial container still exists'
    proof = {'kind': 'fresh_locked_terminal_absence', 'at': original.now(),
        'shared_observation': ref(output / 'terminal-observation.jsonl'), 'terminal_sample': terminal,
        'historical_runner': {'pid': pid, 'start_ticks': ticks, 'participant_key': key},
        'observed_runner_start_ticks': actual_ticks, 'original_runner_absent': True,
        'container_inventory_command': command, 'container_inventory_sha256': hashlib.sha256(docker.stdout.encode()).hexdigest(),
        'trial_name_match': name, 'matching_containers': matches, 'container_absent': True, 'claim_absent': True}
    original.dump(output / 'terminal-absence.json', proof)
    selected = {k: row[k] for k in ('job', 'command', 'exit_code', 'wall_seconds')}
    selected.update(verified)
    selected['admission_lifecycle'] = admission
    selected['validation_kind'] = 'independent_revalidation_of_existing_normal_nop'
    selected['terminal_absence'] = ref(output / 'terminal-absence.json')
    selected['original_observation_errors'] = row['observation_errors']
    source_run = {'summary': ref(summary_path), 'run_identity': ref(identity_path)}
    sources = dict(identity['tooling_sha256'])
    for path in (Path(__file__).resolve(), HERE / 'read_only_observer.py'):
        sources[str(path.relative_to(ROOT))] = original.digest(path)
    new_identity = {'schema_version': 1, 'kind': 'independent_original_nop_revalidation',
        'created_at': original.now(), 'task': identity['task'], 'task_checksum': identity['task_checksum'],
        'harbor_version': identity['harbor_version'], 'inputs': identity['inputs'],
        'original_budgets': identity['original_budgets'], 'tooling_sha256': sources,
        'source_run': source_run, 'model_calls': 0, 'new_control_runs': 0}
    assert original.hashes(old) == before, 'Original raw evidence changed during independent validation'
    original.dump(output / 'run-identity.json', new_identity)
    certificate = {'schema_version': 1, 'kind': 'independent_original_nop_validation',
        'execution_kind': 'independent_revalidation_of_existing_normal_nop', 'passed': True,
        'finished_at': original.now(), 'new_control_runs': 0, 'model_calls': 0,
        'inputs_unchanged': original.frozen_inputs() == identity['inputs'], 'tooling_unchanged': original.tooling_hashes() == identity['tooling_sha256'],
        'run_identity': str((output / 'run-identity.json').relative_to(ROOT)), 'run_identity_sha256': original.digest(output / 'run-identity.json'),
        'source_run': source_run, 'original_nop_record': row, 'original_observation_errors': row['observation_errors'],
        'original_run_file_sha256_before': before, 'original_run_file_sha256_after': original.hashes(old),
        'terminal_absence': ref(output / 'terminal-absence.json'), 'controls': {'nop': selected},
        'interpretation': 'The normal nop completed with expected zero reward and no exception. Original observer-only failure is preserved. Independent raw/source/admission validation and fresh locked terminal absence establish validity without another execution; missing observation instants remain disclosed.'}
    assert certificate['inputs_unchanged'] and certificate['tooling_unchanged']
    original.dump(output / 'summary.json', certificate)
    print(json.dumps({'certificate': ref(output / 'summary.json'), 'passed': True, 'new_control_runs': 0, 'admission_samples': admission['sample_count']}))


if __name__ == '__main__':
    main()
