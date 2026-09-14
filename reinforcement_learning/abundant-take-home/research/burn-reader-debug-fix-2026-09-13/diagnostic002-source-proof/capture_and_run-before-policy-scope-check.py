#!/usr/bin/env python3
"""One diagnostic-002 run with supplemental imported-helper provenance."""
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import runpy
import sys

BASE = Path(__file__).resolve().parent
EVIDENCE = BASE.parent
ROOT = EVIDENCE.parents[1]
HARNESS = EVIDENCE / 'diagnose_saved_submission.py'
OUTPUT = EVIDENCE / 'saved-submission-diagnostic-002'
EXPECTED = {
    'scripts/benchmark_shared_admission.py': 'f6e2cee75437899f4c65eea299c38100b6140e5ed3be23d1074b7dfddc4384b6',
    'scripts/benchmark_interleaving.py': '995868b16804976ce48ef6a1bba52db97ca00f9f968799a41ed78e6ffa8ad62f',
    'scripts/harbor-resource-runner.py': '7f00c308bd679609b8855fc31387ab5acdcc2641a5e39027af74d65da10ed4d9',
    str(HARNESS.relative_to(ROOT)): 'ea5c6eff7dca9d6eebeefb80d0b0c4e55b3c21d71b2c97484ae595f79b6ca4f9',
}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, value):
    path = BASE / name
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def main():
    assert not OUTPUT.exists(), 'Never overwrite or retry a prior run'
    copies = BASE / 'source_snapshot'
    copies.mkdir()
    before = {}
    for name, expected in EXPECTED.items():
        source = ROOT / name
        assert sha(source) == expected, f'Source drift before capture: {name}'
        dest = copies / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
        assert sha(dest) == expected == sha(source)
        before[name] = {'sha256': expected, 'copy': str(dest.relative_to(ROOT))}
    write('source-manifest.json', before)
    sys.path.insert(0, str(ROOT / 'scripts'))
    interleaving = importlib.import_module('benchmark_interleaving')
    assert Path(interleaving.__file__).resolve() == ROOT / 'scripts/benchmark_interleaving.py'
    assert all(sha(ROOT / name) == expected for name, expected in EXPECTED.items())
    name = f'burn-reader-saved-alvwbse-{os.getpid()}'
    state_path = ROOT / 'jobs/candidate-campaigns-shared.control.state.json'
    state_before = json.loads(state_path.read_text())
    assert not state_before.get('interleaving'), 'Interleaving policy was unexpectedly enabled'
    assert name not in interleaving.TRIALS
    assert interleaving.managed(name, state_before) is False
    assert interleaving.wait_reason(name, state_before) is None
    intent = {'started_at': now(), 'pid': os.getpid(), 'container_name': name,
              'harness_sha256': sha(HARNESS), 'wrapper_sha256': sha(Path(__file__)),
              'source_manifest_sha256': sha(BASE / 'source-manifest.json'),
              'policy_enabled_before': bool(state_before.get('interleaving')),
              'interleaving_trials_registered_before': len(interleaving.TRIALS),
              'diagnostic_unmanaged_before': True, 'model_calls': 0,
              'output': str(OUTPUT.relative_to(ROOT))}
    write('intent.json', intent)
    sys.argv = [str(HARNESS), '--apply', '--output', str(OUTPUT)]
    code = None
    try:
        runpy.run_path(str(HARNESS), run_name='__main__')
        code = 0
    except SystemExit as error:
        code = error.code
    finally:
        after = {p: sha(ROOT / p) for p in EXPECTED}
        state_after = json.loads(state_path.read_text())
        unchanged = after == EXPECTED
        unmanaged = (name not in interleaving.TRIALS
                     and interleaving.managed(name, state_after) is False
                     and interleaving.wait_reason(name, state_after) is None)
        result = json.loads((OUTPUT / 'result.json').read_text()) if (OUTPUT / 'result.json').exists() else {}
        passed = (code == 0 and unchanged and unmanaged
                  and not state_after.get('interleaving')
                  and result.get('status') == 'complete' and result.get('inputs_unchanged') is True)
        write('result.json', {
            'finished_at': now(), 'passed': passed, 'harness_exit_code': code,
            'harness_result_sha256': sha(OUTPUT / 'result.json') if result else None,
            'source_sha256_after': after, 'captured_and_live_sources_unchanged': unchanged,
            'interleaving_imported_from': str(Path(interleaving.__file__).relative_to(ROOT)),
            'interleaving_trials_registered_after': len(interleaving.TRIALS),
            'diagnostic_unmanaged_after': unmanaged,
            'policy_enabled_after': bool(state_after.get('interleaving')),
            'model_calls': 0, 'full_regrade_performed': False, 'reward': None,
        })
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
