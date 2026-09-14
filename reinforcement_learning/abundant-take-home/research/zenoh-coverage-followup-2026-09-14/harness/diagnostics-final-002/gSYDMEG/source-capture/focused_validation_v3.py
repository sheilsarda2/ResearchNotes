#!/usr/bin/env python3
"""Focused Zenoh validation, never a model call or full regrade. Default: check only.

Run inside the existing Linux devcontainer. --run explicitly launches one shared-cap
case. A failed test is preserved as a test outcome; cleanup precedes claim release.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
TASK = ROOT / 'research/task-revisions/rs-zenoh-timestamp-instrumentation-v3'
CACHED_TASK = ROOT / 'candidates_v2/rs-zenoh-timestamp-instrumentation'
TOOLS = BASE / 'immutable-tooling-v1'
TOOLS_MANIFEST_SHA = 'eea36c9e6aaa1fdee9cd2a53519ebefa29bb8bd664ef6e9f4c0f68d99f194079'
SRC_DIRS = ('commons/zenoh-protocol/src', 'commons/zenoh-codec/src', 'zenoh/src', 'zenoh-ext/src')
TESTS = {
    'timestamp_robustness.rs': BASE / 'codec-test/timestamp_robustness.rs',
    'timestamp_adminspace.rs': BASE / 'admin-test/timestamp_adminspace.rs',
    'timestamp_adminspace_reply_stack.rs': BASE / 'admin-test/timestamp_adminspace_reply_stack.rs',
}
DEFAULT_IMAGE = 'sha256:95afa96eacc4d6fe4cb2340f19492770c4f0508dca6600a6004c545921843d6f'


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    require(not Path(path).is_symlink(), 'Symlink input: ' + str(path))
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hashes(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), 'Missing/linked source directory: ' + str(root))
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'Symlink input: ' + str(path))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path)
    require(result, 'Empty source tree: ' + str(root))
    return result


def write(path, value):
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    tmp.replace(path)


def patch_paths(patch):
    """Inspect every path, independent of a containing repository's cwd filter."""
    # git apply silently omits paths outside a repository subdirectory. Inspect
    # in a private directory outside the worktree, without inherited Git context.
    git_env = {key: value for key, value in os.environ.items()
               if not key.startswith('GIT_')}
    with tempfile.TemporaryDirectory(prefix='zenoh-patch-inspection-') as directory:
        completed = subprocess.run(['git', 'apply', '--numstat', '-z', str(Path(patch).resolve())],
                                   cwd=directory, env=git_env, text=True, capture_output=True,
                                   check=True, timeout=20)
    paths = [row.split('\t', 2)[2] for row in completed.stdout.split('\0') if row]
    require(paths and all(not Path(p).is_absolute() and '..' not in Path(p).parts and
                          any(p.startswith(d + '/') for d in SRC_DIRS) for p in paths),
            'Patch may change only the four collected src directories')
    return paths


