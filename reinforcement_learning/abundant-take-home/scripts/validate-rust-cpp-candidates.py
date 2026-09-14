#!/usr/bin/env python3
"""Sequential oracle/no-op validation of frozen candidates; never runs models.

Writes hash-bound evidence outside task directories. Does not promote tasks or
modify an existing shortlist. Run with uv run --with dirhash python <script>.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
from dirhash import dirhash

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bench', ROOT / 'scripts/candidate-bench.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prefix')
    parser.add_argument('tasks', nargs='+')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.prefix):
        parser.error('Use a simple unique job prefix')
    if len(set(args.tasks)) != len(args.tasks):
        parser.error('Duplicate task')
    inputs = {}
    for task_id in args.tasks:
        if not re.fullmatch(r'[a-z0-9-]+', task_id):
            parser.error('Invalid task ID')
        path = ROOT / 'candidates' / task_id
        inputs[task_id] = {'task_sha256': bench.digest_task(path),
                           'harbor_task_checksum': dirhash(path, 'sha256')}
    record = ROOT / 'research/validation' / (args.prefix + '-inputs.json')
    if record.exists():
        parser.error('Choose a new prefix; inputs already exist')
    bench.dump(record, inputs)
    evidence = {'input_manifest': str(record.relative_to(ROOT)), 'tasks': {}, 'failures': []}
    summary_path = record.with_name(args.prefix + '-summary.json')
    for index, task_id in enumerate(args.tasks, 1):
        task_path = ROOT / 'candidates' / task_id
        entry = {'id': task_id, 'path': str(task_path.relative_to(ROOT)),
                 'validated_task_sha256': inputs[task_id]['task_sha256'],
                 'validated_harbor_task_checksum': inputs[task_id]['harbor_task_checksum'],
                 'validation_evidence': {}, 'validation_result_sha256': {}}
        evidence['tasks'][task_id] = entry
        for kind, expected in (('oracle', 1), ('nop', 0)):
            name = f'{args.prefix}-{index:02d}-{kind}'
            job = ROOT / 'research/validation/jobs' / name
            log = ROOT / 'research/validation' / (name + '.log')
            try:
                if job.exists() or log.exists():
                    raise RuntimeError('Refusing to overwrite existing job/log')
                print(name, task_id, 'starting', flush=True)
                with log.open('w') as output:
                    run = subprocess.run(bench.harbor_command() + ['run', '-p', str(task_path),
                        '-a', kind, '--job-name', name, '--jobs-dir', 'research/validation/jobs'],
                        cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
                paths = list(job.glob('*/result.json'))
                if run.returncode or len(paths) != 1:
                    raise RuntimeError(f'Process exit {run.returncode}, results {len(paths)}; see {log}')
                path = paths[0]
                result = json.loads(path.read_text())
                reward = (result.get('verifier_result') or {}).get('rewards', {}).get('reward')
                if result.get('exception_info') is not None or reward != expected:
                    raise RuntimeError(f'Reward {reward}; exception {result.get("exception_info")}; see {path}')
                if result['task_name'] != task_id or result['config']['agent']['name'] != kind:
                    raise RuntimeError('Unexpected task or agent')
                if result['task_checksum'] != entry['validated_harbor_task_checksum']:
                    raise RuntimeError('Harbor checksum differs from frozen input')
                if bench.digest_task(task_path) != entry['validated_task_sha256']:
                    raise RuntimeError('Task changed during validation')
                entry[kind + '_verified'] = True
                entry['validation_evidence'][kind] = str(path.relative_to(ROOT))
                entry['validation_result_sha256'][kind] = hashlib.sha256(path.read_bytes()).hexdigest()
                diagnostic = path.parent / 'verifier/diagnostics.json'
                if not diagnostic.exists():
                    diagnostic = path.parent / 'verifier/summary.json'
                if kind == 'oracle' and diagnostic.exists():
                    entry['oracle_diagnostics'] = json.loads(diagnostic.read_text())
                print(name, task_id, 'reward', reward, flush=True)
            except Exception as error:
                evidence['failures'].append({'job': name, 'error': str(error)})
                bench.dump(summary_path, evidence)
                raise
            bench.dump(summary_path, evidence)
        bench.verify_validation(entry)
        entry['status'] = 'harbor_verified'
        bench.dump(summary_path, evidence)


if __name__ == '__main__':
    main()
