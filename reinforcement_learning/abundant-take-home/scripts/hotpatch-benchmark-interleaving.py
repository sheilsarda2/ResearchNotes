#!/usr/bin/env python3
"""Update future admissions in a verified CPython 3.14 benchmark worker."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import textwrap
import time

from benchmark_shared_admission import process_identity

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ['scripts/benchmark_interleaving.py', 'scripts/benchmark_shared_admission.py',
           'scripts/harbor-resource-runner.py', 'scripts/benchmark_coverage_priority.py']


def payload(expected):
    body = '''import gc, hashlib, json, os, pathlib, runpy, sys, time
_expected = ''' + repr(expected) + '''
_ack = pathlib.Path(_expected['ack'])
_result = {'pid': os.getpid(), 'started_at': time.time(), 'passed': False}
try:
    from benchmark_shared_admission import process_identity, SharedAdmission
    assert os.getpid() == _expected['pid']
    assert process_identity(os.getpid()) == _expected['identity']
    root = pathlib.Path(_expected['root'])
    for name, digest in _expected['source_sha256'].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    from harbor.job import Job
    import __main__
    Admission = __main__.Admission
    jobs = [obj for obj in gc.get_objects() if isinstance(obj, Job)
            and obj.config.job_name == _expected['job']]
    admissions = [obj for obj in gc.get_objects() if isinstance(obj, Admission)
                  and obj.shared and str(obj.shared.path.resolve()) == _expected['shared']]
    assert len(jobs) == len(admissions) == 1
    admission = admissions[0]
    _result['active_before'] = sorted(admission.active)
    if _expected.get('runtime_proof'):
        from benchmark_mini_tool_runtime import install as install_runtime
        install_runtime()
        _result['reviewed_runtime_installed_for_future_agents'] = True
    import benchmark_interleaving as rounds
    import benchmark_coverage_priority
    # Refresh functions in the original module namespace, retaining its live
    # registry and existing acquire closures. Reloading would lose queue state.
    import ast
    source = root / 'scripts/benchmark_interleaving.py'
    tree = ast.parse(source.read_text())
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if _expected.get('priority_only'):
        assert getattr(jobs[0], '_benchmark_interleaving_registered', False)
        assert rounds.TRIALS
        functions = [node for node in functions if node.name in {'held_cell_keys', 'wait_reason'}]
        assert len(functions) == 2
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), rounds.__dict__)
    from benchmark_interleaving import install, register
    if not _expected.get('priority_only'):
        new_method = runpy.run_path(str(root / 'scripts/benchmark_shared_admission.py'))['SharedAdmission'].try_acquire
        # Old acquire frames call this method on each poll, so they also see the fix.
        SharedAdmission.try_acquire = new_method
        install(Admission, admission.shared)
        _result['registered_configs'] = register(jobs[0], admission.shared)
    else:
        _result['priority_only'] = True
    import benchmark_interleaving as rounds
    _result['registered_names'] = len(rounds.TRIALS)
    _result['module_id'] = id(rounds)
    _result['active_after'] = sorted(admission.active)
    assert _result['active_after'] == _result['active_before']
    _result.update(passed=True, source_sha256=_expected['source_sha256'],
                   identity=_expected['identity'], job=_expected['job'])
except BaseException as error:
    _result['error_type'] = type(error).__name__
finally:
    _result['finished_at'] = time.time()
    temporary = _ack.with_suffix('.tmp')
    temporary.write_text(json.dumps(_result, indent=2) + '\\n')
    temporary.replace(_ack)
'''
    # remote_exec itself can interrupt synchronous code while a flock is held.
    # Defer mutations to an event-loop callback, after that stack has unwound.
    return ('import asyncio\ndef _benchmark_apply_interleaving():\n'
            + textwrap.indent(body, '    ')
            + '\nasyncio.get_running_loop().call_soon(_benchmark_apply_interleaving)\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--identity', required=True)
    parser.add_argument('--job', required=True)
    parser.add_argument('--shared', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime-proof', type=Path)
    parser.add_argument('--priority-only', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    assert process_identity(args.pid) == args.identity, 'PID identity changed'
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    if args.runtime_proof:
        validator = __import__('runpy').run_path(str(ROOT / 'scripts/activate-benchmark-tool-runtime.py'))
        sources.update(validator['validate_proof'](args.runtime_proof.resolve()))
    args.output.mkdir(parents=True, exist_ok=True)
    ack = args.output.resolve() / f'{args.pid}-{args.identity}.ack.json'
    script = ack.with_suffix('.py')
    expected = dict(pid=args.pid, identity=args.identity, job=args.job,
                    shared=str(args.shared.resolve()), root=str(ROOT), ack=str(ack),
                    source_sha256=sources, runtime_proof=bool(args.runtime_proof),
                    priority_only=args.priority_only)
    script.write_text(payload(expected))
    if not args.apply:
        print(json.dumps(dict(prepared=str(script), **expected)))
        return
    assert not ack.exists(), 'Activation acknowledgement already exists'
    sys.remote_exec(args.pid, str(script))
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and not ack.exists():
        time.sleep(0.1)
    if not ack.exists():
        raise RuntimeError('Live update acknowledgement pending; inspect before retrying')
    result = json.loads(ack.read_text())
    print(json.dumps(result))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