def capture(args):
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', args.image), 'Use an exact image sha256 ID')
    require(digest(TOOLS / 'manifest.json') == TOOLS_MANIFEST_SHA, 'Immutable tooling manifest drift')
    tool_manifest = json.loads((TOOLS / 'manifest.json').read_text())
    for path, expected in tool_manifest['source_sha256'].items():
        require(digest(TOOLS / 'files' / path) == expected, 'Frozen helper drift: ' + path)
    equivalent = ('tests/Dockerfile', 'tests/image-source/gold.patch',
                  'environment/upstream.tar.gz', 'environment/Dockerfile', 'task.toml')
    for name in equivalent:
        require(digest(CACHED_TASK / name) == digest(TASK / name), 'Cached environment differs: ' + name)
    gold_manifest = BASE / 'mutants/gold-source-files.json'
    saved = {}
    if args.source == 'saved':
        require(args.saved_source is not None, '--source saved requires --saved-source')
        source = args.saved_source.resolve()
        require(source.is_relative_to(ROOT), 'Saved source must be within this workspace')
        for directory in SRC_DIRS:
            saved.update({directory + '/' + n: h for n, h in hashes(source / directory).items()})
    else:
        require(args.saved_source is None, '--saved-source is only valid with --source saved')
    patch_hash = None
    if args.patch:
        require(args.patch.resolve().is_relative_to(ROOT), 'Patch must be within this workspace')
        patch_hash = digest(args.patch)
        paths = patch_paths(args.patch)
    return dict(schema_version=2, image=args.image, source=args.source, saved_sources=saved,
                cached_source_task=str(CACHED_TASK.relative_to(ROOT)),
                cached_47_test_file_is_not_executed=True,
                cached_environment_equivalence={name: digest(TASK / name) for name in equivalent},
                gold_source_manifest_sha256=digest(gold_manifest),
                expected_gold_sources=json.loads(gold_manifest.read_text()),
                patch_sha256=patch_hash, tests={n: digest(p) for n, p in TESTS.items()},
                image_files={'/tests/image-source/gold.patch': digest(TASK / 'tests/image-source/gold.patch'),
                  '/tests/hidden/zenoh/tests/timestamp_instrumentation.rs': digest(
                      CACHED_TASK / 'tests/hidden/zenoh/tests/timestamp_instrumentation.rs')},
                task_files=hashes(TASK), helper_manifest_sha256=TOOLS_MANIFEST_SHA,
                helper_files=tool_manifest['source_sha256'],
                harness_files={p.name: digest(p) for p in (Path(__file__), HERE / 'case_inside_v2.py')},
                limits=dict(cpus=4, memory_bytes=8192*1024*1024, network='none',
                            case_timeout_seconds=3600, shared_max_active=14, local_max_active=1))


