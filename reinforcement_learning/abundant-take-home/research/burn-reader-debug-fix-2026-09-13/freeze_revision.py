#!/usr/bin/env python3
"""Write a promotion-ready selection only after exact-revision Harbor controls pass.

This creates proof files within this research folder; it does not promote a task,
change campaign controls, or rewrite any prior result.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

from validate_controls import (BASE, ROOT, TASK, GROUPS, digest, frozen_inputs,
                               hashes, original_budgets, read, tooling_hashes, validate_result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controls', type=Path, required=True, help='Exact completed summary.json')
    parser.add_argument('--output', type=Path, required=True, help='New frozen selection JSON')
    args = parser.parse_args()
    controls_path = args.controls.resolve()
    output = args.output.resolve()
    assert controls_path.is_relative_to(BASE) and output.is_relative_to(BASE)
    assert not output.exists(), 'Do not overwrite a frozen selection'
    controls = read(controls_path)
    assert controls['schema_version'] == 1 and controls['passed'] is True
    assert controls['model_calls'] == 0 and controls['finished_at']
    assert controls['inputs_unchanged'] is True and controls['tooling_unchanged'] is True
    assert set(controls['controls']) == {'oracle', 'nop'}
    identity_path = ROOT / controls['run_identity']
    assert identity_path.parent == controls_path.parent
    assert digest(identity_path) == controls['run_identity_sha256']
    identity = read(identity_path)
    assert identity['model_calls'] == 0 and identity['harbor_version'] == '0.15.0'
    assert identity['task'] == str(TASK.relative_to(ROOT))
    assert identity['inputs'] == frozen_inputs(), 'Task changed since controls'
    assert identity['tooling_sha256'] == tooling_hashes(), 'Validation tooling changed since controls'
    assert identity['original_budgets'] == original_budgets()
    assert digest(identity_path.parent / 'control.json') == identity['control_sha256']

    from harbor.models.task.task import Task
    checksum = Task(TASK).checksum
    assert checksum == identity['task_checksum']
    paths, result_hashes, artifact_hashes = {}, {}, {}
    for agent in ('oracle', 'nop'):
        record = controls['controls'][agent]
        assert record['passed'] is True and record['model_calls'] == 0 and record['exit_code'] == 0
        path = ROOT / record['result']
        assert path.resolve().is_relative_to(controls_path.parent / 'jobs')
        checked = validate_result(path, agent, checksum, identity['tooling_sha256'])
        for key, value in checked.items():
            assert record[key] == value, f'{agent} validation evidence changed: {key}'
        paths[agent] = str(path.relative_to(ROOT))
        result_hashes[agent] = digest(path)
        artifact_hashes[agent] = hashes(path.parent)

    spec = importlib.util.spec_from_file_location('candidate', ROOT / 'scripts/candidate-bench.py')
    candidate = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec.loader.exec_module(candidate)
    task = {'id': TASK.name, 'path': str(TASK.relative_to(ROOT)),
            'revision_of': 'rs-burn-store-pytorch-reader',
            'validated_task_sha256': candidate.digest_task(TASK),
            'validated_harbor_task_checksum': checksum,
            'validation_basis': 'standard_harbor_oracle_nop_and_exact_fixture_manifests',
            'validation_evidence': paths, 'validation_result_sha256': result_hashes,
            'oracle_verified': True, 'nop_verified': True,
            'check_format': 'verifier_groups', 'expected_verifier_groups': GROUPS}
    candidate.verify_validation(task)
    selection = {'created_at': datetime.now(timezone.utc).isoformat(), 'tasks': [task],
                 'model_calls': 0,
                 'validation_summary': str(controls_path.relative_to(ROOT)),
                 'validation_summary_sha256': digest(controls_path),
                 'run_identity_sha256': digest(identity_path),
                 'validation_artifact_sha256': artifact_hashes,
                 'source_manifest_sha256': identity['inputs']['source_manifest_sha256'],
                 'revision_manifest_sha256': identity['inputs']['revision_manifest_sha256'],
                 'validation_note': 'Unchanged task budgets, original eight groups with native ok booleans; '
                     'oracle=1 and nop=0. Seven error assertions no longer require PytorchReader: Debug. '
                     'No promotion, model calls, historical score changes, or claim that the '
                     'historical submitted implementation passes.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as file:
        json.dump(selection, file, indent=2)
        file.write('\n')
    print(f'Frozen validated Burn revision: {output}')


if __name__ == '__main__':
    main()
