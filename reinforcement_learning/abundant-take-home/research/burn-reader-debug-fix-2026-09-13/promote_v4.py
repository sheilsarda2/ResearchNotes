"""Prepare or promote the validated replacement without modifying old trials.

Run inside the Harbor devcontainer. Preparation makes a paused, separate plan.
Application replaces only the retired campaign's policy cells under the shared
lock, publishes the user plan, then enables the new watchdog. It never signals
a trial worker. Interrupted applications leave their intent and snapshots for
explicit inspection; they are not automatically repeated.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from promotion_policy import OLD_TASK, NEW_TASK, replace_policy, replace_user_plan

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
JOBS = ROOT / 'jobs'
OLD = 'candidates-burn-reader-v3-efforts-20-20260913T232621Z'
SHARED = JOBS / 'candidate-campaigns-shared.control.json'
USER = JOBS / 'benchmark-user-plan.json'
FROZEN = BASE / 'frozen-task-manifest.json'
sys.path.insert(0, str(ROOT / 'scripts'))


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value, new=False):
    if new:
        with path.open('x') as output:
            output.write(json.dumps(value, indent=2) + '\n')
        return
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def supplied_files():
    output = subprocess.check_output(['git', 'ls-tree', '-rz', 'c1ae968', '--', '.'], cwd=ROOT)
    checked = {}
    for item in output.split(b'\0'):
        if not item:
            continue
        header, filename = item.decode().split('\t', 1)
        _, kind, expected = header.split()
        assert kind == 'blob'
        actual = subprocess.check_output(['git', 'hash-object', '--no-filters', '--', filename],
                                         cwd=ROOT, text=True).strip()
        assert actual == expected, f'Supplied file changed: {filename}'
        checked[filename] = actual
    assert len(checked) == 36
    return checked


def validate_frozen():
    candidate = module('promotion_candidate', 'candidate-bench.py')
    selection = read(FROZEN)
    assert selection['model_calls'] == 0 and len(selection['tasks']) == 1
    task = selection['tasks'][0]
    assert task['id'] == NEW_TASK
    candidate.verify_validation(task)
    controls = read(ROOT / selection['validation_summary'])
    assert controls['passed'] is True and controls['inputs_unchanged'] is True
    assert controls['tooling_unchanged'] is True
    assert digest(ROOT / selection['validation_summary']) == selection['validation_summary_sha256']
    spec = importlib.util.spec_from_file_location('promotion_controls', BASE / 'validate_controls_immutable.py')
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    identity_path = ROOT / controls['run_identity']
    assert digest(identity_path) == controls['run_identity_sha256'] == selection['run_identity_sha256']
    identity = read(identity_path)
    assert identity['inputs'] == checks.frozen_inputs()
    assert identity['tooling_sha256'] == checks.tooling_hashes()
    assert identity['original_budgets'] == checks.original_budgets()
    assert digest(identity_path.parent / 'control.json') == identity['control_sha256']
    assert digest(ROOT / selection['execution_source_capture']) == selection['execution_source_capture_sha256']
    for agent in ('oracle', 'nop'):
        record = controls['controls'][agent]
        path = ROOT / record['result']
        actual = checks.validate_result(path, agent, identity['task_checksum'], identity['tooling_sha256'])
        assert all(record[key] == value for key, value in actual.items())
        assert actual['trial_file_sha256'] == selection['validation_artifact_sha256'][agent]
    diagnostic = read(BASE / 'diagnostic002-summary.json')
    assert diagnostic['model_calls'] == 0 and diagnostic['passed'] is True
    assert diagnostic['hidden_pytorch_tests_compiled'] == 108
    assert diagnostic['targeted_assertions_exercised'] == 7
    assert diagnostic['no_debug_implementation_added'] is True
    assert diagnostic['task_assertions_unchanged_during_diagnostic'] is True
    assert diagnostic['historical_anticheat_zero_unchanged'] is True
    assert digest(BASE / 'saved-submission-diagnostic-002/result.json') == diagnostic['diagnostic_result_sha256']
    assert digest(BASE / 'diagnostic002-source-proof/run-001/result.json') == diagnostic['supplemental_helper_proof_sha256']
    for name, expected in diagnostic['diagnostic_file_sha256'].items():
        assert digest(BASE / 'saved-submission-diagnostic-002' / name) == expected
    return selection


def validate_refresh_fix():
    folder = ROOT / 'research/watchdog-interleaving-fix-2026-09-14'
    proof = read(folder / 'selection-refresh-proof.json')
    assert proof['passed'] is True and proof['model_calls'] == 0
    assert proof['test_count'] == 3
    assert all(proof['assertions'].values())
    for name, expected in proof['source_sha256'].items():
        assert digest(ROOT / name) == expected, 'Selection refresh changed since regression checks'
    assert digest(folder / 'selection-refresh-only.diff') == proof['patch_sha256']
    return digest(folder / 'selection-refresh-proof.json')


def validate_priority_restored():
    journal = BASE / 'validation-priority'
    if not journal.exists():
        return None
    intent = read(journal / 'intent.json')
    restoration = read(journal / 'restoration.json')
    assert restoration['passed'] is True, 'Validation priority restoration needs review'
    changes = {row['path']: row for row in intent['changes']}
    restored = {row['path']: row for row in restoration['controls']}
    assert len(changes) == len(intent['changes']) == 3
    assert len(restored) == len(restoration['controls']) == 3
    assert set(changes) == set(restored)
    for name, change in changes.items():
        row = restored[name]
        assert row['status'] in ('restored', 'already_original')
        assert row['current_sha256'] == change['original_sha256']
        assert digest(ROOT / name) == change['original_sha256'], 'Campaign control changed after restoration'
    return digest(journal / 'restoration.json')


def prepare(run_id):
    selection = validate_frozen()
    refresh_proof = validate_refresh_fix()
    priority_restoration = validate_priority_restored()
    new = f'candidates-burn-reader-v4-efforts-20-{run_id}'
    assert new not in read(USER)['active_revision_campaigns']
    assert not list(JOBS.glob(new + '*')), 'Never overwrite an existing cohort'
    manifest = JOBS / (new + '.tasks.json')
    manifest.write_bytes(FROZEN.read_bytes())
    control = read(JOBS / (OLD + '.control.json'))
    assert control['paused'] is True
    control.update(paused=True, pause_reason='Validated Burn v4 awaiting atomic promotion')
    dump(JOBS / (new + '.control.json'), control, new=True)
    command = [sys.executable, str(ROOT / 'scripts/run-candidate-screen.py'),
               '--run-id', run_id, '--attempts', '20', '--workers', '12',
               '--models', 'fable-5-1', 'opus-5', 'sonnet-5',
               '--campaign', 'candidates-burn-reader-v4', '--task-manifest', str(manifest), '--plan-only']
    subprocess.run(command, cwd=ROOT, check=True)
    plan = read(JOBS / (new + '.plan.json'))
    assert plan['target'] == 180 and plan['tasks'] == selection['tasks']
    paths = [JOBS / (new + suffix) for suffix in ('.tasks.json', '.control.json', '.plan.json', '.config.json')]
    dump(BASE / 'prepared-cohort.json', {'at': now(), 'old': OLD, 'new': new,
         'paths_sha256': {str(p.relative_to(ROOT)): digest(p) for p in paths},
         'target': 180, 'target_total': 2340, 'paused': True, 'model_calls': 0,
         'diagnostic_summary_sha256': digest(BASE / 'diagnostic002-summary.json'),
         'selection_refresh_proof_sha256': refresh_proof,
         'priority_restoration_sha256': priority_restoration}, new=True)
    print(json.dumps({'prepared': new, 'target': 180, 'paused': True}))


def apply():
    prepared = read(BASE / 'prepared-cohort.json')
    new = prepared['new']
    journal = BASE / 'promotion'
    assert not journal.exists(), 'Inspect an existing promotion; do not repeat it'
    selection = validate_frozen()
    assert validate_priority_restored() == prepared['priority_restoration_sha256']
    assert validate_refresh_fix() == prepared['selection_refresh_proof_sha256']
    assert digest(BASE / 'diagnostic002-summary.json') == prepared['diagnostic_summary_sha256']
    for name, expected in prepared['paths_sha256'].items():
        assert digest(ROOT / name) == expected
    originals = supplied_files()
    watcher = module('promotion_watcher', 'watch-candidate-campaign.py')
    from benchmark_recovery import job_containers
    from benchmark_shared_admission import process_identity
    old_plan = read(JOBS / (OLD + '.plan.json'))
    old_jobs = {j['name'] for j in old_plan['jobs']}
    assert old_jobs == {OLD}, 'Unexpected repair worker must be reviewed'
    assert not watcher.inventory(OLD, old_jobs)['runners']
    assert not watcher.inventory(new, {new})['runners']
    containers = job_containers(JOBS / OLD)
    assert not containers, 'Retired cohort still owns containers'
    inspected_at = time.time()
    history = {str(p.relative_to(ROOT)): digest(p) for p in (JOBS / OLD).glob('*/result.json')}
    assert len(history) == 4, 'Unexpected old trial population must be audited'
    journal.mkdir()
    dump(journal / 'intent.json', {'at': now(), 'old': OLD, 'new': new,
         'prepared_sha256': digest(BASE / 'prepared-cohort.json'),
         'promotion_script_sha256': digest(Path(__file__)),
         'policy_helper_sha256': digest(BASE / 'promotion_policy.py'),
         'old_results_sha256': history, 'supplied_files': originals,
         'empty_container_inventory_at': inspected_at,
         'trial_worker_signals': 0}, new=True)
    with SHARED.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert time.time() - inspected_at < 60, 'Repeat stale inventory before mutation'
        control = read(SHARED)
        assert control['max_active'] == 12
        assert read(JOBS / (OLD + '.control.json'))['paused'] is True
        assert read(JOBS / (new + '.control.json'))['paused'] is True
        assert not watcher.inventory(OLD, old_jobs)['runners']
        assert not watcher.inventory(new, {new})['runners']
        state_path = SHARED.with_suffix('.state.json')
        state = read(state_path)
        for participant in state['participants'].values():
            if Path(participant['control']).resolve() == JOBS / (OLD + '.control.json'):
                assert not participant['trials']
                assert process_identity(participant['pid']) != participant['identity']
        original_user_bytes = USER.read_bytes()
        user = json.loads(original_user_bytes)
        assert validate_refresh_fix() == prepared['selection_refresh_proof_sha256']
        assert state['interleaving'].get('excluded_tasks', []) == user.get('excluded_tasks', [])
        assert validate_priority_restored() == prepared['priority_restoration_sha256']
        updated_policy = replace_policy(state['interleaving'], OLD, new, now())
        updated_user = replace_user_plan(user, OLD, new, selection['tasks'][0], digest(FROZEN), now())
        dump(journal / 'shared-state-before.json', state, new=True)
        dump(journal / 'user-plan-before.json', user, new=True)
        assert USER.read_bytes() == original_user_bytes, 'Concurrent user plan edit'
        state['interleaving'] = updated_policy
        state['updated_at'] = time.time()
        dump(journal / 'shared-state-after.json', state, new=True)
        dump(journal / 'user-plan-after.json', updated_user, new=True)
        dump(state_path, state)
        dump(USER, updated_user)
        assert read(state_path) == state and read(USER) == updated_user
    # The new user plan makes the old watchdog ineligible before its supervisor
    # is stopped. All historical workers have already exited.
    stopped = []
    for info in watcher.inventory(OLD, old_jobs)['supervisors']:
        sent = watcher.signal_named(info, signal.SIGINT)
        stopped.append(dict(info, sigint_sent=sent))
    for info in stopped:
        deadline = time.monotonic() + 5
        while process_identity(info['pid']) == info['identity'] and time.monotonic() < deadline:
            time.sleep(0.1)
        assert process_identity(info['pid']) != info['identity'], 'Old supervisor has not exited'
    assert not watcher.inventory(OLD, old_jobs)['runners']
    assert all(digest(ROOT / name) == expected for name, expected in history.items())
    new_control_path = JOBS / (new + '.control.json')
    with SHARED.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        expected = prepared['paths_sha256'][str(new_control_path.relative_to(ROOT))]
        assert digest(new_control_path) == expected, 'New campaign control changed before activation'
        new_control = read(new_control_path)
        new_control.update(paused=False)
        new_control.pop('pause_reason', None)
        dump(new_control_path, new_control)
    command = [sys.executable, str(ROOT / 'scripts/watch-candidate-campaign.py'),
               '--campaign', new, '--apply', '--interval', '30']
    with (JOBS / (new + '.watchdog.log')).open('ab') as log:
        child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    assert supplied_files() == originals
    result = {'at': now(), 'old': OLD, 'new': new, 'passed': True,
              'old_supervisor_stops': stopped, 'trial_worker_signals': 0,
              'new_watchdog': {'pid': child.pid, 'identity': process_identity(child.pid)},
              'target_total': 2340, 'shared_cap': 12,
              'old_results_unchanged': True, 'supplied_files_unchanged': True,
              'new_control_sha256': digest(new_control_path)}
    dump(journal / 'applied.json', result, new=True)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'apply'])
    parser.add_argument('--run-id')
    args = parser.parse_args()
    if args.action == 'prepare':
        assert args.run_id and all(c.isalnum() or c == '-' for c in args.run_id)
        prepare(args.run_id)
    else:
        assert args.run_id is None
        apply()
