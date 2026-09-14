"""Exclude a registered infrastructure intervention, retaining the raw trial.

Registration is deliberate and exact-trial. It is not a heuristic for classifying
ordinary agent failures. This module never controls a process or edits evidence.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re

from benchmark_evidence import hash_file

ROOT = Path(__file__).resolve().parents[1]
KIND = 'mini_tool_pipe_drain_intervention'
IDENTITY_FILES = {'config.json', 'benchmark-runtime.json', 'agent/benchmark-agent-runtime.json'}
FINAL_FILES = {'result.json', 'benchmark-evidence.json', 'benchmark-deadline.json'}
EXPECTED_EXCEPTIONS = {'NonZeroAgentExitCodeError', 'AgentTimeoutError'}
MINI_RUN_SHA256 = '47f7f72e224b860180c0b6ff6ddb205fe52fe5e66f07145ce3d95c7375df8e6b'
MINI_LOCAL_SHA256 = '01dd33ae6be897458611911cc0eee1389092ea9fc6788c7eb24773e7c6342b1c'
GUARD_SHA256 = 'f4957411242676e6123948262506b7bd3a2f4d6fdaa610720d35aae75ffbc8b1'
HARBOR_INSTALLED_AGENT_SHA256 = '08fc5e92d21a0ca90f4edac7e8939dd6a7459cfca1adb70caea01475d9b077e7'
TRANSCRIPT_PATTERNS = [
    {'pattern': 'rate.?limit', 'flags': 34, 'exception_type': 'ApiRateLimitError'},
    {'pattern': 'too many requests', 'flags': 34, 'exception_type': 'ApiRateLimitError'},
]
# Add a proof only after its source and no-model reproduction are reviewed.
APPROVED_PROOFS = {'cec9db2632c4d42984da06ae18f9a6794c6970bc437a506ee93af365eb98c4ce'}


def _time(value):
    assert isinstance(value, str), 'Intervention timestamp must be explicit'
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    assert stamp.tzinfo is not None, 'Intervention timestamp lacks timezone'
    return stamp.timestamp()


def _number(value):
    assert type(value) in (int, float) and math.isfinite(value), 'Invalid numeric evidence'
    return value


def _path(root, value, area):
    assert isinstance(value, str), 'Missing evidence path'
    relative = Path(value)
    assert not relative.is_absolute() and '..' not in relative.parts
    assert relative.parts and relative.parts[0] == area, 'Evidence outside permitted area'
    path = root / relative
    assert path.resolve().is_relative_to(root / area), 'Evidence path escapes permitted area'
    return path


def _read(path, root, expected=None):
    digest = hash_file(path, root=root)
    assert expected is None or digest == expected, 'Intervention evidence hash changed'
    value = json.loads(path.read_text())
    assert hash_file(path, root=root) == digest, 'Intervention evidence changed during read'
    return value


def _linked(root, link):
    assert isinstance(link, dict) and set(link) == {'path', 'sha256'}
    path = _path(root, link['path'], 'research')
    return path, _read(path, root, link['sha256'])


def _process(value):
    assert isinstance(value, dict)
    assert type(value['pid']) is int and value['pid'] > 0
    assert isinstance(value['start_ticks'], str) and value['start_ticks'].isdigit()
    assert int(value['start_ticks']) > 0
    return value['pid'], value['start_ticks']


def _observation(root, entry, registered):
    _, observation = _linked(root, entry['observation'])
    assert observation['schema_version'] == 1 and observation['kind'] == 'mini_tool_pipe_drain_hang'
    assert observation['trial'] == entry['trial']
    assert _time(observation['observed_at']) <= registered
    assert observation['pid_namespace'] == 'trial_container'
    agent_pid, agent_start = _process(observation['agent'])
    guard_pid, guard_start = _process(observation['guard'])
    assert agent_pid != guard_pid
    assert re.fullmatch(r'/tmp/benchmark-guard-[0-9a-f]{32}', observation['guard']['phase'])
    assert observation['mini_local_sha256'] == MINI_LOCAL_SHA256
    assert observation['mini_run_sha256'] == MINI_RUN_SHA256
    assert observation['tool_timeout_sec'] == 30
    assert _number(observation['stalled_for_sec']) >= 138
    assert type(observation['recorded_turns']) is int and observation['recorded_turns'] > 0
    assert observation['pending_step'] == observation['recorded_turns'] + 1
    assert observation['files'] == entry['identity']['files']
    pipe = observation['pipe']
    assert type(pipe['inode']) is int and pipe['inode'] > 0
    assert type(pipe['agent_read_fd']) is int and pipe['agent_read_fd'] >= 0
    holders = pipe['holder_pids']
    assert isinstance(holders, list) and holders and len(holders) == len(set(holders))
    assert all(type(pid) is int and pid > 0 and pid not in (agent_pid, guard_pid) for pid in holders)
    _, snapshots = _linked(root, observation['observations'])
    assert isinstance(snapshots, list) and len(snapshots) >= 2
    times = [_time(s['observed_at']) for s in snapshots]
    assert times == sorted(set(times)) and times[-1] - times[0] >= 138
    assert times[-1] == _time(observation['observed_at'])
    starts = None
    cpu = None
    for snapshot in snapshots:
        assert snapshot['recorded_turns'] == observation['recorded_turns']
        assert snapshot['mini_local_sha256'] == MINI_LOCAL_SHA256
        assert snapshot['mini_run_sha256'] == MINI_RUN_SHA256
        assert snapshot['tool_timeout_sec'] == 30
        processes = {p['pid']: p for p in snapshot['processes']}
        assert len(processes) == len(snapshot['processes'])
        assert set(processes) == {agent_pid, guard_pid, *holders}
        identities = {pid: _process(p)[1] for pid, p in processes.items()}
        assert identities[agent_pid] == agent_start and identities[guard_pid] == guard_start
        assert all(p['state'] not in ('Z', 'X', 'x') for p in processes.values())
        assert starts is None or identities == starts, 'Observed process identity changed'
        starts = identities
        current_cpu = processes[agent_pid]['cpu_ticks']
        assert isinstance(current_cpu, list) and len(current_cpu) == 2
        assert cpu is None or current_cpu == cpu, 'Agent was making CPU progress'
        cpu = current_cpu
        assert processes[agent_pid]['pipes'][str(pipe['agent_read_fd'])] == f'pipe:[{pipe["inode"]}]'
        assert processes[agent_pid]['wait_channel'] == 'do_sys_poll'
        for pid in holders:
            assert processes[pid]['pipes']['1'] == processes[pid]['pipes']['2'] == f'pipe:[{pipe["inode"]}]'
            parent = pid
            seen = set()
            while parent != guard_pid:
                assert parent in processes and parent not in seen, 'Pipe holder is not guard-owned'
                seen.add(parent)
                parent = processes[parent]['parent_pid']
    return observation


def validate_registration(path, *, root=ROOT):
    """Read-only pre-action check; no authorization or process control is implied."""
    root = Path(root).resolve()
    path = Path(path)
    assert path.is_relative_to(root / 'research/benchmark-interventions')
    entry = _read(path, root)
    assert entry['schema_version'] == 1 and entry['kind'] == KIND
    assert entry['status'] in ('planned', 'finalized')
    assert set(entry['expected_exceptions']) == EXPECTED_EXCEPTIONS
    registered = _time(entry['registered_at'])
    trial = _path(root, entry['trial'], 'jobs')
    identity = entry['identity']
    assert trial.name == identity['trial_name'] and isinstance(identity['task_name'], str)
    assert re.fullmatch('[0-9a-f]{64}', identity['task_checksum'])
    assert set(identity['files']) == IDENTITY_FILES
    files = {name: _read(trial / name, root, sha) for name, sha in identity['files'].items()}
    config = files['config.json']
    assert config['trial_name'] == identity['trial_name']
    assert Path(config['task']['path']).name == identity['task_name']
    assert config['agent']['name'] == 'mini-swe-agent'
    assert config['agent']['kwargs']['version'] == '2.4.6'
    runtime = files['benchmark-runtime.json']
    assert runtime['version'] == 1 and runtime['scope'] == 'single-step-linux-docker'
    assert runtime['process_guard_sha256'] == GUARD_SHA256
    agent_runtime = files['agent/benchmark-agent-runtime.json']
    assert agent_runtime['preflight_passed'] is True
    assert agent_runtime['python'] == agent_runtime['requested_python'] == '3.12.11'
    assert agent_runtime['packages']['mini-swe-agent'] == '2.4.6'
    proof_path, proof = _linked(root, entry['proof'])
    assert entry['proof']['sha256'] in APPROVED_PROOFS, 'Unqualified intervention proof'
    assert proof['passed'] is True and type(proof['model_calls']) is int and proof['model_calls'] == 0
    assert proof['source_files_unchanged'] is True and proof['container_exit_code'] == 0
    assert proof['python_version'] == '3.12.11' and proof['upstream_default_tool_timeout_sec'] == 30
    assert proof['tests_run'] >= 9 and proof['failures'] == proof['errors'] == 0
    assert proof['source_sha256']['source/mini_local.py'] == MINI_LOCAL_SHA256
    assert proof['source_sha256']['source/benchmark_process_guard.py'] == GUARD_SHA256
    assert _time(proof['created_at']) <= registered
    for name, expected in proof['source_sha256'].items():
        relative = Path(name)
        assert not relative.is_absolute() and '..' not in relative.parts and relative.parts[0] == 'source'
        assert hash_file(proof_path.parent / relative, root=root) == expected, 'Reproduction source changed'
    observation = _observation(root, entry, registered)
    if entry['status'] == 'planned':
        assert entry['finalization'] is None
    return entry, observation, files


def _terminal_guard(deadline, observation, registered, finished):
    assert deadline['version'] == 1 and deadline['quiescent'] is True
    assert not deadline.get('cleanup_error') and deadline['helper_sha256'] == GUARD_SHA256
    executions = deadline['executions']
    assert isinstance(executions, list) and executions
    assert all(e['quiescent'] is True for e in executions)
    matches = [e for e in executions if e['pid'] == observation['guard']['pid']]
    assert len(matches) == 1, 'Missing exact observed guard execution'
    guard = matches[0]
    assert guard['identity'] == observation['guard']['start_ticks']
    assert guard['reason'] == 'cancelled' and guard['return_code'] == 124
    assert _number(guard['started_at_epoch']) <= _time(observation['observed_at'])
    assert registered <= _number(guard['stop_requested_at_epoch']) <= _number(guard['quiescent_at_epoch'])
    assert guard['quiescent_at_epoch'] <= _number(deadline['closed_at_epoch'])
    assert deadline['closed_at_epoch'] <= _number(deadline['cleanup_finished_at_epoch']) <= finished
    assert deadline['started_at_epoch'] <= guard['started_at_epoch']
    assert registered <= deadline['agent_run_finished_at_epoch'] <= deadline['cleanup_finished_at_epoch']


def _exception_audit(path, entry, observation, result, deadline, root):
    """Accept an explicitly audited transcript-derived alias of guard exit 124.

    The pre-action expectations remain immutable. A separate after-action audit
    proves why Harbor assigned this label to this one deliberately closed trial.
    """
    audit_path = path.with_name('exception-audit.json')
    audit = _read(audit_path, root)
    assert audit['schema_version'] == 1 and audit['kind'] == 'harbor_transcript_exception_mapping'
    assert audit['trial'] == entry['trial']
    assert audit['exception_type'] == 'ApiRateLimitError' and audit['actual_exit_code'] == 124
    assert _time(audit['audited_at']) >= _time(result['finished_at'])
    planned_path, planned = _linked(root, audit['planned_intervention'])
    assert planned_path != path and planned['status'] == 'planned' and planned['finalization'] is None
    assert {k: v for k, v in planned.items() if k not in ('status', 'finalization')} == {
        k: v for k, v in entry.items() if k not in ('status', 'finalization')}
    _, intent = _linked(root, audit['guard_close_intent'])
    assert intent['trial'] == entry['trial']
    assert intent['registry_sha256'] == audit['planned_intervention']['sha256']
    guard = next(e for e in deadline['executions'] if e['pid'] == observation['guard']['pid'])
    assert _time(entry['registered_at']) <= _time(intent['started_at']) <= guard['stop_requested_at_epoch']
    assert set(audit['files']) == {'result.json', 'benchmark-deadline.json', 'agent/mini-swe-agent.txt'}
    trial = root / entry['trial']
    for name, expected in audit['files'].items():
        assert hash_file(trial / name, root=root) == expected, 'Exception audit evidence changed'
    _, proof = _linked(root, audit['classifier_proof'])
    assert proof['schema_version'] == 1 and proof['passed'] is True
    assert type(proof['model_calls']) is int and proof['model_calls'] == 0
    assert proof['harbor_version'] == '0.15.0'
    assert proof['class_source_sha256'] == HARBOR_INSTALLED_AGENT_SHA256
    assert hashlib.sha256(proof['class_source'].encode()).hexdigest() == HARBOR_INSTALLED_AGENT_SHA256
    assert proof['patterns'] == TRANSCRIPT_PATTERNS
    synthetic = proof['synthetic_check']
    assert synthetic['return_code'] == 124 and synthetic['exception_type'] == 'ApiRateLimitError'
    assert synthetic['matched_pattern'] == 'rate.?limit'
    assert re.search('rate.?limit', synthetic['stdout'], re.IGNORECASE)
    assert result['exception_info']['exception_type'] == audit['exception_type']
    assert result['exception_info']['exception_message'].startswith('Command failed (exit 124): ')
    transcript = (trial / 'agent/mini-swe-agent.txt').read_text()
    matched = next((p for p in proof['patterns'] if re.search(p['pattern'], transcript, p['flags'])), None)
    assert matched == TRANSCRIPT_PATTERNS[0]
    assert audit['matched_pattern'] == matched['pattern']
    assert hash_file(trial / 'agent/mini-swe-agent.txt', root=root) == audit['files']['agent/mini-swe-agent.txt']
    return {'exception_audit': str(audit_path.relative_to(root)),
            'exception_audit_sha256': hash_file(audit_path, root=root)}


def classify_intervention(trial_dir, result, *, root=ROOT):
    """Classify only a terminal, safely closed registered interrupted attempt."""
    root = Path(root).resolve()
    trial_dir = Path(trial_dir).resolve()
    if not trial_dir.is_relative_to(root):
        return None  # No exact registration can name an unrelated trial root.
    relative = str(trial_dir.relative_to(root))
    matches = []
    for path in sorted((root / 'research/benchmark-interventions').glob('*/intervention.json')):
        entry = _read(path, root)
        assert isinstance(entry, dict), 'Invalid intervention registry entry'
        if entry.get('trial') == relative:
            matches.append(path)
    assert len(matches) <= 1, 'Duplicate intervention registrations'
    if not matches:
        return None
    path = matches[0]
    entry, observation, files = validate_registration(path, root=root)
    if not result.get('finished_at'):
        return None
    assert _read(trial_dir / 'result.json', root) == result, 'Intervention result differs from raw result'
    assert result['trial_name'] == entry['identity']['trial_name']
    assert result['task_name'] == entry['identity']['task_name']
    assert result['task_checksum'] == entry['identity']['task_checksum']
    assert result['config'] == files['config.json'], 'Intervention raw configuration differs'
    error = (result.get('exception_info') or {}).get('exception_type')
    finished = _time(result['finished_at'])
    registered = _time(entry['registered_at'])
    assert registered < finished, 'Intervention was registered after the trial finished'
    deadline = _read(trial_dir / 'benchmark-deadline.json', root)
    _terminal_guard(deadline, observation, registered, finished)
    exception_audit = {}
    if error not in EXPECTED_EXCEPTIONS:
        assert error == 'ApiRateLimitError', 'Unexpected interrupted agent exception'
        exception_audit = _exception_audit(path, entry, observation, result, deadline, root)
    if entry['status'] == 'finalized':
        final = entry['finalization']
        assert _time(final['finished_at']) == finished
        assert set(final['files']) == FINAL_FILES
        for name, expected in final['files'].items():
            assert hash_file(trial_dir / name, root=root) == expected, 'Final intervention evidence changed'
    return {'code': KIND, 'resolved': False, 'pending_finalization': entry['status'] == 'planned',
            'interrupted': True, 'intervention': str(path.relative_to(root)),
            'intervention_sha256': hash_file(path, root=root), **exception_audit}


def registry_cache_stamp(*, root=ROOT):
    """Include every followed proof/observation source, even after tampering."""
    root = Path(root).resolve()
    entries = []
    seen = set()

    def visit(path, *, follow=True):
        name = str(path.relative_to(root))
        if name in seen:
            return
        seen.add(name)
        try:
            digest = hash_file(path, root=root)
            entries.append((name, digest))
            if not follow or path.suffix != '.json':
                return
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                return
            if value.get('kind') == KIND:
                trial = _path(root, value['trial'], 'jobs')
                for artifact in sorted(IDENTITY_FILES | FINAL_FILES):
                    visit(trial / artifact, follow=False)
            if value.get('kind') == 'harbor_transcript_exception_mapping':
                trial = _path(root, value['trial'], 'jobs')
                visit(trial / 'agent/mini-swe-agent.txt', follow=False)
            for key in ('observation', 'observations', 'proof', 'planned_intervention',
                        'guard_close_intent', 'classifier_proof'):
                link = value.get(key)
                if isinstance(link, dict):
                    visit(_path(root, link['path'], 'research'))
            for source in value.get('source_sha256', {}):
                source_path = path.parent / source
                visit(_path(root, str(source_path.relative_to(root)), 'research'))
        except (AssertionError, OSError, ValueError, TypeError, AttributeError, KeyError, RuntimeError) as error:
            entries.append((name, type(error).__name__))

    for path in sorted((root / 'research/benchmark-interventions').glob('*/intervention.json')):
        visit(path)
        visit(path.with_name('exception-audit.json'))
    return tuple(entries)
