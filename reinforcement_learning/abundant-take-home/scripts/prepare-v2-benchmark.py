#!/usr/bin/env python3
"""Freeze the user's nine-task v2 selection without editing task materials."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

from harbor.models.task.task import Task

ROOT = Path(__file__).resolve().parents[1]
TASKS = ['rs-burn-store-pytorch-reader', 'rs-zenoh-timestamp-instrumentation',
         'rs-burn-onnx-rnn-runtime-weights', 'cpp-foxglove-sdk-parameter-handler',
         'py-zarr-python-cast-value-scale-offset', 'cpp-zenoh-cpp-connectivity-api',
         'cpp-rosbag2-mixed-serialization-playback', 'py-rosbags-rosbag2-storage-writers',
         'rs-rerun-chunk-optimizer']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists(), 'Refusing to replace a frozen selection'
    spec = importlib.util.spec_from_file_location('candidate', ROOT/'scripts/candidate-bench.py')
    candidate = importlib.util.module_from_spec(spec); spec.loader.exec_module(candidate)
    tasks = []
    for name in TASKS:
        directory = ROOT/'candidates_v2'/name
        task = Task(directory)
        validation = json.loads((ROOT/'candidates_v2/validation'/f'{name}.json').read_text())
        evidence, result_hashes, historical = {}, {}, set()
        for kind, expected in (('oracle', 1), ('nop', 0)):
            paths = list((ROOT/'candidates_v2/validation/jobs'/validation[kind]['job']).glob('*/result.json'))
            assert len(paths) == 1
            result_path = paths[0]; result = json.loads(result_path.read_text())
            assert result['task_name'] == name and result['config']['agent']['name'] == kind
            assert result['exception_info'] is None and result['verifier_result']['rewards']['reward'] == expected
            evidence[kind] = str(result_path.relative_to(ROOT))
            result_hashes[kind] = hashlib.sha256(result_path.read_bytes()).hexdigest()
            historical.add(result['task_checksum'])
            if kind == 'oracle':
                score = json.loads((result_path.parent/'verifier/score.json').read_text())
                expected_groups = list(score['groups'])
                validation_finished = datetime.fromisoformat(result['finished_at'].replace('Z', '+00:00')).timestamp()
        assert len(historical) == 1, 'Oracle and nop used different task revisions'
        newer = [str(p.relative_to(directory)) for p in directory.rglob('*')
                 if p.is_file() and p.stat().st_mtime > validation_finished]
        assert set(newer) <= {'provenance.json', 'STATUS.md'}, f'{name}: executable files newer than controls: {newer}'
        tasks.append({'id': name, 'path': str(directory.relative_to(ROOT)),
                      'task_sha256': candidate.digest_task(directory),
                      'harbor_task_checksum': task.checksum,
                      'validation_basis': 'saved_controls_with_metadata_updates',
                      'historical_validation_checksum': historical.pop(),
                      'files_newer_than_validation': newer,
                      'validation_evidence': evidence, 'validation_result_sha256': result_hashes,
                      'check_format': 'verifier_groups', 'expected_verifier_groups': expected_groups})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'created_at': datetime.now(timezone.utc).isoformat(),
                                     'tasks': tasks, 'pilot_trials': 0,
                                     'validation_note': 'Saved controls predate provenance/status updates. Current complete task directories are independently frozen; old control checksums are retained honestly.'}, indent=2) + '\n')
    print(f'Frozen exactly {len(tasks)} tasks: {args.output}')


if __name__ == '__main__':
    main()
