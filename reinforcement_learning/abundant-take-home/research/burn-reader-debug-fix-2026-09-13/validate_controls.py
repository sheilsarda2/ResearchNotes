#!/usr/bin/env python3
"""Run one standard Harbor oracle and one nop against the frozen Burn revision.

Both controls use the unchanged task budgets and the shared admission pool.
--check-only performs file checks and prints the plan without starting Harbor.
"""
from contextlib import redirect_stdout
from datetime import datetime, timezone
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
TASK = ROOT / 'research/task-revisions/rs-burn-store-pytorch-reader-v4'
GROUPS = ['build', 'anticheat', 'pytorch_unit', 'safetensors_unit',
          'integration', 'pytorch_tests_crate', 'fixture_matrix', 'reject_cases']
COUNTS = {'pytorch_unit': 108, 'safetensors_unit': 52, 'integration': 20,
          'pytorch_tests_crate': 37, 'fixture_matrix': 62, 'reject_cases': 32}


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
    # This independently checks the seven assertion substitutions, unchanged files,
    # and retention of the corrected TAR sys_info fixture.
    captured = io.StringIO()
    with redirect_stdout(captured):
        runpy.run_path(str(BASE / 'validate_assertions.py'), run_name='__main__')
    validation = json.loads(captured.getvalue())
    assert validation['passed'] is True and validation['model_calls'] == validation['docker_runs'] == 0
    origin = read(BASE / 'origin.json')
    assert origin['revision'] == str(TASK.relative_to(ROOT))
    task_files = hashes(TASK)
    assert task_files == {name: row['sha256'] for name, row in read(BASE / 'revision-manifest.json')['files'].items()}
    return {'task_file_sha256': task_files,
            'origin_sha256': digest(BASE / 'origin.json'),
            'source_manifest_sha256': digest(BASE / 'source-manifest.json'),
            'revision_manifest_sha256': digest(BASE / 'revision-manifest.json'),
            'assertion_diff_sha256': digest(BASE / 'assertion.diff'),
            'assertion_validation': validation}


def tooling_hashes():
    # Bind the runner's local helper set, not installed Harbor or any live task.
    paths = list((ROOT / 'scripts').glob('benchmark_*.py'))
    paths += [ROOT / 'scripts/harbor-resource-runner.py', ROOT / 'scripts/candidate-bench.py']
    paths += [BASE / name for name in ('validate_controls.py', 'freeze_revision.py', 'validate_assertions.py')]
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(paths)}


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
    return name, [sys.executable, str(ROOT / 'scripts/harbor-resource-runner.py'),
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
    assert all(type(group.get('ok')) is bool for group in score['groups'].values()), 'Expected native ok booleans'
    if agent == 'oracle':
        assert all(group['ok'] is True for group in score['groups'].values()), 'Oracle did not pass all eight groups'
        for group, count in COUNTS.items():
            row = score['groups'][group]
            assert row['passed'] == row['expected'] == count and row['failed'] == 0
        assert score['groups']['pytorch_unit']['ignored'] == 0
        matrix = score['groups']['fixture_matrix']
        assert matrix['manifest_ok'] is True and matrix['tensor_rows'] == 428
    else:
        assert any(group['ok'] is False for group in score['groups'].values()), 'Nop did not exercise the negative control'
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
    return {'result': str(path.relative_to(ROOT)), 'result_sha256': digest(path),
            'reward': expected_reward, 'exception': None, 'task_checksum': checksum,
            'agent_info': result['agent_info'], 'groups': score['groups'],
            'timings': {key: result.get(key) for key in ('environment_setup', 'agent_execution', 'verifier')},
            'trial_file_sha256': hashes(path.parent), 'passed': True, 'model_calls': 0}


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
                          'commands': [command(a, output / 'jobs', stamp)[1] for a in ('oracle', 'nop')]}, indent=2))
        return

    from importlib.metadata import version
    from harbor.models.task.task import Task
    assert version('harbor') == '0.15.0'
    checksum = Task(TASK).checksum
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
                'control_sha256': digest(control), 'model_calls': 0}
    dump(output / 'run-identity.json', identity)
    summary = {'schema_version': 1, 'started_at': now(), 'passed': False, 'model_calls': 0,
               'run_identity': str((output / 'run-identity.json').relative_to(ROOT)),
               'run_identity_sha256': digest(output / 'run-identity.json'), 'controls': {}}
    dump(output / 'summary.json', summary)
    dump(BASE / 'latest-controls.json', {'output': str(output.relative_to(ROOT)),
                                       'pid': os.getpid(), 'started_at': now()})
    env = dict(os.environ, HARBOR_ADMISSION_CONTROL=str(control))
    for agent in ('oracle', 'nop'):
        name, argv = command(agent, jobs, stamp)
        start = time.monotonic()
        print(f'{now()} Starting Harbor {agent} through shared admission: {name}', flush=True)
        record = {'job': name, 'command': argv, 'passed': False, 'model_calls': 0}
        try:
            with (output / f'{agent}.log').open('x') as log:
                completed = subprocess.run(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            record['exit_code'] = completed.returncode
            assert completed.returncode == 0, 'Harbor control process failed'
            results = list((jobs / name).glob('*/result.json'))
            assert len(results) == 1, 'Expected exactly one trial result'
            record.update(validate_result(results[0], agent, checksum, tools))
        except Exception as error:
            record.update(error_type=type(error).__name__, error=str(error))
        record['wall_seconds'] = round(time.monotonic() - start, 2)
        summary['controls'][agent] = record
        dump(output / 'summary.json', summary)
        print(f'{now()} Harbor {agent}: passed={record["passed"]}', flush=True)

    summary['inputs_unchanged'] = frozen_inputs() == inputs
    summary['tooling_unchanged'] = tooling_hashes() == tools
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
