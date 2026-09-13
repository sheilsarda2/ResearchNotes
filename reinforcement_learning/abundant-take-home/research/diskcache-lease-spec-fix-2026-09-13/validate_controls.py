#!/usr/bin/env python3
"""Run one standard Harbor oracle and one nop against the frozen DiskCache revision.

Both controls use the unchanged task budgets and the shared admission pool.
--check-only performs file checks and prints the plan without starting Harbor.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import difflib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
TASK = ROOT / 'research/task-revisions/diskcache-online-reshard-v2'
EXPECTED_CHECKS = 45


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


def original_supplied_files():
    # Compare directly with the supplied commit, rather than trust a prior check.
    prefix = subprocess.check_output(
        ['git', 'rev-parse', '--show-prefix'], cwd=ROOT, text=True).strip()
    names = subprocess.check_output(
        ['git', 'ls-tree', '-rz', '--name-only', 'c1ae968', '--', '.'], cwd=ROOT
    ).decode().split('\0')
    result = {}
    for name in filter(None, names):
        expected = subprocess.check_output(['git', 'show', 'c1ae968:' + prefix + name], cwd=ROOT)
        path = ROOT / name
        assert not path.is_symlink() and path.read_bytes() == expected, f'Supplied file changed: {name}'
        result[name] = digest(path)
    assert len(result) == 36, 'Unexpected original supplied file set'
    return result


def frozen_inputs():
    origin = read(BASE / 'origin.json')
    assert origin['source'] == 'candidates/diskcache-online-reshard'
    assert origin['revision'] == str(TASK.relative_to(ROOT))
    assert origin['changed_files'] == ['instruction.md']
    assert origin['source_files'] == origin['revision_files'] == 50
    manifests = {}
    for label in ('source', 'revision'):
        manifest_path = BASE / f'{label}-manifest.json'
        assert digest(manifest_path) == origin[f'{label}_manifest_sha256']
        saved = read(manifest_path)
        assert saved['schema_version'] == 1 and saved['root'] == origin[label]
        folder = ROOT / saved['root']
        current = {
            name: {'sha256': sha, 'bytes': (folder / name).stat().st_size,
                   'mode': oct((folder / name).stat().st_mode & 0o777)}
            for name, sha in hashes(folder).items()
        }
        assert current == saved['files'], f'{label} task files changed'
        manifests[label] = current
    before, after = manifests['source'], manifests['revision']
    assert before.keys() == after.keys() and len(after) == 50
    assert [name for name in before if before[name] != after[name]] == ['instruction.md']
    assert before['instruction.md']['mode'] == after['instruction.md']['mode']
    # Include the pre-campaign bytecode; it is part of the locked task digest.
    assert 'tests/__pycache__/test_online_reshard.cpython-312-pytest-8.3.5.pyc' in after
    old = (ROOT / origin['source'] / 'instruction.md').read_bytes().splitlines(keepends=True)
    new = (TASK / 'instruction.md').read_bytes().splitlines(keepends=True)
    changes = [row for row in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes()
               if row[0] != 'equal']
    assert changes == [('replace', 31, 32, 31, 32)], 'Only the lease paragraph may change'
    assert old[31].startswith(b'Coordinate managed clients') and new[31].startswith(b'Coordinate managed clients')
    actual_diff = ''.join(difflib.unified_diff(
        [line.decode() for line in old], [line.decode() for line in new],
        fromfile=origin['source'] + '/instruction.md', tofile=origin['revision'] + '/instruction.md'))
    assert actual_diff.encode() == (BASE / 'instruction.diff').read_bytes()
    assert digest(BASE / 'instruction.diff') == origin['diff_sha256']
    return {'task_file_sha256': {name: row['sha256'] for name, row in after.items()},
            'origin_sha256': digest(BASE / 'origin.json'),
            'source_manifest_sha256': digest(BASE / 'source-manifest.json'),
            'revision_manifest_sha256': digest(BASE / 'revision-manifest.json'),
            'instruction_diff_sha256': digest(BASE / 'instruction.diff'),
            'original_supplied_file_sha256': original_supplied_files(),
            'preservation': {'task_files': 50, 'changed_files': ['instruction.md'],
                             'single_lease_paragraph_only': True, 'original_supplied_files': 36}}


def tooling_hashes():
    # Bind the runner's local helper set, not installed Harbor or any live task.
    paths = list((ROOT / 'scripts').glob('benchmark_*.py'))
    paths += [ROOT / 'scripts/harbor-resource-runner.py', ROOT / 'scripts/candidate-bench.py']
    paths += [BASE / name for name in ('validate_controls.py', 'freeze_revision.py')]
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(paths)}


def original_budgets():
    import tomllib
    task = tomllib.loads((TASK / 'task.toml').read_text())
    assert task['agent']['timeout_sec'] == 7200
    assert task['verifier']['timeout_sec'] == 900
    assert task['environment']['cpus'] == 2 and task['environment']['memory_mb'] == 4096
    assert task['environment']['storage_mb'] == 10240
    assert task['verifier']['environment'] == {
        'network_mode': 'no-network', 'build_timeout_sec': 900,
        'cpus': 2, 'memory_mb': 4096, 'storage_mb': 10240}
    assert task['environment']['build_timeout_sec'] == 900
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


def individual_checks(path):
    root = ET.parse(path).getroot()
    assert root.tag in ('testsuite', 'testsuites'), 'Expected native pytest JUnit XML'
    suites = list(root.iter('testsuite'))
    assert suites and all(not list(suite.findall('testsuite')) for suite in suites)
    totals = {key: 0 for key in ('tests', 'failures', 'errors', 'skipped')}
    checks = []
    for suite in suites:
        counts = {key: int(suite.attrib[key]) for key in totals}
        assert all(count >= 0 for count in counts.values())
        cases = list(suite.findall('testcase'))
        assert counts['tests'] == len(cases), 'JUnit test total differs from native cases'
        for attribute, tag in (('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')):
            assert counts[attribute] == sum(len(case.findall(tag)) for case in cases)
        for case in cases:
            statuses = [tag for tag in ('failure', 'error', 'skipped') if case.find(tag) is not None]
            assert len(statuses) <= 1, 'Ambiguous individual test status'
            checks.append({'classname': case.attrib['classname'], 'name': case.attrib['name'],
                           'status': statuses[0] if statuses else 'passed'})
        for key in totals:
            totals[key] += counts[key]
    assert len({(case['classname'], case['name']) for case in checks}) == len(checks)
    return {'format': 'individual_checks', **totals,
            'passed': sum(case['status'] == 'passed' for case in checks), 'cases': checks}


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
    checks = individual_checks(path.parent / 'verifier/results.xml')
    if agent == 'oracle':
        assert checks['tests'] == checks['passed'] == EXPECTED_CHECKS, 'Oracle must pass exactly 45 native pytest cases'
        assert checks['failures'] == checks['errors'] == checks['skipped'] == 0
    else:
        assert checks['failures'] + checks['errors'] > 0, 'Nop did not exercise the negative control'
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
            'agent_info': result['agent_info'], 'individual_checks': checks,
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
                          'check_format': 'individual_checks', 'expected_checks': EXPECTED_CHECKS,
                          'original_supplied_files': len(inputs['original_supplied_file_sha256']),
                          'admission': admission_control(),
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
