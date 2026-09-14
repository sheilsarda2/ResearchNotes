#!/usr/bin/env python3
"""Nine full, zero-model regrades. Default/check-only never uses Docker or admission."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
ORIGINAL_MANIFEST = BASE / 'paired-inputs/manifest.json'
ORIGINAL_SHA = '93f4f62c362feac77e1ce6ce82070aa52a8fc2e8a4b504a4ed337e5c6c0efc7d'
TOOLS = BASE / 'immutable-tooling-v1'
TOOLS_SHA = 'eea36c9e6aaa1fdee9cd2a53519ebefa29bb8bd664ef6e9f4c0f68d99f194079'
GROUPS = ['anti_cheat', 'build_a', 'build_b', 'lib_test_names', 'hidden_pr_tests',
          'robustness_tests', 'admin_timestamp_tests', 'interop_gold', 'parity_python',
          'zenoh_ext', 'upstream_regressions']
SRC_DIRS = ('commons/zenoh-protocol/src', 'commons/zenoh-codec/src', 'zenoh/src', 'zenoh-ext/src')
POOL = ROOT / 'jobs/candidate-campaigns-shared.control.json'


def require(value, message):
    if not value:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(path):
    path = Path(path)
    require(stat.S_ISREG(path.lstat().st_mode), 'Nonregular file: ' + str(path))
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temporary.replace(path)


def hashes(root):
    root = Path(root)
    require(stat.S_ISDIR(root.lstat().st_mode), 'Invalid root directory')
    result = {}
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), 'Symlink/special file: ' + str(path))
        if stat.S_ISREG(mode):
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def repository_path(text):
    path = Path(text)
    require(not path.is_absolute() and '..' not in path.parts, 'Expected repository-relative path')
    result = ROOT / path
    require(result.resolve().is_relative_to(ROOT), 'Path escapes workspace')
    return result


def source_map(record):
    require(record['source_matches_pre_verifier_capture'], 'Original source capture did not match')
    require(hashlib.sha256(canonical(record['source_files'])).hexdigest() == record['source_manifest_sha256'], 'Original source manifest digest differs')
    result = {}
    prefix = 'artifacts/submission/'
    for name, value in record['source_files'].items():
        require(name.startswith(prefix) and value['kind'] == 'file', 'Unexpected original source entry')
        relative = name[len(prefix):]
        require('..' not in Path(relative).parts and any(relative.startswith(p + '/') for p in SRC_DIRS), 'Uncollected source')
        result[relative] = value['sha256']
    require(result and all(any(name.startswith(p + '/') for name in result) for p in SRC_DIRS), 'Missing collected source root')
    return result


def validate_score(score, reward_text):
    require(score['required_groups'] == GROUPS, 'Wrong required group list')
    require(set(score['groups']) == set(GROUPS) and score['missing_groups'] == [], 'Incomplete full verifier')
    require(all(type(group.get('pass')) is bool for group in score['groups'].values()), 'Invalid group verdict')
    expected = int(all(group['pass'] for group in score['groups'].values()))
    require(type(score['reward']) in (int, float) and score['reward'] == expected, 'Reward/group disagreement')
    require(float(reward_text.strip()) == expected, 'reward.txt disagrees with score')
    return expected


def collect_stopped_container(name, artifacts, invoke):
    """Never delay removal to copy logs from a container whose stop is unproven."""
    stopped = False
    try:
        invoke('stop', ['docker', 'kill', name])
        code, log = invoke('stopped', ['docker', 'inspect', '--format', '{{.State.Running}}', name], 10)
        try:
            stopped = code == 0 and log is not None and log.read_text().strip() == 'false'
        except OSError:
            stopped = False
        if stopped:
            invoke('collect-output', ['docker', 'cp', name + ':/tmp/paired-output', str(artifacts / 'case')])
            invoke('collect-verifier', ['docker', 'cp', name + ':/logs/verifier', str(artifacts / 'verifier')])
    finally:
        invoke('remove', ['docker', 'rm', '-f', name])
    return stopped


def gate(args):
    """File-only gate. A reward-zero original is as eligible as a reward-one original."""
    require(digest(ORIGINAL_MANIFEST) == ORIGINAL_SHA, 'Original first-cell manifest drift')
    original = read(ORIGINAL_MANIFEST)
    require(original['passed'] and original['selected_cells'] == 9, 'Need all nine original cells')
    require(digest(TOOLS / 'manifest.json') == TOOLS_SHA, 'Captured tooling manifest drift')
    tooling = read(TOOLS / 'manifest.json')
    for name, expected in tooling['source_sha256'].items():
        require(digest(TOOLS / 'files' / name) == expected, 'Captured runtime source drift')
    final_manifest = read(args.task_manifest)
    task = repository_path(final_manifest['task'])
    task_files = hashes(task)
    require(task_files == final_manifest['task_file_sha256'], 'Final task files differ')
    from harbor.models.task.task import Task
    require(Task(task).checksum == final_manifest['harbor_task_checksum'], 'Final Harbor checksum differs')
    import tomllib
    config = tomllib.loads((task / 'task.toml').read_text())
    env = config['verifier']['environment']
    require(config['verifier']['timeout_sec'] == 3600 and env['cpus'] == 4 and env['memory_mb'] == 8192
            and env['storage_mb'] == 30720 and env['network_mode'] == 'no-network', 'Final verifier changed declared resources')
    old_task_inputs = ORIGINAL_MANIFEST.parent / original['task_inputs']['path']
    require(digest(old_task_inputs) == original['task_inputs']['sha256'], 'Original task input record drift')
    stimulus = read(old_task_inputs)['agent_facing_files']
    current_stimulus = {name: value for name, value in task_files.items()
                        if name in ('instruction.md', 'task.toml') or name.startswith('environment/')}
    require(current_stimulus == {name: row['sha256'] for name, row in stimulus.items()}, 'Final stimulus differs from original')
    controls = read(args.controls_summary)
    require(controls['passed'] and controls['inputs_unchanged'] and controls['tooling_unchanged'] and controls['model_calls'] == 0, 'Final controls not valid')
    for agent, reward in (('oracle', 1), ('nop', 0)):
        row = controls['controls'][agent]
        result_path = repository_path(row['result'])
        require(row['passed'] and row['reward'] == reward and row['task_checksum'] == final_manifest['harbor_task_checksum'], 'Wrong control identity/outcome')
        require(digest(result_path) == row['result_sha256'], 'Control raw result drift')
        result = read(result_path)
        require(result['task_checksum'] == row['task_checksum'] and result['config']['agent']['name'] == agent
                and result['config']['agent']['model_name'] is None and result['exception_info'] is None and result['finished_at'], 'Not a normal matching control')
        require(result['verifier_result']['rewards']['reward'] == reward, 'Control raw reward differs')
        for name, expected in row['trial_file_sha256'].items():
            require(digest(result_path.parent / name) == expected, 'Control artifact drift: ' + name)
        score = read(result_path.parent / 'verifier/score.json')
        require(validate_score(score, (result_path.parent / 'verifier/reward.txt').read_text()) == reward, 'Wrong control score')
    diagnostics = read(args.diagnostics_summary)
    require(diagnostics['passed'] and diagnostics['model_calls'] == 0 and diagnostics['counted_sweep_trials'] == 0, 'Omission probes incomplete')
    require(diagnostics['controls_summary_sha256'] == digest(args.controls_summary), 'Probes validated different controls')
    mutants = [row for row in diagnostics['cases'] if row['kind'] == 'single_omission_probe']
    frozen_mutants = read(BASE / 'mutants/manifest.json')
    require(diagnostics['mutant_manifest_sha256'] == digest(BASE / 'mutants/manifest.json'), 'Mutant manifest changed')
    require({row['name'] for row in mutants} == {row['id'] for row in frozen_mutants['mutants']} and len(mutants) == 6, 'Need six distinct omission probes')
    for row in mutants:
        require(row['omission_detected'] is True and row['other_tests_passed'] is True
                and row['case_exit_code'] == 101, 'Omission escaped intended runtime assertion')
        probe_path = repository_path(row['result'])
        require(digest(probe_path) == row['result_sha256'], 'Probe result drift')
        probe = read(probe_path)
        require(probe['status'] == 'complete' and probe['case_exit_code'] == 101 and probe['container_absent']
                and probe['claim_absent'] and probe['inputs_unchanged'] and probe['model_calls'] == 0, 'Probe execution incomplete')
        for name, expected in probe['artifact_sha256'].items():
            require(digest(probe_path.parent / 'case' / name) == expected, 'Probe artifact drift')
        text = (probe_path.parent / 'case/cargo.stdout.log').read_text()
        require('error[E' not in text and all(signature in text for signature in row['expected_assertion_signatures']),
                'Probe runtime assertion signature missing')
    image = args.verifier_image_id
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', image), 'Pin the exact verifier image ID')
    proof = read(args.verifier_image_proof)
    oracle = controls['controls']['oracle']
    require(oracle['verifier_image_proof']['sha256'] == digest(args.verifier_image_proof)
            and repository_path(oracle['verifier_image_proof']['path']).resolve() == args.verifier_image_proof.resolve(),
            'Image proof is not the one bound by the normal oracle control')
    require(proof['passed'] and proof['kind'] == 'normal_harbor_oracle_verifier_image' and proof['verifier_image_id'] == image, 'Wrong image proof')
    require(proof['oracle_result_path'] == oracle['result'] and proof['oracle_result_sha256'] == oracle['result_sha256']
            and proof['final_task_checksum'] == final_manifest['harbor_task_checksum'], 'Image proof not bound to exact oracle')
    observed = proof['observed_host_config']
    inspection_path = repository_path(proof['raw_docker_inspection_path'])
    require(digest(inspection_path) == proof['raw_docker_inspection_sha256'], 'Oracle image inspection drift')
    inspection = read(inspection_path)
    require(inspection['Image'] == image and inspection['HostConfig'] == observed, 'Image proof/inspection disagreement')
    expected_project = Path(oracle['result']).parent.name.lower() + '__verifier__trial'
    require(inspection['Config.Labels']['com.docker.compose.project'] == expected_project, 'Inspection is not the oracle verifier')
    require(observed['NanoCpus'] == 4000000000 and observed['Memory'] == 8192 * 1024 * 1024 and observed['NetworkMode'] == 'none', 'Oracle observed resources differ')
    require('StorageOpt' in observed, 'Missing truthful storage observation')
    require(type(observed.get('MemorySwap')) is int, 'Missing swap setting observation')
    require(observed['StorageOpt'] in (None, {}), 'Configured storage quota requires explicit matching support')
    records = []
    for row in original['trials']:
        path = ORIGINAL_MANIFEST.parent / row['record']
        require(digest(path) == row['sha256'], 'Original input record drift')
        record = read(path)
        require(record['trial'] == row['trial'] and record['first_counted'], 'Wrong original trial')
        trial = repository_path(record['trial'])
        require(digest(trial / 'result.json') == row['result_sha256'] == record['raw_files']['result.json']['sha256'], 'Original result drift')
        require(digest(trial / 'benchmark-evidence.json') == row['evidence_sha256'], 'Original evidence drift')
        expected = source_map(record)
        require(hashes(trial / 'artifacts/submission') == expected, 'Original source drift')
        for name in ('benchmark-snapshot.json', 'agent/benchmark-agent-input.json', 'agent/mini-swe-agent.trajectory.json', 'config.json'):
            require(digest(trial / name) == record['raw_files'][name]['sha256'], 'Original stimulus/capture record drift')
        require(record['agent_input_marker']['task_sha256'] == current_stimulus['instruction.md']
                and record['first_user_prompt']['contains_exact_instruction'], 'Delivered instruction differs')
        records.append({'record_path': str(path.relative_to(ROOT)), 'record_sha256': row['sha256'],
                        'original': record, 'source': str(trial / 'artifacts/submission'), 'source_files': expected})
    require(len(records) == 9 and len({(r['original']['model'], r['original']['effort']) for r in records}) == 9, 'Duplicate/missing cells')
    return {'task': {'path': str(task.relative_to(ROOT)), 'harbor_task_checksum': final_manifest['harbor_task_checksum'],
                     'task_manifest_sha256': digest(args.task_manifest), 'task_file_sha256': task_files},
            'test_file_sha256': {name: value for name, value in task_files.items() if name.startswith('tests/')},
            'records': records, 'control_summary_sha256': digest(args.controls_summary),
            'diagnostics_summary_sha256': digest(args.diagnostics_summary), 'verifier_image_proof_sha256': digest(args.verifier_image_proof),
            'oracle_observed_host_config': observed, 'verifier_image_id': image,
            'original_input_manifest': {'path': str(ORIGINAL_MANIFEST.relative_to(ROOT)), 'sha256': ORIGINAL_SHA},
            'tooling_manifest_sha256': TOOLS_SHA, 'tooling_file_sha256': tooling['source_sha256'],
            'harness_file_sha256': {p.name: digest(p) for p in (Path(__file__), HERE / 'case_inside.py')},
            'declared_limits': {'cpus': 4, 'memory_mb': 8192, 'storage_mb': 30720, 'network_mode': 'no-network',
                                'verifier_timeout_seconds': 3600, 'local_max_active': 1, 'shared_max_active': 14}}


def run_case(index, record, output, inputs, args):
    destination = output / ('%02d-' % index + Path(record['original']['trial']).name.split('__')[-1])
    destination.mkdir()
    artifacts = destination / 'artifacts'
    artifacts.mkdir()
    staged = destination / 'source-capture'
    require(hashes(record['source']) == record['source_files'], 'Original source changed before copy')
    shutil.copytree(record['source'], staged)
    require(hashes(staged) == record['source_files'], 'Staged source differs')
    request = {'source_file_sha256': record['source_files'], 'test_file_sha256': inputs['test_file_sha256']}
    write(destination / 'request.json', request)
    shutil.copyfile(HERE / 'case_inside.py', destination / 'case_inside.py')
    require(digest(destination / 'case_inside.py') == inputs['harness_file_sha256']['case_inside.py'], 'Driver changed')
    control = destination / 'control.json'
    local = {'max_active': 1, 'min_total_mb': 30000, 'reserve_mb': 4096, 'startup_reserve_mb': 1536,
             'startup_window_sec': 120, 'start_interval_sec': 5, 'max_memory_pressure_pct': 1.0,
             'pressure_cooldown_sec': 60, 'shared_pool': str(POOL)}
    write(control, local)
    sys.path.insert(0, str(TOOLS / 'files/scripts'))
    from benchmark_shared_admission import SharedAdmission
    from benchmark_recovery import memory_snapshot
    from benchmark_interleaving import managed
    admission = SharedAdmission(POOL, control)
    name = 'zenoh-paired-' + uuid.uuid4().hex[:16]
    original = record['original']
    result = {'schema_version': 1, 'kind': 'paired_verifier_regrade', 'full_regrade': True,
              'model_calls': 0, 'counted_sweep_trial': False, 'started_at': now(), 'status': 'waiting',
              'original_trial_path': original['trial'], 'original_input_record_sha256': record['record_sha256'],
              'original_input_record_path': record['record_path'],
              'original_result_sha256': original['raw_files']['result.json']['sha256'],
              'original_task_checksum': original['original_task_checksum'], 'original_reward': original['reward'],
              'submitted_source_manifest_sha256': original['source_manifest_sha256'],
              'final_task_checksum': inputs['task']['harbor_task_checksum'], 'verifier_image_id': args.verifier_image_id,
              'declared_limits': inputs['declared_limits'], 'limits': inputs['declared_limits'],
              'container_name': name, 'admission_key': admission.key, 'reward': None,
              'required_groups': GROUPS, 'groups': {}, 'lifecycle': [], 'validation_passed': False}
    claimed = False
    command_count = 0

    def event(kind, **fields):
        result['lifecycle'].append({'at': now(), 'event': kind, **fields})
        write(destination / 'result.json', result)

    def command(label, argv, timeout, check=True):
        nonlocal command_count
        command_count += 1
        log = artifacts / ('%02d-' % command_count + label + '.log')
        try:
            with log.open('xb') as stream:
                completed = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=max(.1, timeout))
            code = completed.returncode
        except subprocess.TimeoutExpired:
            code = 124
        except OSError:
            code = 125
        event('command', label=label, returncode=code, log=str(log.relative_to(ROOT)), sha256=digest(log))
        if check:
            require(code == 0, label + ' failed; see ' + str(log))
        return code, log

    def owns_claim():
        with admission.locked() as (_, state):
            return name in state['participants'].get(admission.key, {}).get('trials', {})

    try:
        event('registered')
        waiting, notice = time.monotonic(), 0
        while True:
            require(time.monotonic() - waiting < args.admission_timeout, 'Admission wait deadline')
            with admission.locked() as (config, state):
                require(config['max_active'] == 14 and not managed(name, state), 'Wrong pool/interleaving scope')
            reason = admission.try_acquire(name, memory_snapshot(), local)
            if reason is None:
                claimed = True
                with admission.locked() as (config, state):
                    counts = {'own_claims': len(state['participants'][admission.key]['trials']),
                              'shared_claims': sum(len(p['trials']) for p in state['participants'].values()),
                              'external_trials': state.get('external_trials', 0), 'shared_cap': config['max_active']}
                require(counts['own_claims'] == 1 and counts['shared_claims'] + counts['external_trials'] <= 14,
                        'Admission claim count exceeds declared limit')
                event('claimed', **counts)
                break
            if time.monotonic() - notice >= 30:
                print(now(), name, 'waiting:', reason, flush=True)
                event('waiting', reason=reason)
                notice = time.monotonic()
            time.sleep(2)
        require(gate(args) == inputs, 'Input drift while queued')
        setup_deadline = time.monotonic() + 180
        def setup(label, argv):
            require(time.monotonic() < setup_deadline, 'Replay setup deadline exceeded')
            return command(label, argv, min(60, setup_deadline - time.monotonic()))
        swap = inputs['oracle_observed_host_config']['MemorySwap']
        swap_args = ['--memory-swap', str(swap)] if swap != 0 else []
        setup('create', ['docker', 'create', '--name', name, '--cpus', '4', '--memory', '8192m',
                        *swap_args, '--network', 'none', '--entrypoint', 'sleep', args.verifier_image_id, 'infinity'])
        setup('start', ['docker', 'start', name])
        template = '{"image":{{json .Image}},"memory":{{.HostConfig.Memory}},"swap":{{.HostConfig.MemorySwap}},"cpus":{{.HostConfig.NanoCpus}},"network":{{json .HostConfig.NetworkMode}},"storage_opt":{{json (index .HostConfig "StorageOpt")}},"running":{{.State.Running}}}'
        _, inspection = setup('inspect-limits', ['docker', 'inspect', '--format', template, name])
        observed = json.loads(inspection.read_text())
        require(observed['image'] == args.verifier_image_id and observed['running']
                and observed['cpus'] == 4000000000 and observed['memory'] == 8192 * 1024 * 1024
                and observed['swap'] == swap and observed['network'] == 'none', 'Wrong actual container limits')
        require((observed['storage_opt'] or {}) == (inputs['oracle_observed_host_config']['StorageOpt'] or {}), 'Storage differs from oracle')
        result['observed_limits'] = {'cpus': 4, 'memory_mb': 8192, 'network_mode': 'none',
                                     'storage_mb': None, 'storage_opt': observed['storage_opt'],
                                     'verifier_timeout_seconds': 3600, 'memory_swap_bytes': observed['swap'],
                                     'storage_note': 'No explicit StorageOpt quota, matching normal Harbor oracle; declared 30720 MB is not claimed as an enforced Docker quota.'}
        setup('source-root', ['docker', 'exec', name, 'mkdir', '-p', '/workspace/repo'])
        setup('copy-source', ['docker', 'cp', str(staged) + '/.', name + ':/workspace/repo'])
        setup('copy-request', ['docker', 'cp', str(destination / 'request.json'), name + ':/tmp/paired-request.json'])
        setup('copy-driver', ['docker', 'cp', str(destination / 'case_inside.py'), name + ':/tmp/paired-case.py'])
        result['status'] = 'running'
        event('full-verifier-start')
        code, _ = command('full-verifier', ['docker', 'exec', name, 'python3', '-B', '/tmp/paired-case.py'], 3630, check=False)
        result['driver_exit_code'] = code
        require(code == 0, 'Full verifier driver failed/timed out')
        result['status'] = 'workload_finished'
    except BaseException as error:
        result.update(status='error', error_type=type(error).__name__, error=str(error))
    finally:
        cleanup_errors = []
        try:
            claimed = claimed or owns_claim()
        except BaseException as error:
            cleanup_errors.append('ownership_lookup:' + type(error).__name__)
        result['cleanup'] = {'container_absent': False, 'claim_absent': False}
        if claimed:
            cleanup_deadline = time.monotonic() + 120
            # Confirm workload stopped before copying logs. If stop cannot be
            # proven, skip collection and attempt removal immediately.
            def cleanup_command(label, argv, cap=20):
                try:
                    code, log = command(label, argv, min(cap, max(.1, cleanup_deadline - time.monotonic())), check=False)
                    if code != 0:
                        cleanup_errors.append(label + ':' + str(code))
                    return code, log
                except BaseException as error:
                    cleanup_errors.append(label + ':' + type(error).__name__)
                    return 125, None
            stopped = collect_stopped_container(name, artifacts, cleanup_command)
            result['cleanup']['stopped_before_collection'] = stopped
            if not stopped:
                cleanup_errors.append('collection_skipped_unconfirmed_stop')
            try:
                code, listing = command('absence', ['docker', 'ps', '-aq', '--filter', 'name=^/' + name + '$'],
                                        min(20, max(.1, cleanup_deadline - time.monotonic())), check=False)
                result['cleanup']['container_absent'] = code == 0 and not listing.read_text().strip()
                if result['cleanup']['container_absent']:
                    admission.release(name)
                    result['cleanup']['claim_absent'] = not owns_claim()
                    event('released')
            except BaseException as error:
                cleanup_errors.append('absence_or_release:' + type(error).__name__)
        else:
            # No container operation occurs before a claim is acquired.
            result['cleanup']['container_absent'] = True
            try:
                result['cleanup']['claim_absent'] = not owns_claim()
            except BaseException as error:
                cleanup_errors.append('unadmitted_claim_check:' + type(error).__name__)
        result['cleanup']['errors'] = cleanup_errors
        if not all(result['cleanup'][key] for key in ('container_absent', 'claim_absent')):
            result['status'] = 'cleanup_failed'
            result['claim_retained_for_recovery'] = True
        try:
            result['original_source_unchanged'] = hashes(record['source']) == record['source_files']
            require(result['original_source_unchanged'] and gate(args) == inputs, 'Original/final input drift')
            raw = read(artifacts / 'case/raw-result.json')
            require(not raw.get('error') and not raw['timed_out'] and raw['verifier_exit_code'] == 0, 'Full verifier did not finish normally')
            for field, filename, expected in (
                ('source_file_sha256_before', 'source-before.json', record['source_files']),
                ('source_file_sha256_after', 'source-after.json', record['source_files']),
                ('test_file_sha256_before', 'tests-before.json', inputs['test_file_sha256']),
                ('test_file_sha256_after', 'tests-after.json', inputs['test_file_sha256']),
            ):
                result[field] = read(artifacts / 'case' / filename)
                require(result[field] == expected, 'Copied full-grader input mismatch: ' + field)
            result['source_files_unchanged'] = True
            result['test_files_unchanged'] = True
            score_path = artifacts / 'verifier/score.json'
            score = read(score_path)
            result['reward'] = validate_score(score, (artifacts / 'verifier/reward.txt').read_text())
            result.update(groups=score['groups'], score_path=str(score_path.relative_to(ROOT)), score_sha256=digest(score_path))
            result['validation_passed'] = result['status'] == 'workload_finished' and not cleanup_errors
            if result['validation_passed']:
                result['status'] = 'complete'
        except BaseException as error:
            result.update(validation_error_type=type(error).__name__, validation_error=str(error))
        result.update(finished_at=now(), artifact_root=str(destination.relative_to(ROOT)))
        write(destination / 'result.json', result)
    result_path = destination / 'result.json'
    return {**result, 'result_path': str(result_path.relative_to(ROOT)), 'result_sha256': digest(result_path),
            'artifact_file_sha256': hashes(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-manifest', type=Path, required=True)
    parser.add_argument('--controls-summary', type=Path, required=True)
    parser.add_argument('--diagnostics-summary', type=Path, required=True)
    parser.add_argument('--verifier-image-id', required=True)
    parser.add_argument('--verifier-image-proof', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--admission-timeout', type=int, default=21600)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    inputs = gate(args)
    if not args.run:
        print(json.dumps({'check_only': True, 'passed': True, 'model_calls': 0, 'docker_calls': 0,
                          'task': inputs['task'], 'original_trials': [r['original']['trial'] for r in inputs['records']],
                          'verifier_image_id': args.verifier_image_id, 'declared_limits': inputs['declared_limits']}, indent=2))
        return
    require(Path('/proc/self/stat').exists(), 'Run only in the existing Linux devcontainer')
    require(args.output is not None, '--run requires a fresh output path')
    output = args.output.resolve()
    require(output.is_relative_to(HERE) and output != HERE and not output.exists(), 'Never overwrite or leave paired-regrades/')
    with (HERE / 'queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output.mkdir()
        write(output / 'inputs.json', inputs)
        summary = {'schema_version': 1, 'kind': 'paired_verifier_regrades', 'started_at': now(),
                   'validation_passed': False, 'all_regrades_complete': False, 'model_calls': 0,
                   'counted_sweep_trials': 0, 'task': inputs['task'], 'original_input_manifest': inputs['original_input_manifest'],
                   'inputs_path': str((output / 'inputs.json').relative_to(ROOT)), 'inputs_sha256': digest(output / 'inputs.json'),
                   'controls_summary': str(args.controls_summary.resolve().relative_to(ROOT)),
                   'controls_summary_sha256': inputs['control_summary_sha256'],
                   'diagnostics_summary': str(args.diagnostics_summary.resolve().relative_to(ROOT)),
                   'diagnostics_summary_sha256': inputs['diagnostics_summary_sha256'],
                   'verifier_image_proof': str(args.verifier_image_proof.resolve().relative_to(ROOT)),
                   'verifier_image_proof_sha256': inputs['verifier_image_proof_sha256'], 'regrades': []}
        write(output / 'summary.json', summary)
        def interrupted(signum, frame):
            raise KeyboardInterrupt('Controller interrupted by signal ' + str(signum))
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        for index, record in enumerate(inputs['records'], 1):
            print(now(), 'Full paired regrade', index, '/ 9:', record['original']['trial'], flush=True)
            result = run_case(index, record, output, inputs, args)
            summary['regrades'].append(result)
            write(output / 'summary.json', summary)
            if not result['validation_passed']:
                summary['stopped_on_incomplete_regrade'] = result['original_trial_path']
                break
        summary.update(finished_at=now(), all_regrades_complete=len(summary['regrades']) == 9 and all(r['validation_passed'] for r in summary['regrades']))
        summary['validation_passed'] = summary['all_regrades_complete'] and gate(args) == inputs
        summary['meaning'] = 'Execution and provenance completeness only. Rewards are separate; zero-reward complete regrades count as complete.'
        write(output / 'summary.json', summary)
        print(json.dumps({'summary': str((output / 'summary.json').relative_to(ROOT)), 'validation_passed': summary['validation_passed'],
                          'regrades': len(summary['regrades']), 'rewards': [r['reward'] for r in summary['regrades']]}), flush=True)
        if not summary['validation_passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
