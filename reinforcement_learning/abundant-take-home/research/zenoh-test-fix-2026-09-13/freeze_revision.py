#!/usr/bin/env python3
"""Freeze the corrected task only after Harbor and alternative-layout checks pass."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
TASK = ROOT / 'research/task-revisions/rs-zenoh-timestamp-instrumentation-v3'
sys.path.insert(0, str(ROOT / 'scripts'))
from harbor.models.task.task import Task

spec = importlib.util.spec_from_file_location('candidate', ROOT / 'scripts/candidate-bench.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists(), 'Do not overwrite a frozen task selection'
    controls = read(BASE / 'harbor-validation.json')
    checksum = Task(TASK).checksum
    paths, hashes = {}, {}
    for kind, reward in (('oracle', 1), ('nop', 0)):
        path = Path(controls[kind]['result'])
        result = read(path)
        assert result['finished_at'] and result['exception_info'] is None
        assert result['task_name'] == TASK.name and result['task_checksum'] == checksum
        assert result['config']['agent']['name'] == kind
        assert result['verifier_result']['rewards']['reward'] == reward
        assert result['config']['timeout_multiplier'] == 1
        assert not result['config']['verifier'].get('override_timeout_sec')
        assert all(value is None for key, value in result['config']['environment'].items()
                   if key.startswith('override_'))
        paths[kind] = str(path.relative_to(ROOT))
        hashes[kind] = digest(path)

    groups = read(TASK / 'provenance.json')['oracle']['groups']
    oracle_score = read(Path(controls['oracle']['result']).parent / 'verifier/score.json')
    assert oracle_score['reward'] == 1 and set(groups) == set(oracle_score['groups'])
    assert all(group.get('pass') is True for group in oracle_score['groups'].values())
    diagnostic_root = Path(read(BASE / 'latest-validation.json')['output'])
    historical = [ROOT / path for path in read(BASE / 'origin.json')['source_failure_trials']]
    diagnostics = {}
    for trial in ('nBCUPNz', 'sAWGR4Y'):
        path = diagnostic_root / trial / 'result.json'
        record = read(path)
        assert record['status'] == 'complete' and record['diagnostic_pass'] is True
        assert record['full_regrade_performed'] is False and record['model_calls'] == 0
        assert record['verifier_exit_code'] == 0
        assert record['case'] == trial and Path(record['task']).resolve() == TASK.resolve()
        expected_sources = [path for path in historical if path.name.endswith('__' + trial)]
        assert len(expected_sources) == 1
        assert Path(record['source_trial']).resolve() == expected_sources[0].resolve()
        expected = record['task_file_sha256']
        actual = {str(p.relative_to(TASK)): digest(p) for p in TASK.rglob('*') if p.is_file()}
        assert actual == expected, 'Task changed after alternative-layout validation'
        source = Path(record['source_trial']) / 'artifacts/submission'
        assert record['submission_file_sha256'] and record['submission_file_sha256'] == {
            str(p.relative_to(source)): digest(p) for p in source.rglob('*') if p.is_file()}
        diagnostics[trial] = {'path': str(path.relative_to(ROOT)), 'sha256': digest(path)}

    task = {'id': TASK.name, 'path': str(TASK.relative_to(ROOT)),
            'revision_of': 'rs-zenoh-timestamp-instrumentation',
            'validated_task_sha256': candidate.digest_task(TASK),
            'validated_harbor_task_checksum': checksum,
            'validation_basis': 'harbor_controls_and_alternative_layout_diagnostics',
            'validation_evidence': paths, 'validation_result_sha256': hashes,
            'alternative_layout_diagnostics': diagnostics,
            'check_format': 'verifier_groups', 'expected_verifier_groups': groups}
    candidate.verify_validation(task)
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'tasks': [task],
                'validation_note': 'Exact-revision Harbor oracle/nop controls plus focused checks '
                                   'on both unchanged historical submissions. No model calls or '
                                   'retroactive score replacements.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        json.dump(manifest, output, indent=2)
        output.write('\n')
    print(f'Frozen validated revision: {args.output}')


if __name__ == '__main__':
    main()