def launch(args, inputs):
    require(Path('/proc/self/stat').exists(), 'Run inside the existing Linux devcontainer')
    output = args.output.absolute()
    require(output.is_relative_to(HERE) and output.resolve() == output and not output.exists(),
            'Choose a fresh real output path under harness/')
    output.mkdir(parents=True)
    write(output / 'inputs.json', inputs)
    shutil.copyfile(TOOLS / 'manifest.json', output / 'tooling-manifest.json')
    frozen = output / 'source-capture'
    frozen.mkdir()
    for path in (Path(__file__), HERE / 'case_inside_v2.py', *TESTS.values()):
        shutil.copyfile(path, frozen / path.name)
        require(digest(path) == digest(frozen / path.name), 'Input changed while copying')
    if args.patch:
        shutil.copyfile(args.patch, frozen / 'focused.patch')
        require(digest(frozen / 'focused.patch') == inputs['patch_sha256'], 'Patch snapshot drift')
    if args.source == 'saved':
        for directory in SRC_DIRS:
            dest = frozen / 'saved' / directory
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(args.saved_source / directory, dest)
    require(capture(args) == inputs, 'Input drift before admission')
    pool = ROOT / 'jobs/candidate-campaigns-shared.control.json'
    require(json.loads(pool.read_text())['max_active'] == 14, 'Shared pool must remain at 14')
    local = dict(max_active=1, startup_reserve_mb=1536, reserve_mb=4096, shared_pool=str(pool))
    write(output / 'control.json', local)
    sys.path.insert(0, str(TOOLS / 'files/scripts'))
    from benchmark_shared_admission import SharedAdmission
    from benchmark_recovery import memory_snapshot
    from benchmark_interleaving import managed
    name = 'zenoh-focused-' + uuid.uuid4().hex[:16]
    record = dict(kind='focused_no_model_diagnostic', full_regrade=False, reward=None, model_calls=0,
                  status='waiting', started_at=now(), name=name, image=args.image,
                  inputs_sha256=digest(output / 'inputs.json'), limits=inputs['limits'], lifecycle=[])
    admission = SharedAdmission(pool, output / 'control.json')
    record['admission_key'] = admission.key
    claimed, deadline = False, None
    command_number = 0

    def event(kind, **fields):
        record['lifecycle'].append(dict(at=now(), event=kind, **fields))
        write(output / 'result.json', record)

    def command(label, argv, *, check=True, cleanup=False):
        nonlocal command_number
        command_number += 1
        log = output / f'{command_number:02d}-{label}.log'
        remaining = 60 if cleanup else max(1, math.ceil(deadline - time.monotonic()))
        require(cleanup or time.monotonic() < deadline, 'Admitted case deadline reached')
        failure = None
        try:
            with log.open('w') as stream:
                completed = subprocess.run(argv, text=True, stdout=stream, stderr=subprocess.STDOUT,
                                           timeout=remaining)
        except (subprocess.TimeoutExpired, OSError) as error:
            failure = type(error).__name__
            with log.open('a') as stream:
                stream.write('\nHarness command failure: ' + failure + '\n')
            completed = subprocess.CompletedProcess(argv, 124 if isinstance(error, subprocess.TimeoutExpired) else 125)
        event('command', label=label, exit_code=completed.returncode, log=log.name, sha256=digest(log))
        if failure and not cleanup:
            raise RuntimeError(label + ': ' + failure)
        if check:
            require(completed.returncode == 0, f'{label} failed; see {log.name}')
        return completed, log

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'Interrupted by signal {signum}')

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        event('registered')
        wait_started, last_notice = time.monotonic(), 0
        while True:
            with admission.locked() as (config, state):
                require(config['max_active'] == 14, 'Shared capacity changed')
                require(not managed(name, state), 'Diagnostic must be unmanaged by interleaving policy')
            require(time.monotonic() - wait_started < args.admission_timeout, 'Admission wait deadline reached')
            reason = admission.try_acquire(name, memory_snapshot(), local)
            if reason is None:
                claimed, deadline = True, time.monotonic() + 3600
                with admission.locked() as (config, state):
                    counts = dict(shared_claim_count=sum(len(p['trials']) for p in state['participants'].values()),
                                  own_claim_count=len(state['participants'][admission.key]['trials']),
                                  shared_max_active=config['max_active'])
                require(counts['own_claim_count'] == 1 and counts['shared_claim_count'] <= 14,
                        'Claim count exceeds scoped resource limits')
                event('claimed', admitted_at=now(), **counts)
                require(capture(args) == inputs, 'Input drift before container startup')
                break
            if time.monotonic() - last_notice >= 30:
                require(capture(args) == inputs, 'Input drift while queued')
                print(now(), 'waiting:', reason, flush=True)
                event('waiting', reason=reason)
                last_notice = time.monotonic()
            time.sleep(2)
        record['status'] = 'running'
        _, image_log = command('image-identity', ['docker', 'image', 'inspect', '--format', '{{.Id}}', args.image])
        require(image_log.read_text().strip() == args.image, 'Cached image ID mismatch')
        command('create', ['docker', 'create', '--name', name, '--cpus', '4', '--memory', '8192m',
                '--memory-swap', '8192m', '--network', 'none', '--entrypoint', 'sleep', args.image, 'infinity'])
        command('start', ['docker', 'start', name])
        template = '{"image":{{json .Image}},"memory":{{.HostConfig.Memory}},"swap":{{.HostConfig.MemorySwap}},"cpus":{{.HostConfig.NanoCpus}},"network":{{json .HostConfig.NetworkMode}},"running":{{.State.Running}}}'
        _, inspection = command('limits', ['docker', 'inspect', '--format', template, name])
        limits = json.loads(inspection.read_text())
        require(limits == dict(image=args.image, memory=8192*1024*1024, swap=8192*1024*1024,
                               cpus=4000000000, network='none', running=True), 'Container limits differ')
        record['container_limits'] = limits
        command('copy-driver', ['docker', 'cp', str(frozen / 'case_inside_v2.py'), name + ':/tmp/focused_case.py'])
        command('copy-request', ['docker', 'cp', str(output / 'inputs.json'), name + ':/tmp/focused-request.json'])
        command('mkdir-tests', ['docker', 'exec', name, 'mkdir', '/tmp/focused-tests'])
        for filename in TESTS:
            command('copy-' + Path(filename).stem, ['docker', 'cp', str(frozen / filename), name + ':/tmp/focused-tests/' + filename])
        if args.patch:
            command('copy-patch', ['docker', 'cp', str(frozen / 'focused.patch'), name + ':/tmp/focused.patch'])
        if args.source == 'saved':
            command('copy-saved', ['docker', 'cp', str(frozen / 'saved'), name + ':/tmp/focused-source'])
        remaining = max(1, math.floor(deadline - time.monotonic()) - 5)
        result, _ = command('case', ['docker', 'exec', name, 'timeout', '-k', '3', str(remaining),
                                     'python3', '/tmp/focused_case.py'], check=False)
        record['case_exit_code'] = result.returncode
        record['status'] = 'complete'
    except BaseException as error:
        record.update(status='error', error_type=type(error).__name__, error=str(error))
    finally:
        # Teardown gets a separate bounded grace period; no container outlives a released claim.
        if claimed:
            command('collect-output', ['docker', 'cp', name + ':/tmp/focused-output', str(output / 'case')], check=False, cleanup=True)
            command('remove', ['docker', 'rm', '-f', name], check=False, cleanup=True)
            absent, listing = command('absence', ['docker', 'ps', '-aq', '--filter', 'name=^/' + name + '$'],
                                      check=False, cleanup=True)
            record['container_absent'] = absent.returncode == 0 and not listing.read_text().strip()
            if record['container_absent']:
                admission.release(name)
                with admission.locked() as (config, state):
                    record['claim_absent'] = name not in state['participants'][admission.key]['trials']
                    record['unmanaged_after'] = not managed(name, state)
                    counts = dict(shared_claim_count=sum(len(p['trials']) for p in state['participants'].values()),
                                  own_claim_count=len(state['participants'][admission.key]['trials']),
                                  shared_max_active=config['max_active'])
                event('released', **counts)
            else:
                record.update(status='cleanup_failed', claim_retained_for_recovery=True)
                event('claim_retained')
        try:
            record['inputs_unchanged'] = capture(args) == inputs
        except Exception as error:
            record['inputs_unchanged'] = False
            record['input_check_error'] = str(error)
        if not record['inputs_unchanged']:
            record['status'] = 'input_drift'
        if (output / 'case').exists():
            record['artifact_sha256'] = hashes(output / 'case')
            case_file = output / 'case/case.json'
            if case_file.exists():
                case = json.loads(case_file.read_text())
                record['case_outcome'] = case
                if case.get('error') and record['status'] == 'complete':
                    record['status'] = 'case_error'
        if record.get('case_exit_code') in (124, 137) and record['status'] == 'complete':
            record['status'] = 'timeout'
        record['finished_at'] = now()
        write(output / 'result.json', record)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if record['status'] == 'complete' and record.get('claim_absent') else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--image', default=DEFAULT_IMAGE)
    parser.add_argument('--source', choices=['gold', 'pristine', 'saved'], default='gold')
    parser.add_argument('--saved-source', type=Path)
    parser.add_argument('--patch', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--admission-timeout', type=int, default=3600)
    args = parser.parse_args()
    inputs = capture(args)
    if not args.run:
        print(json.dumps(dict(check_only=True, inputs=inputs), indent=2, sort_keys=True))
        return 0
    require(args.output is not None, '--run requires a fresh --output under harness/')
    with (HERE / 'focused.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return launch(args, inputs)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        raise SystemExit(f'Focused validation stopped: {type(error).__name__}: {error}')
