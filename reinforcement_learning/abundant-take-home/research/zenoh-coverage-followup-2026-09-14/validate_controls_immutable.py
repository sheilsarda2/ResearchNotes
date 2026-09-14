#!/usr/bin/env python3
"""Run standard Harbor controls against the separate Zenoh follow-up revision.

Both controls use captured, byte-identical standard runtime sources, unchanged
task budgets and the same shared admission pool. Live source edits are recorded
separately and cannot change the sources this run executes.
--check-only performs file checks and prints the plan without starting Harbor.
"""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import sys
sys.dont_write_bytecode = True
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
TASK = BASE / 'rs-zenoh-timestamp-instrumentation-v4-validation'
CAPTURE = BASE / 'immutable-tooling-v1'
GROUPS = ['anti_cheat', 'build_a', 'build_b', 'lib_test_names', 'hidden_pr_tests',
          'robustness_tests', 'admin_timestamp_tests', 'interop_gold', 'parity_python',
          'zenoh_ext', 'upstream_regressions']
COUNTS = {'hidden_pr_tests': {'timestamp_instrumentation': 47},
          'robustness_tests': {'timestamp_robustness': 5},
          'admin_timestamp_tests': {'timestamp_adminspace': 1, 'timestamp_adminspace_reply_stack': 1},
          'interop_gold': {'ts_interop': 6}, 'parity_python': {'ts_interop': 4}}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def hashes(folder):
    result = {}
    for path in sorted(folder.rglob('*')):
        assert not path.is_symlink(), 'Proof directories must not contain symlinks'
        if path.is_file():
            result[str(path.relative_to(folder))] = digest(path)
    return result


def frozen_inputs():
    manifest = read(BASE / 'task-manifest.json')
    assert manifest['task'] == str(TASK.relative_to(ROOT))
    actual = hashes(TASK)
    assert actual == manifest['task_file_sha256'], 'Task bytes changed'
    return {'task_file_sha256': actual,
            'task_manifest_sha256': digest(BASE / 'task-manifest.json')}


def tooling_hashes():
    manifest = read(CAPTURE / 'manifest.json')
    tree = CAPTURE / 'files'
    assert (tree / 'research').is_symlink()
    assert (tree / 'research').resolve() == ROOT / 'research'
    result = dict(manifest['source_sha256'])
    actual = {str(path.relative_to(tree)): digest(path)
              for path in sorted((tree / 'scripts').rglob('*')) if path.is_file()}
    assert actual == result, 'Captured execution source tree changed'
    assert not any(path.is_symlink() for path in (tree / 'scripts').rglob('*'))
    for name in ('validate_controls_immutable.py', 'execute_captured_runner.py',
                 'capture_immutable_tooling.py', 'pin_control_verifier_image.py'):
        result[str((BASE / name).relative_to(ROOT))] = digest(BASE / name)
    result[str((CAPTURE / 'manifest.json').relative_to(ROOT))] = digest(CAPTURE / 'manifest.json')
    result[str((BASE / 'immutable-execution-context.json').relative_to(ROOT))] = digest(BASE / 'immutable-execution-context.json')
    return result


def live_source_differences():
    manifest = read(CAPTURE / 'manifest.json')
    return {name: {'captured': expected,
                   'live': digest(ROOT / name) if (ROOT / name).is_file() else None}
            for name, expected in manifest['source_sha256'].items()
            if not (ROOT / name).is_file() or digest(ROOT / name) != expected}


def original_budgets():
    import tomllib
    task = tomllib.loads((TASK / 'task.toml').read_text())
    assert task['agent']['timeout_sec'] == 21600
    assert task['verifier']['timeout_sec'] == 3600
    assert task['environment']['cpus'] == 4 and task['environment']['memory_mb'] == 8192
    assert task['environment']['storage_mb'] == 30720
    assert task['verifier']['environment']['network_mode'] == 'no-network'
    return {name: task[name] for name in ('agent', 'verifier', 'environment')}


