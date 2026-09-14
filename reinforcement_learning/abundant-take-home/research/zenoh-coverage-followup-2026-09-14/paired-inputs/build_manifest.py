#!/usr/bin/env python3
"""Freeze provenance for original Zenoh first cells; never run or regrade a trial."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
import subprocess

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
CAMPAIGN = 'candidates-zenoh-v3-efforts-20-20260913T221317Z'
TASK_ID = 'rs-zenoh-timestamp-instrumentation-v3'
TASK = ROOT / 'research/task-revisions' / TASK_ID
COUNTED = {'scored', 'timeout', 'verifier_timeout'}
COLLECTED = ('commons/zenoh-protocol/src', 'commons/zenoh-codec/src',
             'zenoh/src', 'zenoh-ext/src')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    assert stat.S_ISREG(path.lstat().st_mode), 'Non-regular file: ' + str(path)
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def metadata(path):
    return {'kind': 'file', 'sha256': digest(path), 'size': path.stat().st_size}


def tree(root):
    assert stat.S_ISDIR(root.lstat().st_mode)
    result = {}
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        assert stat.S_ISDIR(mode) or stat.S_ISREG(mode), str(path)
        if stat.S_ISREG(mode):
            result[path.relative_to(root).as_posix()] = metadata(path)
    return result


def load(path):
    return json.loads(path.read_bytes())


def write_once(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True).encode() + b'\n')


def archive(path):
    content = path.read_bytes()
    target = OUT / 'sources' / (sha(content) + '.json')
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        assert target.read_bytes() == content
    else:
        with target.open('xb') as stream:
            stream.write(content)
    return json.loads(content), {'original': path.relative_to(ROOT).as_posix(),
                               'snapshot': target.relative_to(ROOT).as_posix(),
                               'sha256': sha(content), 'bytes': len(content)}


def cell(row):
    model = row['model']
    if not model.startswith('anthropic/'):
        model = 'anthropic/claude-' + model
    return row['task'], model, row['effort']


def timestamp(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    assert value.tzinfo is not None
    return value.astimezone(timezone.utc)


def verify_snapshot(trial, snapshot, files):
    payload = {k: v for k, v in snapshot.items() if k != 'sha256'}
    assert sha(canonical(payload)) == snapshot['sha256']
    actual = {}
    for name in snapshot['paths']:
        path = trial / name
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        if not path.exists():
            actual[name] = {'kind': 'missing'}
        elif path.is_file():
            actual[name] = files[name]
        else:
            actual[name] = {'kind': 'directory'}
            for item in sorted(path.rglob('*')):
                key = item.relative_to(trial).as_posix()
                actual[key] = files[key] if item.is_file() else {'kind': 'directory'}
    assert actual == snapshot['entries'], 'Snapshot differs: ' + str(trial)


def supplied_preservation():
    prefix = subprocess.check_output(['git', 'rev-parse', '--show-prefix'], cwd=ROOT, text=True).strip()
    repo = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=ROOT, text=True).strip())
    names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', '-z', 'c1ae968', '--', prefix], cwd=repo).decode().split('\0')
    names = [name for name in names if name]
    assert len(names) == 36
    for name in names:
        assert (ROOT / name[len(prefix):]).read_bytes() == subprocess.check_output(['git', 'show', 'c1ae968:' + name], cwd=repo)
    return {'commit': 'c1ae968', 'files_checked': len(names), 'all_unchanged': True}


def main():
    assert not (OUT / 'manifest.json').exists(), 'Never replace a frozen paired-input manifest'
    frozen_data_sha = digest(ROOT / 'research/takehome-presentation-2026-09-14/data/results.json')
    summary, summary_source = archive(ROOT / 'jobs' / (CAMPAIGN + '.summary.json'))
    plan, plan_source = archive(ROOT / 'jobs' / (CAMPAIGN + '.plan.json'))
    scope, scope_source = archive(ROOT / 'research/takehome-presentation-2026-09-14/coverage-scope.json')
    frozen, frozen_source = archive(ROOT / 'research/zenoh-test-fix-2026-09-13/frozen-task-manifest.json')
    assert summary['job'] == CAMPAIGN
    definition = next(t for t in plan['tasks'] if t['id'] == TASK_ID)
    assert definition == next(t for t in frozen['tasks'] if t['id'] == TASK_ID)
    task_files = tree(TASK)
    hashes = {name: row['sha256'] for name, row in task_files.items()}
    directory_digest = sha(json.dumps(hashes, sort_keys=True).encode())
    assert directory_digest == definition['validated_task_sha256']
    task_checksum = definition['validated_harbor_task_checksum']
    instruction = (TASK / 'instruction.md').read_text()
    jobs = {job['name']: job for job in plan['jobs']}
    for job in jobs.values():
        assert digest(ROOT / job['config']) == job['sha256']
    expected_cells = {cell(row) for row in scope['cells'].values()
                      if row['job'] == CAMPAIGN and row['task'] == TASK_ID}
    assert len(expected_cells) == 9
    assert expected_cells == {(TASK_ID, 'anthropic/claude-' + model, effort)
                              for model in plan['models'] for effort in plan['efforts']}
    candidates = defaultdict(list)
    excluded = []
    raw_rows = []
    for row in summary['trials']:
        assert cell(row) in expected_cells
        trial = ROOT / row['trial']
        assert trial.parent.name in jobs and trial.parent.name == CAMPAIGN
        raw = load(trial / 'result.json')
        assert digest(trial / 'result.json') == row['result_sha256']
        assert raw['finished_at'] == row['finished_at']
        assert raw['task_checksum'] == task_checksum
        configured = raw['config']['agent']
        assert cell({'task': raw['task_name'], 'model': configured['model_name'],
                     'effort': configured['kwargs']['reasoning_effort']}) == cell(row)
        raw_rows.append({k: row[k] for k in ('trial', 'model', 'effort', 'status', 'finished_at', 'result_sha256')})
        if row['status'] in COUNTED:
            candidates[cell(row)].append(row)
        else:
            excluded.append(raw_rows[-1])
    for setting in summary['settings']:
        assert len(candidates[cell(setting)]) == setting['completed']
    assert set(candidates) == expected_cells
    selected = [min(candidates[key], key=lambda r: (timestamp(r['finished_at']), r['trial']))
                for key in sorted(expected_cells)]
    records = []
    missing_optional_markers = []
    for row in selected:
        trial = ROOT / row['trial']
        files = tree(trial)
        raw = load(trial / 'result.json')
        evidence = load(trial / 'benchmark-evidence.json')
        snapshot = load(trial / 'benchmark-snapshot.json')
        assert files['result.json']['sha256'] == row['result_sha256']
        assert files['benchmark-evidence.json']['sha256'] == row['benchmark_evidence_sha256']
        assert evidence['identity']['task_checksum'] == raw['task_checksum'] == task_checksum
        assert evidence['pre_verifier_snapshot_check']['matches'] is True
        assert evidence['pre_verifier_snapshot_check']['expected_sha256'] == snapshot['sha256']
        assert evidence['pre_verifier_snapshot_check']['observed_sha256'] == snapshot['sha256']
        verify_snapshot(trial, snapshot, files)
        verify_snapshot(trial, evidence['input_snapshot'], files)
        source_files = {name: value for name, value in files.items() if name.startswith('artifacts/submission/')}
        expected_sources = {name: value for name, value in snapshot['entries'].items()
                            if name.startswith('artifacts/submission/') and value['kind'] == 'file'}
        assert source_files == expected_sources and source_files
        assert all(any(name.startswith('artifacts/submission/' + directory + '/') for directory in COLLECTED)
                   for name in source_files)
        assert all(any(name.startswith('artifacts/submission/' + directory + '/') for name in source_files)
                   for directory in COLLECTED)
        config = load(trial / 'config.json')
        assert config == raw['config']
        assert config['task']['path'] == definition['path']
        assert config['timeout_multiplier'] == 1 and not config.get('extra_instruction_paths')
        assert all(value is None for key, value in config.items() if key.endswith('_timeout_multiplier'))
        assert all(value is None for key, value in config['environment'].items() if key.startswith('override_'))
        assert not config['environment'].get('mounts') and not config['environment'].get('extra_docker_compose')
        agent_config = config['agent']
        assert not agent_config.get('skills') and not agent_config.get('mcp_servers')
        assert all(agent_config.get(key) is None for key in ('override_timeout_sec', 'max_timeout_sec', 'override_setup_timeout_sec'))
        raw_reward = raw['verifier_result']['rewards']['reward']
        assert row['status'] == 'scored' and raw_reward == row['reward'] == row['raw_reward']
        assert float((trial / 'verifier/reward.txt').read_text()) == raw_reward
        score = load(trial / 'verifier/score.json')
        assert score['reward'] == raw_reward and score['required_groups'] == definition['expected_verifier_groups']
        assert not score['missing_groups'] and len(score['groups']) == 9
        assert sum(g['pass'] is True for g in score['groups'].values()) == row['checks_passed']
        native = load(trial / 'agent/mini-swe-agent.trajectory.json')
        atif = load(trial / 'agent/trajectory.json')
        steps = [step for step in atif['steps'] if step.get('source') == 'agent']
        assert len(steps) == row['assistant_steps'] == evidence['counts']['atif_agent_steps']
        assert sum(len(step.get('tool_calls') or []) for step in steps) == row['tool_calls']
        assert sum(m.get('role') == 'assistant' for m in native['messages']) == evidence['counts']['native_assistant_turns']
        index, first = next((i, m) for i, m in enumerate(native['messages']) if m.get('role') == 'user')
        assert isinstance(first['content'], str) and instruction in first['content']
        prompt = first['content'].encode()
        input_marker = load(trial / 'agent/benchmark-agent-input.json')
        assert input_marker['delivery'] == 'final_run_task_config'
        assert input_marker['task_sha256'] == hashes['instruction.md']
        assert input_marker['task_utf8_bytes'] == len(instruction.encode())
        runtime = load(trial / 'agent/benchmark-agent-runtime.json')
        tool_marker = trial / 'agent/benchmark-agent-tool-runtime.json'
        tools = load(tool_marker) if tool_marker.exists() else None
        assert runtime['preflight_passed'] is True
        if tools is not None:
            assert tools['preflight_passed'] is True
        else:
            missing_optional_markers.append({'trial': row['trial'],
                                             'missing': 'agent/benchmark-agent-tool-runtime.json',
                                             'effect': 'Cannot attest to the later tool-runtime preflight for this original trial; saved source and delivered instruction remain verifiable.'})
        weaknesses = ['Immutable agent image ID is not established by inspected result/config/runtime markers; task file and declared configuration equality does not prove byte-identical installed runtime.']
        if tools is None:
            weaknesses.append('The later tool-runtime preflight marker is absent; no tool preflight is inferred.')
        record = {k: row[k] for k in ('trial', 'model', 'effort', 'status', 'finished_at', 'reward', 'raw_reward',
                                      'assistant_steps', 'tool_calls', 'checks_passed', 'checks_total')}
        record.update(original_task_checksum=task_checksum, first_counted=True,
                      task_input_manifest='task-inputs.json', source_file_count=len(source_files),
                      source_files=source_files, source_manifest_sha256=sha(canonical(source_files)),
                      source_matches_pre_verifier_capture=True, whole_pre_verifier_snapshot_matches=True,
                      post_verifier_input_snapshot_matches=True, raw_files=files,
                      pre_verifier_snapshot_payload_sha256=snapshot['sha256'],
                      post_verifier_input_snapshot_payload_sha256=evidence['input_snapshot']['sha256'],
                      first_user_prompt={'trajectory': 'agent/mini-swe-agent.trajectory.json', 'message_index': index,
                                         'utf8_bytes': len(prompt), 'sha256': sha(prompt), 'contains_exact_instruction': True},
                      agent_input_marker=input_marker,
                      declared_agent={'name': agent_config['name'], 'model_name': agent_config['model_name'],
                                      'reasoning_effort': agent_config['kwargs']['reasoning_effort'],
                                      'version': agent_config['kwargs']['version'], 'timeout_multiplier': 1,
                                      'extra_instruction_paths': [], 'skills': [], 'mcp_servers': [],
                                      'resource_overrides': None, 'extra_mounts': [], 'extra_compose': []},
                      agent_runtime={'python': runtime['python'], 'preflight_passed': True,
                                     'tool_marker_present': tools is not None,
                                     'tool_python': tools['python'] if tools is not None else None,
                                     'tool_preflight_passed': tools['preflight_passed'] if tools is not None else None},
                      weak_identity_evidence=weaknesses,
                      replays_started=0, model_calls=0, paired_reward=None)
        # Re-read every file after inspection so a concurrent mutation cannot enter this proof silently.
        assert tree(trial) == files
        name = trial.name.split('__')[-1] + '.json'
        write_once(OUT / 'trials' / name, record)
        records.append({k: record[k] for k in ('trial', 'model', 'effort', 'reward', 'assistant_steps', 'source_file_count')})
        records[-1].update(record='trials/' + name, sha256=digest(OUT / 'trials' / name),
                           result_sha256=files['result.json']['sha256'], evidence_sha256=files['benchmark-evidence.json']['sha256'])
    assert tree(TASK) == task_files
    assert digest(ROOT / 'research/takehome-presentation-2026-09-14/data/results.json') == frozen_data_sha
    agent_facing = {name: data for name, data in task_files.items()
                    if name in ('instruction.md', 'task.toml') or name.startswith('environment/')}
    task_record = {'task': definition['path'], 'original_harbor_task_checksum': task_checksum,
                   'whole_task_directory_digest': directory_digest, 'whole_task_file_sha256': hashes,
                   'agent_facing_files': agent_facing,
                   'equality_scope': 'Files verified against the pre-campaign frozen whole-task digest; actual delivered instruction additionally verified per trial.',
                   'image_identity': 'Not established; no new image inspection or model environment was launched.'}
    write_once(OUT / 'task-inputs.json', task_record)
    manifest = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                'kind': 'original_first_counted_trial_inputs_for_future_paired_verifier_regrades',
                'campaign': CAMPAIGN, 'summary_updated_at': summary['updated_at'],
                'selection': 'Earliest counted UTC finished_at, then original trial path, per frozen model/effort cell; counted statuses are scored, timeout, verifier_timeout. Selection is independently recomputed from archived campaign summary, plan and scope, and bound to original raw result bytes.',
                'source_snapshots': {'summary': summary_source, 'plan': plan_source, 'scope': scope_source, 'frozen_task': frozen_source},
                'task_inputs': {'path': 'task-inputs.json', 'sha256': digest(OUT / 'task-inputs.json')},
                'original_terminal_rows_checked': raw_rows, 'excluded_noncounted_rows': excluded,
                'required_cells': 9, 'selected_cells': len(records), 'trials': records,
                'original_rewards': dict(Counter(str(record['reward']) for record in records)),
                'all_saved_sources_match_pre_verifier_capture': True, 'all_first_prompts_contain_exact_instruction': True,
                'missing_required_identity_evidence': [],
                'missing_optional_runtime_markers': missing_optional_markers,
                'weak_identity_evidence': ['Historical immutable agent image IDs were not established. Do not claim full runtime/image byte identity.',
                                           'This binds the original v3 stimulus only; final corrected revision stimulus equality and full paired grader outcomes remain separate future gates.'],
                'originals_preserved': supplied_preservation(), 'presentation_data_unchanged_sha256': frozen_data_sha,
                'builder_sha256': digest(Path(__file__)), 'passed': True,
                'model_calls': 0, 'replays_started': 0, 'counted_sweep_trials_added': 0,
                'original_scores_changed': False, 'paired_regrade_outcomes': None}
    write_once(OUT / 'manifest.json', manifest)
    print(json.dumps({'manifest': (OUT / 'manifest.json').relative_to(ROOT).as_posix(),
                      'sha256': digest(OUT / 'manifest.json'), 'selected': len(records),
                      'original_rewards': manifest['original_rewards'], 'source_file_counts': [r['source_file_count'] for r in records],
                      'passed': True, 'model_calls': 0, 'replays_started': 0}))


if __name__ == '__main__':
    main()
