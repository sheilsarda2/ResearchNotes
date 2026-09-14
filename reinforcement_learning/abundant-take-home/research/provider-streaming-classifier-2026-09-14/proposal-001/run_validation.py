#!/usr/bin/env python3
"""One offline pure-test proof. No runtime jobs, provider calls, or source rollout."""
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import subprocess
import sys

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def filemap(root):
    paths = sorted(root.rglob('*'))
    assert not any(p.is_symlink() for p in paths), 'Unexpected symlink'
    return {str(p.relative_to(root)): sha(p) for p in paths if p.is_file()}


def supplied():
    names = subprocess.check_output(
        ['git', 'ls-tree', '-r', '--name-only', 'c1ae968', '--', '.'], cwd=ROOT, text=True).splitlines()
    assert len(names) == 36
    answer = {}
    for name in names:
        original = subprocess.check_output(['git', 'show', f'c1ae968:./{name}'], cwd=ROOT)
        assert (ROOT / name).read_bytes() == original, name
        answer[name] = hashlib.sha256(original).hexdigest()
    return answer


def main():
    for name in ('validation.json', 'tests.log'):
        assert not (BASE / name).exists(), 'Never overwrite proof'
    started = datetime.now(timezone.utc).isoformat()
    inputs = json.loads((BASE / 'input-manifest.json').read_text())
    original = inputs['original_source_sha256']
    assert {p: sha(ROOT / p) for p in original} == original
    trial = ROOT / inputs['original_trial_path']
    job_before = filemap(trial.parent)
    assert filemap(trial) == inputs['original_trial_file_sha256']
    supplied_before = supplied()
    proposed = BASE / 'copied/scripts/run-candidate-screen.proposed.py'
    assert sha(proposed) == inputs['proposed_source_sha256']
    exceptions = ast.parse((BASE / 'package-source/litellm-exceptions.py').read_text())
    cls = next(n for n in exceptions.body if isinstance(n, ast.ClassDef) and n.name == 'MidStreamFallbackError')
    assert [ast.unparse(b) for b in cls.bases] == ['ServiceUnavailableError']
    test_command = [sys.executable, '-B', str(BASE / 'test_proposal.py')]
    completed = subprocess.run(test_command, cwd=ROOT, text=True, capture_output=True, timeout=60)
    with (BASE / 'tests.log').open('x') as out:
        out.write(completed.stdout + completed.stderr)
    spec = importlib.util.spec_from_file_location('offline_tests', BASE / 'test_proposal.py')
    tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tests)
    baseline = tests.projection(tests.classify(tests.BASELINE, trial / 'result.json', ROOT))
    changed = tests.projection(tests.classify(tests.PROPOSED, trial / 'result.json', ROOT))
    assert {p: sha(ROOT / p) for p in original} == original
    assert filemap(trial.parent) == job_before
    assert supplied() == supplied_before
    proof = {'schema_version': 1, 'kind': 'offline_classifier_proposal_validation',
        'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
        'passed': completed.returncode == 0, 'test_exit_code': completed.returncode,
        'tests': 13, 'test_command': test_command,
        'python': platform.python_version(), 'model_calls': 0, 'container_starts': 0,
        'live_runtime_or_control_edits': False, 'deployment': False, 'canary_adoption': False,
        'original_projection': baseline, 'proposed_projection': changed,
        'original_job_path': str(trial.parent.relative_to(ROOT)),
        'original_job_file_sha256': job_before, 'original_job_files_unchanged': True,
        'original_source_and_bound_inputs_unchanged': True,
        'supplied_original_sha256': supplied_before, 'supplied_originals_unchanged': True,
        'input_manifest_sha256': sha(BASE / 'input-manifest.json'),
        'proposed_source_sha256': sha(proposed), 'patch_sha256': sha(BASE / 'proposal.patch'),
        'test_source_sha256': sha(BASE / 'test_proposal.py'),
        'validation_source_sha256': sha(Path(__file__)), 'test_log_sha256': sha(BASE / 'tests.log'),
        'package_contract': {'mini_version': '2.4.6', 'litellm_version': '1.100.1',
            'midstream_class_base': 'ServiceUnavailableError',
            'package_source_sha256': inputs['package_source_sha256']},
        'limitations': ['Pure classifier tests run under Harbor Python, not agent lifecycle execution.',
            'Synthetic fixtures carry no real model prompts, thinking, signatures, or source submissions.',
            'Infrastructure classification cannot recover unknown failed-request usage or billing.',
            'No live reclassification, model transport fix, provider call, or deployment is performed.']}
    with (BASE / 'validation.json').open('x') as out:
        json.dump(proof, out, indent=2, sort_keys=True)
        out.write('\n')
    print(json.dumps({'passed': proof['passed'], 'tests': proof['tests'],
        'raw_job_files': len(job_before), 'protected_originals': len(supplied_before),
        'validation_sha256': sha(BASE / 'validation.json'),
        'baseline': baseline, 'proposed': changed}, indent=2))
    return completed.returncode


if __name__ == '__main__':
    raise SystemExit(main())