def admission_control():
    return {'max_active': 1, 'min_total_mb': 30000, 'reserve_mb': 4096,
            'startup_reserve_mb': 1536, 'startup_window_sec': 120,
            'start_interval_sec': 5, 'max_memory_pressure_pct': 1.0,
            'pressure_cooldown_sec': 60, 'network_pool_cidr': '172.31.0.0/16',
            'paused': False,
            'shared_pool': str(ROOT / 'jobs/candidate-campaigns-shared.control.json')}


def command(agent, jobs, stamp):
    assert agent in ('oracle', 'nop')
    name = f'{TASK.name}-{agent}-{stamp}'
    return name, [sys.executable, str(BASE / 'execute_captured_runner.py'),
                  '--proof', str(jobs.parent / (agent + '-runner-source-proof.json')), '--',
                  'run', '-p', str(TASK), '-a', agent, '-y', '-n', '1',
                  '-o', str(jobs), '--job-name', name]


def epoch(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    assert parsed.tzinfo is not None
    return parsed.timestamp()


def validate_result(path, agent, checksum, tools):
    result = read(path)
    assert result['finished_at'] and result['exception_info'] is None, 'Control did not finish normally'
    assert result['task_name'] == TASK.name and result['task_checksum'] == checksum
    config = result['config']
    assert config == read(path.parent / 'config.json')
    assert Path(config['task']['path']).resolve() == TASK.resolve()
    assert config['agent']['name'] == agent and config['agent']['model_name'] is None
    assert result['agent_info']['name'] == agent and result['agent_info'].get('model_info') is None
    assert config['timeout_multiplier'] == 1 and not config.get('extra_instruction_paths')
    assert all(value is None for key, value in config.items() if key.endswith('_timeout_multiplier'))
    for section in ('agent', 'verifier'):
        assert not config[section].get('disable')
        for key in ('override_timeout_sec', 'max_timeout_sec', 'override_setup_timeout_sec'):
            assert config[section].get(key) is None, 'Control changed a task time budget'
    assert all(value is None for key, value in config['environment'].items() if key.startswith('override_'))
    usage = result.get('agent_result') or {}
    assert all(usage.get(key) in (None, 0) for key in ('n_input_tokens', 'n_output_tokens', 'cost_usd'))
    expected_reward = 1 if agent == 'oracle' else 0
    assert result['verifier_result']['rewards']['reward'] == expected_reward
    score = read(path.parent / 'verifier/score.json')
    assert score['reward'] == expected_reward and set(score['groups']) == set(GROUPS)
    assert score['required_groups'] == GROUPS and score['missing_groups'] == []
    if agent == 'oracle':
        assert all(score['groups'][group]['pass'] is True for group in GROUPS)
        for group, expected_bins in COUNTS.items():
            details = read(path.parent / 'verifier' / (group + '.json'))
            assert details['problems'] == [], f'{group}: grader found problems'
            for name, expected in expected_bins.items():
                row = details['binaries'][name]
                assert row['passed'] == expected and row['failed'] == row['ignored'] == 0
                assert row['status'] == 'ok' and len(details['tests'][name]) == expected
                assert all(status == 'ok' for status in details['tests'][name].values())
    else:
        assert any(score['groups'][group]['pass'] is False for group in GROUPS)
    assert float((path.parent / 'verifier/reward.txt').read_text().strip()) == expected_reward
    deadline = read(path.parent / 'benchmark-deadline.json')
    assert deadline['quiescent'] is True and not deadline.get('cleanup_error')
    assert deadline['helper_sha256'] == tools['scripts/benchmark_process_guard.py']
    assert isinstance(deadline['executions'], list)
    assert all(e['quiescent'] is True for e in deadline['executions'])
    if agent == 'oracle':
        assert deadline['executions'], 'Oracle did not execute its reference solution'
    assert deadline['cleanup_finished_at_epoch'] <= epoch(result['verifier']['started_at'])
    runtime = read(path.parent / 'benchmark-runtime.json')
    assert runtime['runtime_sha256'] == tools['scripts/benchmark_deadline.py']
    assert runtime['process_guard_sha256'] == tools['scripts/benchmark_process_guard.py']
    source_proof_path = path.parents[3] / (agent + '-runner-source-proof.json')
    source_proof = read(source_proof_path)
    assert source_proof['inspection_only'] is False
    assert source_proof['all_local_imports_captured'] is True
    assert source_proof['captured_tree_unchanged'] is True
    assert source_proof['repository_root_overrides'] is False
    assert source_proof['runner_sha256'] == tools['scripts/harbor-resource-runner.py']
    assert source_proof['capture_manifest_sha256'] == tools[str((CAPTURE / 'manifest.json').relative_to(ROOT))]
    assert source_proof['cwd'] == str(ROOT)
    assert source_proof['loaded_local_modules']
    assert source_proof['installed_harbor'] == read(BASE / 'immutable-execution-context.json')['installed_harbor']
    return {'runner_source_proof': str(source_proof_path.relative_to(ROOT)),
            'runner_source_proof_sha256': digest(source_proof_path),
            'result': str(path.relative_to(ROOT)), 'result_sha256': digest(path),
            'reward': expected_reward, 'exception': None, 'task_checksum': checksum,
            'agent_info': result['agent_info'], 'groups': score['groups'],
            'timings': {key: result.get(key) for key in ('environment_setup', 'agent_execution', 'verifier')},
            'trial_file_sha256': hashes(path.parent), 'passed': True, 'model_calls': 0}


def observe_admission(path, control, pid):
    shared = Path(admission_control()['shared_pool'])
    state = read(shared.with_suffix('.state.json'))
    config = read(shared)
    own = {key: value for key, value in state['participants'].items()
           if value['control'] == str(control)}
    row = {'at': now(), 'runner_pid': pid, 'shared_max_active': config['max_active'],
           'shared_control_sha256': digest(shared),
           'total_claims': sum(len(p['trials']) for p in state['participants'].values()),
           'own_participants': own}
    resources = control.with_suffix('.resources.json')
    if resources.exists():
        row['local_resources'] = read(resources)
    with path.open('a') as output:
        output.write(json.dumps(row) + '\n')


def validate_admission(path, control):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    claims = [sum(len(p['trials']) for p in row['own_participants'].values()) for row in rows]
    assert max(claims) == 1 and claims[-1] == 0, 'Expected one acquired then released shared slot'
    assert all(row['total_claims'] <= row['shared_max_active'] for row in rows)
    assert all(row['shared_max_active'] == 14 for row in rows), 'Global cap changed; review explicitly'
    return {'path': str(path.relative_to(ROOT)), 'sha256': digest(path),
            'sample_count': len(rows), 'peak_own_claims': max(claims),
            'final_own_claims': claims[-1],
            'shared_cap_respected': True, 'global_cap': 14, 'passed': True}


def wait_with_observation(child, observe_once, interval=1):
    """Reap the same child even when the read-only evidence observer fails."""
    errors = []
    while True:
        finished = child.poll() is not None
        try:
            observe_once()
        except Exception as error:
            failure = {'error_type': type(error).__name__, 'error': str(error)}
            if failure not in errors:
                errors.append(failure)
        if finished:
            return errors
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    inputs = frozen_inputs()
    tools = tooling_hashes()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = (args.output or BASE / ('harbor-validation-' + stamp)).resolve()
    assert output.is_relative_to(BASE) and output != BASE, 'Validation output must be a new directory under this proof folder'
    if args.check_only:
        print(json.dumps({'check_only': True, 'passed': True, 'model_calls': 0, 'docker_runs': 0,
                          'task': str(TASK.relative_to(ROOT)), 'task_files': len(inputs['task_file_sha256']),
                          'expected_verifier_groups': GROUPS, 'admission': admission_control(),
                          'original_budgets': original_budgets(),
                          'captured_execution_tree': str((CAPTURE / 'files').relative_to(ROOT)),
                          'capture_manifest_sha256': digest(CAPTURE / 'manifest.json'),
                          'live_source_differences': live_source_differences(),
                          'commands': [command(a, output / 'jobs', stamp)[1] for a in ('oracle', 'nop')]}, indent=2))
        return

    from importlib.metadata import version
    from harbor.models.task.task import Task
    from pin_control_verifier_image import maybe_pin, finalize as finalize_image
    assert version('harbor') == '0.15.0'
    checksum = Task(TASK).checksum
    assert checksum == read(BASE / 'task-manifest.json')['harbor_task_checksum']
    budgets = original_budgets()
    assert not output.exists(), 'Do not overwrite an existing validation run'
    output.mkdir()
    jobs = output / 'jobs'
    jobs.mkdir()
    control = output / 'control.json'
    dump(control, admission_control())
    identity = {'schema_version': 1, 'started_at': now(), 'harbor_version': version('harbor'),
                'task': str(TASK.relative_to(ROOT)), 'task_checksum': checksum,
                'inputs': inputs, 'tooling_sha256': tools, 'original_budgets': budgets,
                'control_sha256': digest(control), 'model_calls': 0,
                'execution_source_tree': str((CAPTURE / 'files').relative_to(ROOT)),
                'live_source_differences_at_start': live_source_differences()}
    dump(output / 'run-identity.json', identity)
    summary = {'schema_version': 1, 'started_at': now(), 'passed': False, 'model_calls': 0,
               'run_identity': str((output / 'run-identity.json').relative_to(ROOT)),
               'run_identity_sha256': digest(output / 'run-identity.json'), 'controls': {}}
    dump(output / 'summary.json', summary)
    dump(BASE / 'latest-controls.json', {'output': str(output.relative_to(ROOT)),
                                       'pid': os.getpid(), 'started_at': now()})
    env = dict(os.environ, HARBOR_ADMISSION_CONTROL=str(control), PYTHONDONTWRITEBYTECODE='1')
    for agent in ('oracle', 'nop'):
        name, argv = command(agent, jobs, stamp)
        start = time.monotonic()
        print(f'{now()} Starting Harbor {agent} through shared admission: {name}', flush=True)
        record = {'job': name, 'command': argv, 'passed': False, 'model_calls': 0}
        try:
            with (output / f'{agent}.log').open('x') as log:
                child = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                observe = output / (agent + '-admission-observations.jsonl')
                def observe_control():
                    observe_admission(observe, control, child.pid)
                    if agent == 'oracle':
                        maybe_pin(jobs / name, output, checksum)
                observation_errors = wait_with_observation(child, observe_control)
                record['observation_errors'] = observation_errors
                record['exit_code'] = child.returncode
                assert not observation_errors, 'Admission observation failed; child was reaped before continuing'
            record['admission_lifecycle'] = validate_admission(observe, control)
            record['exit_code'] = child.returncode
            assert child.returncode == 0, 'Harbor control process failed'
            results = list((jobs / name).glob('*/result.json'))
            assert len(results) == 1, 'Expected exactly one trial result'
            record.update(validate_result(results[0], agent, checksum, tools))
            if agent == 'oracle':
                record['verifier_image_proof'] = finalize_image(output, results[0], ROOT)
        except Exception as error:
            record.update(error_type=type(error).__name__, error=str(error))
        record['wall_seconds'] = round(time.monotonic() - start, 2)
        summary['controls'][agent] = record
        dump(output / 'summary.json', summary)
        print(f'{now()} Harbor {agent}: passed={record["passed"]}', flush=True)

    summary['inputs_unchanged'] = frozen_inputs() == inputs
    summary['tooling_unchanged'] = tooling_hashes() == tools
    summary['live_source_differences_at_finish'] = live_source_differences()
    summary['finished_at'] = now()
    summary['passed'] = (summary['inputs_unchanged'] and summary['tooling_unchanged']
                         and all(r['passed'] for r in summary['controls'].values()))
    dump(output / 'summary.json', summary)
    print(json.dumps({'summary': str((output / 'summary.json').relative_to(ROOT)),
                      'passed': summary['passed'], 'model_calls': 0}), flush=True)
    if not summary['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
