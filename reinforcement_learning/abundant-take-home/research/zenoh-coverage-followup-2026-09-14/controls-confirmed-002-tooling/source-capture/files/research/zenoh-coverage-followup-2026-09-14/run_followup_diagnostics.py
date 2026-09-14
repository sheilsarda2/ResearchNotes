"""Sequential, zero-model omission probes and two saved-source diagnostics.

Requires passed exact-revision Harbor controls. This creates no model attempt,
changes no score, and never overwrites a previous focused case.
"""
import sys
sys.dont_write_bytecode = True
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
HARNESS = BASE / 'harness/focused_validation_v2.py'


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def source_hashes(path):
    files = {}
    for item in sorted(Path(path).rglob('*')):
        assert not item.is_symlink()
        if item.is_file():
            files[str(item.relative_to(path))] = digest(item)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controls-summary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    controls = read(args.controls_summary)
    assert controls['passed'] is True and controls['model_calls'] == 0
    oracle = controls['controls']['oracle']
    assert oracle['passed'] is True and oracle['reward'] == 1
    assert controls['controls']['nop']['passed'] is True
    oracle_result = ROOT / oracle['result']
    assert digest(oracle_result) == oracle['result_sha256']
    oracle_source = oracle_result.parent / 'artifacts/submission'
    assert oracle_source.is_dir()
    oracle_expected = {name[len('artifacts/submission/'):]: value
                       for name, value in oracle['trial_file_sha256'].items()
                       if name.startswith('artifacts/submission/')}
    assert oracle_expected and source_hashes(oracle_source) == oracle_expected
    task = BASE / 'rs-zenoh-timestamp-instrumentation-v4-validation'
    manifest = read(BASE / 'task-manifest.json')
    assert source_hashes(task) == manifest['task_file_sha256']
    assert oracle['task_checksum'] == manifest['harbor_task_checksum']
    for folder, name in [('codec-test', 'timestamp_robustness.rs'),
                         ('admin-test', 'timestamp_adminspace.rs'),
                         ('admin-test', 'timestamp_adminspace_reply_stack.rs')]:
        assert digest(BASE / folder / name) == manifest['task_file_sha256']['tests/hidden/zenoh/tests/' + name]
    spec = importlib.util.spec_from_file_location('followup_score_parser', task / 'tests/score.py')
    score = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(score)
    output = args.output.resolve()
    assert output.is_relative_to(BASE / 'harness') and not output.exists()
    output.mkdir()
    mutants_path = BASE / 'mutants/manifest.json'
    mutants = read(mutants_path)
    saved_path = BASE / 'saved-source-audit/full-source-preflight.json'
    saved = read(saved_path)
    assert saved['passed'] is True
    cases = []
    for item in mutants['mutants']:
        patch = ROOT / item['patch']['path']
        assert digest(patch) == item['patch']['sha256']
        cases.append({'name': item['id'], 'kind': 'single_omission_probe',
                      'source': oracle_source, 'patch': patch,
                      'expected_failed_test': item['test'],
                      'expected_changed_file': item['source']})
    for trial in saved['trials']:
        root = ROOT / trial['trial']
        assert digest(root / 'result.json') == trial['result_sha256']
        expected = {name[len('artifacts/submission/'):]: value['sha256']
                    for name, value in trial['source_files'].items()}
        assert source_hashes(root / 'artifacts/submission') == expected
        cases.append({'name': root.name.split('__')[-1], 'kind': 'saved_success_diagnostic',
                      'source': root / 'artifacts/submission', 'original_trial': trial['trial']})
    summary = {'schema_version': 1, 'model_calls': 0, 'counted_sweep_trials': 0,
               'full_regrades': False, 'started_at': datetime.now(timezone.utc).isoformat(),
               'controls_summary': str(args.controls_summary.resolve().relative_to(ROOT)),
               'controls_summary_sha256': digest(args.controls_summary),
               'mutant_manifest_sha256': digest(mutants_path),
               'saved_source_preflight_sha256': digest(saved_path),
               'coordinator_sha256': digest(__file__), 'harness_sha256': digest(HARNESS),
               'cases': [], 'passed': False}
    write(output / 'summary.json', summary)
    for case in cases:
        assert source_hashes(task) == manifest['task_file_sha256']
        assert source_hashes(oracle_source) == oracle_expected
        for folder, name in [('codec-test', 'timestamp_robustness.rs'),
                             ('admin-test', 'timestamp_adminspace.rs'),
                             ('admin-test', 'timestamp_adminspace_reply_stack.rs')]:
            assert digest(BASE / folder / name) == manifest['task_file_sha256']['tests/hidden/zenoh/tests/' + name]
        destination = output / case['name']
        command = [sys.executable, "-B", str(HARNESS), '--run', '--source', 'saved',
                   '--saved-source', str(case['source']), '--output', str(destination)]
        if case.get('patch'):
            command += ['--patch', str(case['patch'])]
        print(datetime.now(timezone.utc).isoformat(), 'Starting', case['name'], flush=True)
        with (output / (case['name'] + '.controller.log')).open('x') as log:
            completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        result_path = destination / 'result.json'
        result = read(result_path)
        assert completed.returncode == 0 and result['status'] == 'complete', 'Diagnostic infrastructure failure'
        assert result['container_absent'] and result['claim_absent'] and result['inputs_unchanged']
        assert result['model_calls'] == 0
        text = (destination / 'case/cargo.stdout.log').read_text()
        binaries = score.parse_run(text)
        tests = {name: status for binary in binaries.values() for name, status in binary['tests'].items()}
        assert len(binaries) == 3 and len(tests) == 7 and 'error[E' not in text, 'All seven tests must execute'
        row = {'name': case['name'], 'kind': case['kind'], 'result': str(result_path.relative_to(ROOT)),
               'result_sha256': digest(result_path), 'case_exit_code': result['case_exit_code'],
               'tests': tests, 'all_tests_passed': all(value == 'ok' for value in tests.values()),
               'model_calls': 0, 'counted_sweep_trial': False, 'reward': None}
        if case['kind'] == 'single_omission_probe':
            before = read(destination / 'case/selected-source-manifest.json')
            after = read(destination / 'case/effective-source-manifest.json')
            changed = [name for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)]
            assert changed == [case['expected_changed_file']]
            assert tests[case['expected_failed_test']] == 'FAILED', 'Omission escaped its intended public assertion'
            assert result['case_exit_code'] == 101, 'Expected a normal Rust assertion failure'
            failed = {name for name, status in tests.items() if status == 'FAILED'}
            allowed = {case['expected_failed_test']}
            if case['name'] == 'omit-admin-receive':
                allowed.add('adminspace_reply_preserves_received_query_stack')
            assert failed == allowed and all(status == 'ok' for name, status in tests.items() if name not in allowed)
            signatures = {
                'omit-zero-config-rejection': ['Push: zero configuration'],
                'omit-count-rejection': ['Push: count 256'],
                'omit-append-cap': ['incoming count 255', 'left: 256', 'right: 255'],
                'retain-unknown-point-as-receive': ['skip both bad records and append one Receive', 'left: 4', 'right: 3'],
                'retain-undecodable-uhlc-as-custom': ['skip both bad records and append one Receive', 'left: 4', 'right: 3'],
                'omit-admin-receive': ['Receive-only admin delivery must invoke the router timestamp callback exactly once',
                                       "reply must preserve admin Receive and append the querying client's Receive exactly once",
                                       'left: 0', 'right: 1', 'left: 1', 'right: 2'],
            }
            assert all(signature in text for signature in signatures[case['name']]), 'Expected assertion signature missing'
            row.update(expected_failed_test=case['expected_failed_test'], omission_detected=True,
                       changed_source_files=changed, patch_sha256=digest(case['patch']),
                       expected_assertion_signatures=signatures[case['name']], other_tests_passed=True)
        else:
            row['original_trial'] = case['original_trial']
        summary['cases'].append(row)
        write(output / 'summary.json', summary)
    summary.update(passed=True, finished_at=datetime.now(timezone.utc).isoformat(),
                   meaning='All diagnostics executed cleanly and all six omissions were detected; saved-source outcomes are listed separately.')
    write(output / 'summary.json', summary)
    print(json.dumps({'summary': str((output / 'summary.json').relative_to(ROOT)), 'passed': True}))


if __name__ == '__main__':
    main()
