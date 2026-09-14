#!/usr/bin/env python3
"""Plan or assemble an exact-byte take-home review pack. Never authors report prose.

Plan now: --plan-only --plan research/takehome-presentation-2026-09-14/packaging/plan-82.json
Assemble later: --plan <verified-99-plan.json> --output <packaging/review-pack>
Supply --report only to --plan-only, after the human-written report exists.
Select a separate collector snapshot with --data-dir only during --plan-only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
import unicodedata
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'research/takehome-presentation-2026-09-14'
PACKAGING = BASE / 'packaging'
SCOPE_SHA = '4154e6eec13c8a7454edd0f2f6090859be5e913b6065dc7cb6c4df763e5ad0bd'
RETAINED = {
    'rs-rerun-chunk-optimizer': 'candidates_v2/rs-rerun-chunk-optimizer',
    'rs-zenoh-timestamp-instrumentation-v3': 'research/task-revisions/rs-zenoh-timestamp-instrumentation-v3',
    'rs-burn-store-pytorch-reader-v4': 'research/task-revisions/rs-burn-store-pytorch-reader-v4',
}
SQLITE = 'candidates/sqlite-utils-schema-plan'
TASK_ROOTS = ('candidates', 'candidates_v2', 'research/task-revisions')
COUNTED = {'scored', 'timeout', 'verifier_timeout'}
ZENOH_HISTORY = 'rs-zenoh-timestamp-instrumentation-v3'
ZENOH_GROUPS = ['anti_cheat', 'build_a', 'build_b', 'lib_test_names', 'hidden_pr_tests',
                'robustness_tests', 'admin_timestamp_tests', 'interop_gold', 'parity_python',
                'zenoh_ext', 'upstream_regressions']
QUALITY_CRITERIA = {'behavior_in_task_description', 'behavior_in_tests', 'informative_test_structure',
                    'anti_cheating_measures', 'structured_data_schema', 'pinned_dependencies', 'typos',
                    'tests_or_solution_in_image', 'test_deps_in_image', 'hardcoded_solution', 'file_reference_mentioned'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def relative(value):
    text = str(value)
    p = PurePosixPath(text)
    require(text and not p.is_absolute() and '\\' not in text and '\x00' not in text
            and all(x not in ('', '.', '..') for x in text.split('/')), 'Unsafe relative path')
    return p


def workspace_path(path):
    p = Path(path)
    if p.is_absolute():
        p = p.relative_to(ROOT)
    return relative(p.as_posix()).as_posix()


def safe_open(path):
    """Open below the workspace through directory descriptors, refusing symlinks."""
    parts = relative(path).parts
    fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    finally:
        os.close(fd)
    if not stat.S_ISREG(os.fstat(file_fd).st_mode):
        os.close(file_fd)
        raise ValueError('Only regular files may be packaged')
    return os.fdopen(file_fd, 'rb')


def read(path):
    with safe_open(path) as stream:
        return stream.read()


def file_info(path):
    h = hashlib.sha256()
    with safe_open(path) as stream:
        before = os.fstat(stream.fileno())
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
        after = os.fstat(stream.fileno())
    require((before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_ino, after.st_size, after.st_mtime_ns), 'File changed while hashing: ' + path)
    return dict(sha256=h.hexdigest(), bytes=after.st_size,
                mode=stat.S_IMODE(after.st_mode), mtime_ns=after.st_mtime_ns)


def inventory(path):
    path = workspace_path(path)
    source = ROOT / path
    require(not source.is_symlink(), 'Symlinks are not packaged: ' + path)
    if source.is_file():
        return dict(is_directory=False, directories=[], files={'': file_info(path)})
    require(source.is_dir(), 'Missing directory: ' + path)
    directories, files = [''], {}
    for directory, dirs, names in os.walk(source, followlinks=False):
        for name in sorted(dirs + names):
            p = Path(directory) / name
            mode = p.lstat().st_mode
            require(not stat.S_ISLNK(mode), 'Symlinks are not packaged: ' + str(p.relative_to(ROOT)))
            rel = p.relative_to(source).as_posix()
            relative(rel)
            if stat.S_ISDIR(mode):
                directories.append(rel)
            elif stat.S_ISREG(mode):
                files[rel] = file_info(p.relative_to(ROOT).as_posix())
            else:
                raise ValueError('Non-regular input: ' + str(p.relative_to(ROOT)))
    require(files, 'Empty source tree: ' + path)
    return dict(is_directory=True, directories=sorted(directories), files=files)


def bytes_identity(tree):
    return dict(is_directory=tree['is_directory'], directories=tree['directories'],
                files={k: {a: v[a] for a in ('sha256', 'bytes', 'mode')} for k, v in tree['files'].items()})


def task_digest(tree):
    # Same whole-directory digest as scripts/candidate-bench.py.
    hashes = {k: v['sha256'] for k, v in tree['files'].items()}
    return sha(json.dumps(hashes, sort_keys=True).encode())


def task_paths():
    """Only direct task directories under the three authorized roots; never validation/jobs."""
    paths = []
    for root in TASK_ROOTS:
        for config in sorted((ROOT / root).glob('*/task.toml')):
            source = config.parent.relative_to(ROOT).as_posix()
            read(source + '/task.toml')  # Refuse a symlink at any component.
            paths.append(source)
    require(set(RETAINED.values()).issubset(paths) and SQLITE in paths, 'Required built task is missing')
    return sorted(paths)


def mapped_task_paths(retained):
    paths = sorted(set(task_paths()) | set(retained.values()))
    names = [unicodedata.normalize('NFC', Path(p).name).casefold() for p in paths]
    require(len(names) == len(set(names)), 'Task basenames collide across samples/archive')
    return paths


def cell(row):
    model = row['model']
    if not model.startswith('anthropic/'):
        model = 'anthropic/claude-' + model
    return json.dumps([row['task'], model, row['effort']], separators=(',', ':'))


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'Terminal timestamp must include a timezone')
    return result.astimezone(timezone.utc)


def original_materials():
    rows = subprocess.check_output(['git', 'ls-tree', '-rz', 'c1ae968', '--', '.'], cwd=ROOT).split(b'\0')
    checked = {}
    for row in rows:
        if not row:
            continue
        metadata, name = row.split(b'\t', 1)
        _, kind, expected = metadata.split()
        if kind != b'blob':
            continue
        name = name.decode()
        actual = subprocess.check_output(['git', 'hash-object', '--no-filters', '--', name], cwd=ROOT).strip()
        require(actual == expected, 'Supplied take-home file changed: ' + name)
        checked[name] = expected.decode()
    require(len(checked) == 36, 'Unexpected original-material inventory')
    return dict(commit='c1ae968', checked=len(checked), git_blob_ids=checked, unchanged=True)


def verify_selection(data, scope, documents):
    require(sha(documents['scope']) == SCOPE_SHA == data['scope_sha256'], 'Frozen scope changed')
    cells = scope['cells']
    require(scope['count'] == len(cells) == 99, 'Scope must contain exactly 99 cells')
    require(all(cell(v) == k for k, v in cells.items()), 'Invalid scope cell identity')
    require(set(data['counted_statuses']) == COUNTED and not data['data_quality_issues'], 'Invalid collector quality/status gate')
    authoritative, task_maps, job_maps = {}, {}, {}
    for campaign, source in data['campaign_plans'].items():
        plan = json.loads(documents['source:' + source['sha256']])
        task_maps[campaign] = {x['id']: x for x in plan['tasks']}
        job_maps[campaign] = {x['name']: x for x in plan['jobs']}
    for source in data['sources'].values():
        summary = json.loads(documents['source:' + source['sha256']])
        for row in summary['trials']:
            key = cell(row)
            if row['status'] in COUNTED and key in cells and cells[key]['job'] == summary['job']:
                require(row['trial'] not in authoritative, 'Duplicate authoritative trial')
                authoritative[row['trial']] = (summary['job'], row)
    require({x['trial'] for x in data['trials']} == set(authoritative), 'Collector/source trial inventory differs')
    require(len(data['trials']) == len(authoritative), 'Duplicate collector trial')
    by_cell = defaultdict(list)
    adopted = []
    for row in data['trials']:
        campaign, source = authoritative[row['trial']]
        key = cell(row)
        require(row['cell'] == key and row['campaign'] == campaign, 'Collector cell/campaign mismatch')
        require(row['status'] == source['status'] and row['finished_at'] == source['finished_at'], 'Terminal status drift')
        parts = relative(row['trial']).parts
        require(len(parts) == 3 and parts[0] == 'jobs' and row['raw_job'] == parts[1], 'Raw job/trial layout changed')
        require(parts[1] in job_maps[campaign], 'Raw job is not declared in the campaign plan')
        if parts[1] != campaign:
            declaration = job_maps[campaign][parts[1]]
            require(declaration.get('replacement_for') and declaration.get('reason'), 'Undeclared adopted trial')
            adopted.append(dict(trial=row['trial'], campaign=campaign, declaration=declaration))
        require(all(row.get(k) == source.get(k) for k in ('reward', 'raw_reward', 'assistant_steps', 'tool_calls',
                    'checks_passed', 'checks_total', 'check_unit', 'cost_usd')), 'Projected outcome metadata differs')
        result_path = row['trial'] + '/result.json'
        result_bytes = read(result_path)
        require(sha(result_bytes) == source['result_sha256'] == row['artifacts'][result_path]['sha256'], 'Raw result hash drift')
        result = json.loads(result_bytes)
        require(result.get('finished_at') and result['finished_at'] == row['finished_at'], 'Nonterminal raw trial')
        require(result['task_name'] == row['task'], 'Raw task identity differs')
        agent = result['config']['agent']
        require(cell(dict(task=result['task_name'], model=agent['model_name'], effort=agent['kwargs']['reasoning_effort'])) == key,
                'Raw model/effort differs')
        expected = task_maps[campaign][row['task']]
        require(result['task_checksum'] == row['task_checksum'] ==
                expected.get('harbor_task_checksum', expected.get('validated_harbor_task_checksum')), 'Raw task revision drift')
        evidence_path = row['trial'] + '/benchmark-evidence.json'
        evidence_bytes = read(evidence_path)
        require(sha(evidence_bytes) == source['benchmark_evidence_sha256'] == row['artifacts'][evidence_path]['sha256'],
                'Raw evidence hash drift')
        evidence = json.loads(evidence_bytes)
        require(row['assistant_steps'] == evidence['counts']['atif_agent_steps'] and
                row['tool_calls'] == evidence['counts']['atif_tool_calls'], 'Raw evidence counts differ')
        require(row['raw_reward'] == ((result.get('verifier_result') or {}).get('rewards') or {}).get('reward'),
                'Raw reward projection differs')
        by_cell[key].append(row)
    require(sorted(adopted, key=lambda x: x['trial']) == sorted(data['adopted_jobs'], key=lambda x: x['trial']),
            'Adopted-job provenance differs')
    first = [min(rows, key=lambda x: (timestamp(x['finished_at']), x['trial'])) for rows in by_cell.values()]
    names = {x['trial'] for x in first}
    require(all(row['first_counted_result'] is (row['trial'] in names) for row in data['trials']), 'First-result flags differ')
    missing = sorted(set(cells) - set(by_cell))
    coverage = dict(covered=len(first), required=99, ready=not missing, missing_cells=missing)
    require(data['coverage']['covered'] == len(first) and data['coverage']['required'] == 99
            and data['coverage']['ready'] is (not missing)
            and {x['cell'] for x in data['coverage']['missing']} == set(missing), 'Reported coverage disagrees')
    require(set(data['tasks']) == {v['task'] for v in cells.values()}, 'Eleven-task table differs from frozen scope')
    return sorted(first, key=lambda x: x['cell']), task_maps, coverage


def destinations(entries, allowed_roots=frozenset({'samples', 'jobs', 'archive', 'report'})):
    def normalized(name):
        return unicodedata.normalize('NFC', name).casefold()
    seen = {}
    for entry in entries:
        dest = relative(entry['destination']).as_posix()
        require(dest.split('/')[0] in allowed_roots, 'Unexpected pack root')
        for rel in entry['tree']['directories'] + list(entry['tree']['files']):
            name = dest + ('/' + rel if rel else '')
            key = normalized(name)
            require(key not in seen, 'Duplicate destination: ' + name)
            seen[key] = name
    # Detect a file used as a directory by another entry.
    files = {normalized(e['destination'] + ('/' + n if n else '')) for e in entries for n in e['tree']['files']}
    require(not any(normalized(str(parent)) in files for name in seen.values() for parent in PurePosixPath(name).parents),
            'Destination file/directory collision')


def data_directory(value=None):
    directory = workspace_path(value if value is not None else BASE / 'data')
    path = ROOT / directory
    require(path == BASE / 'data' or (path.is_relative_to(BASE / 'data-snapshots') and path != BASE / 'data-snapshots'),
            'Data directory must be the default data directory or a separate data-snapshots descendant')
    return directory


def data_snapshot_metadata(directory, documents):
    """Describe only the used files; retain relative aliases even when bytes are shared."""
    directory = data_directory(directory)
    data = json.loads(documents['results'])
    files = {'results.json': dict(document_key='results', sha256=sha(documents['results']), bytes=len(documents['results']))}
    for source in [data['scope_source'], *data['sources'].values(), *data['campaign_plans'].values()]:
        name = relative(source['snapshot']).as_posix()
        require(name != 'results.json', 'Collector source collides with results.json')
        key = 'source:' + source['sha256']
        content = documents[key]
        require(sha(content) == source['sha256'], 'Collector provenance snapshot drift')
        entry = dict(document_key=key, sha256=sha(content), bytes=len(content))
        require(name not in files or files[name] == entry, 'Conflicting collector snapshot path')
        files[name] = entry
    destinations([dict(destination='data-snapshot/' + name, tree=dict(is_directory=False, directories=[], files={'': row}))
                  for name, row in files.items()], {'data-snapshot'})
    require(documents['source:' + data['scope_sha256']] == documents['scope'], 'Scope snapshot differs')
    return dict(schema_version=1, directory=directory, results_path=directory + '/results.json',
                supporting_directory='data-snapshot', files=files)


def load_documents(data_dir=None):
    directory = data_directory(data_dir)
    documents = {
        'scope': read((BASE / 'coverage-scope.json').relative_to(ROOT).as_posix()),
        'results': read(directory + '/results.json'),
        'cases': read((BASE / 'evidence/shortlist-cases.json').relative_to(ROOT).as_posix()),
        'final_controls': read((BASE / 'evidence/shortlist-controls-final.json').relative_to(ROOT).as_posix()),
    }
    data = json.loads(documents['results'])
    for source in [data['scope_source'], *data['sources'].values(), *data['campaign_plans'].values()]:
        content = read(directory + '/' + relative(source['snapshot']).as_posix())
        require(sha(content) == source['sha256'], 'Collector provenance snapshot drift')
        documents['source:' + source['sha256']] = content
    metadata = data_snapshot_metadata(directory, documents)
    for name, expected in metadata['files'].items():
        actual = file_info(directory + '/' + name)
        require(all(actual[key] == expected[key] for key in ('sha256', 'bytes')), 'Selected data changed while loading')
    documents['selected_data'] = canonical(metadata)
    return documents


def captured_documents(plan):
    documents = {}
    for key, source in plan['captured_inputs'].items():
        content = read(source['path'])
        require(sha(content) == source['sha256'] and len(content) == source['bytes'], 'Frozen plan provenance changed')
        documents[key] = content
    return documents


def verify_selected_data(plan, documents):
    if 'selected_data' not in documents:
        require(plan.get('selected_data') is None, 'Selected data metadata is missing from captured inputs')
        return None  # Historical plans already contain their authoritative results/source bytes.
    metadata = json.loads(documents['selected_data'])
    require(metadata == data_snapshot_metadata(metadata['directory'], documents)
            and plan['selected_data'] == metadata, 'Selected data snapshot binding differs')
    return metadata


def copy_selected_data(plan, supporting):
    documents = captured_documents(plan)
    selected = verify_selected_data(plan, documents)
    if selected is None:
        return
    for name, expected in selected['files'].items():
        source = plan['captured_inputs'][expected['document_key']]['path']
        tree = inventory(source)
        require(all(tree['files'][''][k] == expected[k] for k in ('sha256', 'bytes')), 'Captured data snapshot drift')
        destination = selected['supporting_directory'] + '/' + name
        copy_entry(dict(source=source, destination=destination, tree=tree), supporting)
        require(bytes_identity(inventory(supporting / destination)) == bytes_identity(tree), 'Copied data snapshot differs')


def control_sources(controls, task_maps):
    """Bind six real exact-revision controls, separately from counted model trials."""
    require(len(controls) == 3 and {c['task_id'] for c in controls} == set(RETAINED), 'Final control shortlist differs')
    definitions = {name: row for tasks in task_maps.values() for name, row in tasks.items() if name in RETAINED}
    sources = set()
    for group in controls:
        name = group['task_id']
        expected = definitions[name]
        checksum = expected.get('harbor_task_checksum', expected.get('validated_harbor_task_checksum'))
        digest = expected.get('task_sha256', expected.get('validated_task_sha256'))
        require(group['task_path'] == RETAINED[name] and group['current_harbor_checksum'] == checksum
                and group['current_task_sha256'] == digest and group['exact_revision_controls_pass'] is True,
                'Final controls do not bind the retained revision')
        require(sha(read(group['manifest_path'])) == group['manifest_sha256'], 'Control manifest drift')
        sources.add(group['manifest_path'])
        require(len(group['controls']) == 2 and {c['agent'] for c in group['controls']} == {'oracle', 'nop'},
                'Final controls must contain exactly one oracle and one nop')
        for control in group['controls']:
            result_bytes = read(control['path'])
            score_bytes = read(control['score_path'])
            require(sha(result_bytes) == control['result_sha256'] and sha(score_bytes) == control['score_sha256'],
                    'Final control raw result/score drift')
            result = json.loads(result_bytes)
            require(result['task_name'] == name and result['task_checksum'] == checksum
                    and result['config']['agent']['name'] == control['agent'] and result['finished_at']
                    and not result.get('exception_info')
                    and result['verifier_result']['rewards']['reward'] == json.loads(score_bytes)['reward']
                    == (1 if control['agent'] == 'oracle' else 0),
                    'Final control does not have the expected terminal outcome')
            trial = Path(control['path']).parent
            require(Path(control['score_path']).is_relative_to(trial), 'Control score is outside its trial')
            sources.add(trial.as_posix())
            if control.get('completion_verification'):
                require(sha(read(control['completion_verification'])) == control['completion_verification_sha256'],
                        'Control completion proof drift')
                sources.add(control['completion_verification'])
    return sources


def bound_json(reference, supporting):
    """Read a supplied evidence reference without trusting a summary's success flag."""
    path = workspace_path(reference['path'])
    content = read(path)
    require(sha(content) == reference['sha256'], 'Revision evidence hash drift: ' + path)
    supporting.add(path)
    return json.loads(content)


def file_hashes(path):
    return {name: value['sha256'] for name, value in inventory(path)['files'].items()}


def relative_reference(parent, path, checksum):
    return dict(path=str(PurePosixPath(parent).parent / relative(path)), sha256=checksum)


def validate_group_score(score, reward):
    require(score['required_groups'] == ZENOH_GROUPS and score['missing_groups'] == []
            and set(score['groups']) == set(ZENOH_GROUPS), 'A complete corrected verifier must report all 11 groups')
    require(all(type(g['pass']) is bool for g in score['groups'].values()), 'Invalid group outcome')
    require(type(reward) in (int, float) and reward in (0, 1) and score['reward'] == reward
            == int(all(g['pass'] for g in score['groups'].values())), 'Full verifier reward/group disagreement')


def verify_original_pair_inputs(reference, first, definition, supporting):
    manifest = bound_json(reference, supporting)
    require(manifest['kind'] == 'original_first_counted_trial_inputs_for_future_paired_verifier_regrades'
            and manifest['passed'] is True and manifest['model_calls'] == manifest['counted_sweep_trials_added'] == 0
            and manifest['original_scores_changed'] is False and not manifest['missing_required_identity_evidence'],
            'Original paired-input proof is incomplete')
    selected = {r['trial']: r for r in first if r['task'] == ZENOH_HISTORY}
    require(len(selected) == 9 and manifest['required_cells'] == manifest['selected_cells'] == 9
            and len(manifest['trials']) == 9 and {r['trial'] for r in manifest['trials']} == set(selected),
            'Final revision requires all nine original first-counted cells, including failures')
    source = RETAINED[ZENOH_HISTORY]
    task_inputs = bound_json(relative_reference(reference['path'], manifest['task_inputs']['path'],
                                                manifest['task_inputs']['sha256']), supporting)
    require(task_inputs['task'] == source and task_inputs['whole_task_file_sha256'] == file_hashes(source),
            'Original paired-input task identity differs')
    checksum = definition.get('harbor_task_checksum', definition.get('validated_harbor_task_checksum'))
    require(task_inputs['original_harbor_task_checksum'] == checksum, 'Original paired-input Harbor checksum differs')
    for item in manifest['source_snapshots'].values():
        bound_json(dict(path=item['snapshot'], sha256=item['sha256']), supporting)
    instruction = read(source + '/instruction.md')
    records = {}
    for item in manifest['trials']:
        record_ref = relative_reference(reference['path'], item['record'], item['sha256'])
        record = bound_json(record_ref, supporting)
        trial = item['trial']; row = selected[trial]
        require(record['trial'] == trial and record['first_counted'] is True
                and record['original_task_checksum'] == checksum
                and all(record[k] == row[k] for k in ('model', 'effort', 'finished_at', 'reward', 'raw_reward',
                                                      'assistant_steps', 'tool_calls')),
                'Original paired outcome differs from frozen first-counted result')
        for name, expected in record['raw_files'].items():
            actual = file_info(trial + '/' + relative(name).as_posix())
            require(actual['sha256'] == expected['sha256'] and actual['bytes'] == expected['size'],
                    'Original paired raw artifact changed')
        require(set(file_hashes(trial)) == set(record['raw_files']), 'Original paired raw inventory changed')
        require(record['raw_files']['result.json']['sha256'] == item['result_sha256']
                == row['artifacts'][trial + '/result.json']['sha256'], 'Original paired result anchor differs')
        actual_sources = {'artifacts/submission/' + name: dict(kind='file', sha256=value['sha256'], size=value['bytes'])
                          for name, value in inventory(trial + '/artifacts/submission')['files'].items()}
        snapshot = json.loads(read(trial + '/benchmark-snapshot.json'))
        expected_sources = {name: value for name, value in snapshot['entries'].items()
                            if name.startswith('artifacts/submission/') and value['kind'] == 'file'}
        require(actual_sources == record['source_files'] == expected_sources
                and sha(canonical(actual_sources)) == record['source_manifest_sha256']
                and len(actual_sources) == record['source_file_count'] == item['source_file_count'],
                'Saved source no longer matches the original pre-verifier capture')
        native = json.loads(read(trial + '/agent/mini-swe-agent.trajectory.json'))
        prompt = next(m['content'] for m in native['messages'] if m.get('role') == 'user')
        require(isinstance(prompt, str) and instruction.decode() in prompt
                and sha(prompt.encode()) == record['first_user_prompt']['sha256'], 'Delivered instruction differs')
        marker = json.loads(read(trial + '/agent/benchmark-agent-input.json'))
        require(marker['delivery'] == 'final_run_task_config' and marker['task_sha256'] == sha(instruction),
                'Delivered instruction marker differs')
        config = json.loads(read(trial + '/config.json'))
        raw_result = json.loads(read(trial + '/result.json'))
        require(config == raw_result['config'] and workspace_path(config['task']['path']) == source,
                'Original saved config/task identity differs')
        require(config['timeout_multiplier'] == 1 and not config.get('extra_instruction_paths')
                and all(v is None for k, v in config.items() if k.endswith('_timeout_multiplier')), 'Original stimulus changed')
        require(not config['agent'].get('skills') and not config['agent'].get('mcp_servers')
                and not (config['agent'].get('kwargs') or {}).get('skills')
                and not (config['agent'].get('kwargs') or {}).get('mcp_servers'),
                'Unexpected original skills or MCP tools')
        require(all(v is None for k, v in config['environment'].items() if k.startswith('override_'))
                and not config['environment'].get('mounts') and not config['environment'].get('extra_docker_compose')
                and not (config['environment'].get('kwargs') or {}).get('extra_mounts')
                and not (config['environment'].get('kwargs') or {}).get('extra_compose_files'), 'Original environment overrides differ')
        require(all(config['agent'].get(k) is None for k in
                    ('override_timeout_sec', 'max_timeout_sec', 'override_setup_timeout_sec')), 'Original agent budget differs')
        records[trial] = dict(record=record, record_sha256=item['sha256'], row=row,
                             sources={n.removeprefix('artifacts/submission/'): v['sha256'] for n, v in actual_sources.items()})
    supporting.add(str(PurePosixPath(reference['path']).parent))
    return records, manifest


def verify_revision_controls(reference, final_task, task_reference, supporting):
    summary = bound_json(reference, supporting)
    require(summary['passed'] is True and summary['model_calls'] == 0 and summary['inputs_unchanged'] is True
            and summary['tooling_unchanged'] is True and set(summary['controls']) == {'oracle', 'nop'},
            'Corrected task controls are not complete')
    identity = bound_json(dict(path=summary['run_identity'], sha256=summary['run_identity_sha256']), supporting)
    require(identity['task'] == final_task['task'] and identity['task_checksum'] == final_task['harbor_task_checksum']
            and identity['inputs']['task_file_sha256'] == final_task['task_file_sha256']
            and identity['inputs']['task_manifest_sha256'] == task_reference['sha256'], 'Controls exercised a different task')
    for agent, entry in summary['controls'].items():
        require(entry['passed'] is True and entry['model_calls'] == 0 and entry['exit_code'] == 0,
                'Corrected task control execution failed')
        result = bound_json(dict(path=entry['result'], sha256=entry['result_sha256']), supporting)
        trial = str(PurePosixPath(entry['result']).parent)
        require(file_hashes(trial) == entry['trial_file_sha256'], 'Corrected control raw files changed')
        supporting.add(trial)
        require(result['task_name'] == Path(final_task['task']).name
                and result['task_checksum'] == final_task['harbor_task_checksum']
                and result['config']['agent']['name'] == agent and result['config']['agent']['model_name'] is None
                and result['finished_at'] and result.get('exception_info') is None, 'Corrected control identity differs')
        config = result['config']
        source_proof = bound_json(dict(path=entry['runner_source_proof'],
                                       sha256=entry['runner_source_proof_sha256']), supporting)
        require(source_proof['captured_tree_unchanged'] is True and source_proof['all_local_imports_captured'] is True
                and source_proof['repository_root_overrides'] is False, 'Control runner source proof incomplete')
        execution_root = PurePosixPath(source_proof['cwd'])
        require(execution_root.is_absolute() and '..' not in execution_root.parts, 'Invalid captured execution root')
        task_path = config['task']['path']
        expected_task_path = (execution_root / relative(final_task['task'])).as_posix()
        require(config == json.loads(read(trial + '/config.json'))
                and (task_path == final_task['task'] or task_path == expected_task_path), 'Control config/task path differs')
        require(result['agent_info']['name'] == agent and result['agent_info'].get('model_info') is None
                and all((result.get('agent_result') or {}).get(k) in (None, 0)
                        for k in ('n_input_tokens', 'n_output_tokens', 'cost_usd')), 'Control unexpectedly used a model')
        require(config['timeout_multiplier'] == 1 and not config.get('extra_instruction_paths')
                and all(v is None for k, v in config.items() if k.endswith('_timeout_multiplier'))
                and all(v is None for k, v in config['environment'].items() if k.startswith('override_')),
                'Corrected control task budgets were overridden')
        for section in ('agent', 'verifier'):
            require(not config[section].get('disable') and all(config[section].get(k) is None for k in
                    ('override_timeout_sec', 'max_timeout_sec', 'override_setup_timeout_sec')), 'Control budget override')
        score = json.loads(read(trial + '/verifier/score.json'))
        reward = 1 if agent == 'oracle' else 0
        require(result['verifier_result']['rewards']['reward'] == reward, 'Corrected control reward differs')
        validate_group_score(score, reward)
        require(float(read(trial + '/verifier/reward.txt')) == reward, 'Control reward artifact disagrees with score')
        if agent == 'oracle':
            for group, counts in {'hidden_pr_tests': {'timestamp_instrumentation': 47},
                                  'robustness_tests': {'timestamp_robustness': 5},
                                  'admin_timestamp_tests': {'timestamp_adminspace': 1, 'timestamp_adminspace_reply_stack': 1},
                                  'interop_gold': {'ts_interop': 6}, 'parity_python': {'ts_interop': 4}}.items():
                details = json.loads(read(trial + '/verifier/' + group + '.json'))
                require(details['problems'] == [], 'Corrected oracle scorer reported problems')
                for name, count in counts.items():
                    binary = details['binaries'][name]
                    require(binary['passed'] == count and binary['failed'] == binary['ignored'] == 0
                            and binary['status'] == 'ok' and len(details['tests'][name]) == count
                            and all(v == 'ok' for v in details['tests'][name].values()), 'Corrected oracle omitted required tests')
        lifecycle = entry['admission_lifecycle']
        require(lifecycle['passed'] is True and lifecycle['peak_own_claims'] == 1
                and lifecycle['final_own_claims'] == 0 and lifecycle['shared_cap_respected'] is True,
                'Corrected control cleanup/admission proof incomplete')
        bound_json_or_lines = read(workspace_path(lifecycle['path']))
        require(sha(bound_json_or_lines) == lifecycle['sha256'], 'Control admission proof drift')
        supporting.add(lifecycle['path'])
    return summary


def verify_revision_quality(reference, final_task, supporting):
    audit = bound_json(reference, supporting)
    require(audit['task'] == final_task['task'] and audit.get('task_checksum', audit.get('original_task_checksum'))
            == final_task['harbor_task_checksum'] and audit['target_task_file_sha256'] == final_task['task_file_sha256'],
            'Quality review does not bind the corrected task')
    require(file_hashes(audit['reviewed_copy_path']) == final_task['task_file_sha256'], 'Reviewer saw a different task copy')
    supporting.add(audit['reviewed_copy_path'])
    report = bound_json(dict(path=audit['check_report'], sha256=audit['check_report_sha256']), supporting)
    require(len(report['results']) == 1 and report['results'][0].get('error') is None, 'Quality review report incomplete')
    checks = report['results'][0]['checks']
    require(set(checks) == QUALITY_CRITERIA and all(c['outcome'] == 'pass' for c in checks.values()),
            'Every corrected-task quality criterion must pass; a review reward alone is insufficient')
    require(audit['valid_review_report'] is True and audit['rubric_pass'] == 11
            and audit['rubric_fail'] == audit['rubric_not_applicable'] == [], 'Quality audit/report disagreement')
    trial = workspace_path(audit['raw_trial'])
    require(file_hashes(trial) == audit['raw_trial_file_sha256'], 'Quality raw trial manifest drift')
    for name, expected in audit['evidence']['input_snapshot']['entries'].items():
        if expected['kind'] == 'file':
            require(file_info(trial + '/' + relative(name).as_posix())['sha256'] == expected['sha256'],
                    'Quality raw evidence changed')
    require(json.loads(read(trial + '/artifacts/check-result.json')) == checks, 'Quality report differs from raw review artifact')
    result = json.loads(read(trial + '/result.json'))
    require(result['finished_at'] and result.get('exception_info') is None
            and result['config']['agent']['name'] == 'mini-swe-agent'
            and result['config']['agent']['model_name'] == 'anthropic/claude-sonnet-5'
            and result['config']['agent']['kwargs']['reasoning_effort'] == 'high'
            and result['config']['agent']['kwargs']['version'] == '2.4.6'
            and audit['evidence']['identity']['agent'] == 'mini-swe-agent'
            and audit['evidence']['identity']['agent_version'] == '2.4.6', 'Quality reviewer identity differs')
    supporting.add(trial)
    return checks


def verify_paired_runtime(summary, controls_reference, controls, final_task, supporting):
    require(dict(path=summary['controls_summary'], sha256=summary['controls_summary_sha256']) == controls_reference,
            'Paired runtime used different final controls')
    oracle = controls['controls']['oracle']
    proof_reference = dict(path=summary['verifier_image_proof'], sha256=summary['verifier_image_proof_sha256'])
    require(all(oracle['verifier_image_proof'][k] == proof_reference[k] for k in ('path', 'sha256')),
            'Paired verifier image proof is not bound to the normal oracle')
    proof = bound_json(proof_reference, supporting)
    require(proof['passed'] is True and proof['kind'] == 'normal_harbor_oracle_verifier_image'
            and proof['oracle_result_path'] == oracle['result'] and proof['oracle_result_sha256'] == oracle['result_sha256']
            and proof['final_task_checksum'] == final_task['harbor_task_checksum'], 'Verifier image proof identity differs')
    inspection = bound_json(dict(path=proof['raw_docker_inspection_path'],
                                  sha256=proof['raw_docker_inspection_sha256']), supporting)
    host = proof['observed_host_config']
    require(inspection['Image'] == proof['verifier_image_id'] and inspection['HostConfig'] == host
            and inspection['Config.Labels']['com.docker.compose.project']
            == Path(oracle['result']).parent.name.lower() + '__verifier__trial', 'Oracle image inspection identity differs')
    require(host['NanoCpus'] == 4000000000 and host['Memory'] == 8192 * 1024 * 1024
            and host['NetworkMode'] == 'none' and type(host['MemorySwap']) is int
            and host['StorageOpt'] in (None, {}), 'Oracle runtime limits/inspection incomplete')
    diagnostics = bound_json(dict(path=summary['diagnostics_summary'], sha256=summary['diagnostics_summary_sha256']), supporting)
    require(diagnostics['passed'] is True and diagnostics['model_calls'] == diagnostics['counted_sweep_trials'] == 0
            and diagnostics['controls_summary_sha256'] == controls_reference['sha256'], 'Omission probes incomplete or used other controls')
    mutant_path = str(PurePosixPath(final_task['task']).parent / 'mutants/manifest.json')
    mutants = bound_json(dict(path=mutant_path, sha256=diagnostics['mutant_manifest_sha256']), supporting)
    probes = [r for r in diagnostics['cases'] if r['kind'] == 'single_omission_probe']
    require(len(probes) == 6 and {r['name'] for r in probes} == {r['id'] for r in mutants['mutants']},
            'All six distinct omission probes are required')
    for probe in probes:
        require(probe['omission_detected'] is True and probe['other_tests_passed'] is True
                and probe['case_exit_code'] == 101, 'Omission probe lacks the expected runtime assertion')
        raw = bound_json(dict(path=probe['result'], sha256=probe['result_sha256']), supporting)
        root = str(PurePosixPath(probe['result']).parent)
        require(raw['status'] == 'complete' and raw['case_exit_code'] == 101 and raw['model_calls'] == 0
                and raw['container_absent'] is True and raw['claim_absent'] is True and raw['inputs_unchanged'] is True,
                'Omission probe did not complete normally')
        for name, expected in raw['artifact_sha256'].items():
            require(sha(read(root + '/case/' + relative(name).as_posix())) == expected, 'Omission probe artifact drift')
        log = read(root + '/case/cargo.stdout.log').decode()
        require('error[E' not in log and probe['expected_assertion_signatures']
                and all(s in log for s in probe['expected_assertion_signatures']), 'Omission assertion evidence missing')
        supporting.add(root)
    inputs = bound_json(dict(path=summary['inputs_path'], sha256=summary['inputs_sha256']), supporting)
    require(inputs['task'] == summary['task'] and inputs['original_input_manifest'] == summary['original_input_manifest']
            and inputs['control_summary_sha256'] == controls_reference['sha256']
            and inputs['verifier_image_proof_sha256'] == proof_reference['sha256']
            and inputs['diagnostics_summary_sha256'] == summary['diagnostics_summary_sha256']
            and inputs['verifier_image_id'] == proof['verifier_image_id']
            and inputs['oracle_observed_host_config'] == host, 'Paired run inputs disagree with final runtime evidence')
    return proof


def verify_paired_regrades(reference, originals, input_reference, task_reference, final_task,
                           controls_reference, controls, supporting):
    summary = bound_json(reference, supporting)
    require(summary['kind'] == 'paired_verifier_regrades' and summary['validation_passed'] is True
            and summary['all_regrades_complete'] is True and summary['model_calls'] == summary['counted_sweep_trials'] == 0,
            'Paired full regrades are incomplete or counted as model trials')
    require(summary['original_input_manifest'] == input_reference and summary['task'] == dict(
            path=final_task['task'], harbor_task_checksum=final_task['harbor_task_checksum'],
            task_manifest_sha256=task_reference['sha256'], task_file_sha256=final_task['task_file_sha256']),
            'Paired regrades used different task/input identities')
    require(len(summary['regrades']) == 9 and {r['original_trial_path'] for r in summary['regrades']} == set(originals),
            'Must pair every original first-counted cell exactly once')
    runtime = verify_paired_runtime(summary, controls_reference, controls, final_task, supporting)
    expected_tests = {n: h for n, h in final_task['task_file_sha256'].items() if n.startswith('tests/')}
    outcomes = []
    for pair in summary['regrades']:
        original = originals[pair['original_trial_path']]; row = original['row']; record = original['record']
        require(pair['kind'] == 'paired_verifier_regrade' and pair['full_regrade'] is True
                and pair['model_calls'] == 0 and pair['counted_sweep_trial'] is False
                and pair['validation_passed'] is True,
                'Focused probes/new model attempts cannot substitute for full paired regrades')
        require(pair['original_result_sha256'] == row['artifacts'][row['trial'] + '/result.json']['sha256']
                and pair['original_input_record_sha256'] == original['record_sha256']
                and pair['original_task_checksum'] == record['original_task_checksum']
                and pair['original_reward'] == row['reward']
                and pair['submitted_source_manifest_sha256'] == record['source_manifest_sha256']
                and pair['final_task_checksum'] == final_task['harbor_task_checksum'], 'Paired original/final identity mismatch')
        require(pair['source_file_sha256_before'] == pair['source_file_sha256_after'] == original['sources']
                and pair['test_file_sha256_before'] == pair['test_file_sha256_after'] == expected_tests
                and pair['source_files_unchanged'] is True and pair['test_files_unchanged'] is True,
                'Paired execution source/test identity differs')
        require(pair['verifier_image_id'].startswith('sha256:') and len(pair['verifier_image_id']) == 71
                and all(c in '0123456789abcdef' for c in pair['verifier_image_id'][7:])
                and pair['verifier_image_id'] == runtime['verifier_image_id'],
                'Paired verifier image must have an immutable identity')
        declared = dict(cpus=4, memory_mb=8192, storage_mb=30720, verifier_timeout_seconds=3600,
                        network_mode='no-network', local_max_active=1, shared_max_active=14)
        require(pair['declared_limits'] == declared, 'Paired declared resource limits differ')
        observed = pair['observed_limits']
        require(all(observed[k] == declared[k] for k in ('cpus', 'memory_mb', 'verifier_timeout_seconds'))
                and observed['network_mode'] == 'none'
                and observed['storage_mb'] in (None, 30720), 'Paired observed resource limits differ')
        require(observed['storage_mb'] is not None or (not observed['storage_opt'] and observed['storage_note']),
                'Unconfigured storage quota must retain actual StorageOpt evidence and limitation')
        require(observed['memory_swap_bytes'] == runtime['observed_host_config']['MemorySwap']
                and (observed['storage_opt'] or {}) == (runtime['observed_host_config']['StorageOpt'] or {}),
                'Paired swap/storage settings differ from the normal oracle')
        require(pair['cleanup']['container_absent'] is True and pair['cleanup']['claim_absent'] is True
                and pair['cleanup']['stopped_before_collection'] is True and not pair['cleanup'].get('errors'),
                'Paired cleanup remains incomplete')
        root = workspace_path(pair['artifact_root'])
        require(not root.startswith('jobs/'), 'Paired diagnostics must remain separate from original raw jobs')
        require(file_hashes(root) == pair['artifact_file_sha256'], 'Paired raw diagnostic files changed')
        supporting.add(root)
        process = json.loads(read(root + '/artifacts/case/raw-result.json'))
        require(process['kind'] == 'paired_verifier_process_result' and process['full_regrade'] is True
                and process['model_calls'] == 0 and process['verifier_exit_code'] == 0
                and process['timed_out'] is False and not process.get('error')
                and process['workload_timeout_seconds'] == 3600
                and process['source_files_unchanged'] is True and process['test_files_unchanged'] is True,
                'Full verifier process did not finish normally')
        for field, filename in (('source_file_sha256_before', 'source-before.json'),
                                ('source_file_sha256_after', 'source-after.json'),
                                ('test_file_sha256_before', 'tests-before.json'),
                                ('test_file_sha256_after', 'tests-after.json')):
            require(json.loads(read(root + '/artifacts/case/' + filename)) == pair[field],
                    'Paired process input maps differ from result projection')
        for path in (pair['result_path'], pair['score_path']):
            require(PurePosixPath(workspace_path(path)).is_relative_to(root), 'Paired result/score outside its artifact root')
        result = bound_json(dict(path=pair['result_path'], sha256=pair['result_sha256']), supporting)
        require(result['kind'] == 'paired_verifier_regrade' and result['full_regrade'] is True
                and result['model_calls'] == 0 and result['counted_sweep_trial'] is False
                and result['validation_passed'] is True and result['driver_exit_code'] == 0
                and result['original_trial_path'] == row['trial'] and result['final_task_checksum'] == final_task['harbor_task_checksum']
                and result['reward'] == pair['reward'] and result['finished_at'] and result['status'] == 'complete',
                'Paired raw result disagrees with summary')
        for key in ('original_result_sha256', 'original_input_record_sha256', 'original_task_checksum', 'original_reward',
                    'submitted_source_manifest_sha256', 'source_file_sha256_before', 'source_file_sha256_after',
                    'test_file_sha256_before', 'test_file_sha256_after', 'source_files_unchanged', 'test_files_unchanged',
                    'verifier_image_id', 'declared_limits', 'observed_limits', 'cleanup',
                    'score_path', 'score_sha256', 'groups', 'required_groups', 'artifact_root'):
            require(result[key] == pair[key], 'Paired identity/provenance projection differs: ' + key)
        score = bound_json(dict(path=pair['score_path'], sha256=pair['score_sha256']), supporting)
        validate_group_score(score, pair['reward'])
        require(float(read(root + '/artifacts/verifier/reward.txt')) == pair['reward'], 'Paired reward artifact disagrees with score')
        require(pair['required_groups'] == score['required_groups'] and pair['groups'] == score['groups'], 'Paired group summary drift')
        outcomes.append(dict(original_trial=row['trial'], original_reward=row['reward'], paired_reward=pair['reward'],
                             counted_as_new_model_trial=False, observed_storage_mb=observed['storage_mb']))
    return outcomes


def final_revision_mapping(mapping, first, task_maps):
    """Optional explicit correction, currently scoped to Zenoh's separately validated v3 successor."""
    effective, supporting, evidence = dict(RETAINED), set(), []
    if mapping is None:
        return effective, supporting, evidence
    require(mapping['schema_version'] == 1 and mapping['kind'] == 'final_revision_packaging_mapping'
            and len(mapping['mappings']) == 1, 'Unsupported final-revision mapping schema')
    entry = mapping['mappings'][0]
    require(entry['historical_task_id'] == ZENOH_HISTORY, 'Only the explicitly supported Zenoh correction can be mapped')
    definition = next(tasks[ZENOH_HISTORY] for tasks in task_maps.values() if ZENOH_HISTORY in tasks)
    final_task = bound_json(entry['final_task_manifest'], supporting)
    final_path = workspace_path(final_task['task'])
    require(final_path != RETAINED[ZENOH_HISTORY] and final_path not in RETAINED.values()
            and final_path.startswith('research/'), 'Corrected task must have a distinct research revision path')
    final_files = file_hashes(final_path)
    old_files = file_hashes(RETAINED[ZENOH_HISTORY])
    require(sha(json.dumps(old_files, sort_keys=True).encode()) == definition.get('task_sha256', definition.get('validated_task_sha256')),
            'Historical task no longer matches its frozen campaign')
    require(final_task['harbor_task_checksum'] != definition.get('harbor_task_checksum', definition.get('validated_harbor_task_checksum')),
            'Corrected revision must retain a distinct task checksum')
    require(final_files == final_task['task_file_sha256'] and 'task.toml' in final_files, 'Corrected task manifest drift')
    delivered = lambda files: {n: h for n, h in files.items() if n in ('instruction.md', 'task.toml') or n.startswith('environment/')}
    require(delivered(final_files) == delivered(old_files), 'Corrected revision changed agent-facing inputs or budgets')
    changed = sorted(n for n in set(old_files) | set(final_files) if old_files.get(n) != final_files.get(n))
    require(changed and changed == entry['reviewed_changed_files'] and all(
        n.startswith(('tests/', 'solution/')) or n in ('STATUS.md', 'provenance.json') for n in changed),
        'Unreviewed revision changes outside verifier/reference/provenance scope')
    lineage = bound_json(entry['revision_lineage'], supporting)
    require(lineage['parent_task'] == RETAINED[ZENOH_HISTORY] and lineage['parent_file_sha256'] == old_files
            and lineage['revision_task'] == final_path and lineage['revision_file_sha256'] == final_files
            and lineage['changed_files'] == changed and lineage['instruction_and_budgets_unchanged'] is True
            and lineage['existing_v3_scores_and_campaign_unchanged'] is True,
            'Revision lineage does not bind the reviewed parent/final task')
    for path_key in ('reference_result', 'reference_failure_log'):
        proof_path = workspace_path(lineage[path_key])
        require(sha(read(proof_path)) == lineage[path_key + '_sha256'], 'Reference correction proof drift')
        supporting.add(proof_path)
    require(final_files.get('solution/admin-reply-stack.patch') == lineage['reference_correction_sha256'],
            'Reference correction patch differs from lineage')
    originals, manifest = verify_original_pair_inputs(entry['paired_inputs_manifest'], first, definition, supporting)
    controls = verify_revision_controls(entry['controls_summary'], final_task, entry['final_task_manifest'], supporting)
    verify_revision_quality(entry['final_quality'], final_task, supporting)
    outcomes = verify_paired_regrades(entry['paired_regrades_summary'], originals, entry['paired_inputs_manifest'],
                                     entry['final_task_manifest'], final_task, entry['controls_summary'], controls, supporting)
    effective[ZENOH_HISTORY] = final_path
    evidence.append(dict(historical_task_id=ZENOH_HISTORY, historical_task_path=RETAINED[ZENOH_HISTORY],
                         final_task_path=final_path, final_task_checksum=final_task['harbor_task_checksum'],
                         reviewed_changed_files=changed, paired_outcomes=outcomes,
                         historical_identity_limits=manifest['weak_identity_evidence'],
                         missing_optional_runtime_markers=manifest['missing_optional_runtime_markers']))
    return effective, supporting, evidence


def make_plan(plan_path, report=None, supporting=(), final_revision=None, data_dir=None):
    preservation = original_materials()
    documents = load_documents(data_dir)
    selected_data = json.loads(documents['selected_data']) if 'selected_data' in documents else None
    data, scope, cases = (json.loads(documents[k]) for k in ('results', 'scope', 'cases'))
    first, task_maps, coverage = verify_selection(data, scope, documents)
    if final_revision is not None:
        documents['final_revision_mapping'] = read(workspace_path(final_revision))
    mapping = json.loads(documents['final_revision_mapping']) if 'final_revision_mapping' in documents else None
    retained, revision_sources, revision_evidence = final_revision_mapping(mapping, first, task_maps)
    controls = json.loads(documents['final_controls'])
    required_supporting = control_sources(controls, task_maps)
    required_supporting.update(revision_sources)
    required_supporting.add((BASE / 'evidence').relative_to(ROOT).as_posix())
    require({x['task_id'] for x in cases['cases']} == set(RETAINED), 'Provisional shortlist differs')
    definitions = {}
    for campaign, tasks in task_maps.items():
        for name, row in tasks.items():
            if name in data['tasks']:
                require(name not in definitions or definitions[name]['path'] == row['path'], 'Conflicting task path')
                definitions[name] = row
    built_tasks = mapped_task_paths(retained)
    entries = []
    for name in sorted(data['tasks']):
        definition = definitions[name]
        source = workspace_path(definition['path'])
        require(source in built_tasks, 'Comparison task is outside the authorized archive roots')
        if name in RETAINED:
            require(source == RETAINED[name], 'Retained revision differs from provisional selection')
        tree = inventory(source)
        expected = definition.get('task_sha256', definition.get('validated_task_sha256'))
        require(task_digest(tree) == expected, 'Task whole-directory hash drift: ' + source)
        kept = source in retained.values()
        entries.append(dict(kind='retained_task' if kept else 'discarded_task', task=name,
                            source=source, destination=('samples/' if kept else 'archive/') + Path(source).name,
                            archive_role=None if kept else ('paired_historical_revision' if name in RETAINED else 'current_eleven_task_comparison'),
                            whole_task_sha256=expected, tree=tree))
    for source in sorted(set(built_tasks) - {e['source'] for e in entries}):
        kept = source in retained.values()
        entries.append(dict(kind='retained_task' if kept else 'discarded_task', task=Path(source).name, source=source,
                            destination=('samples/' if kept else 'archive/') + Path(source).name,
                            archive_role=None if kept else ('original_nul_audit_candidate' if source == SQLITE else 'historical_candidate_or_superseded_revision'),
                            tree=inventory(source)))
    selected = [row for row in first if row['task'] in RETAINED]
    for row in selected:
        source = workspace_path(row['trial'])
        tree = inventory(source)
        for mandatory in ('result.json', 'agent/trajectory.json', 'agent/mini-swe-agent.trajectory.json',
                          'benchmark-evidence.json', 'benchmark-snapshot.json'):
            require(mandatory in tree['files'], 'Missing required raw evidence: ' + source + '/' + mandatory)
        require(any(x.startswith('verifier/') for x in tree['files']) and any(x.startswith('artifacts/') for x in tree['files']),
                'Raw trial must retain verifier and submission artifacts')
        for path, expected in row['artifacts'].items():
            rel = Path(path).relative_to(source).as_posix()
            require(rel in tree['files'] and all(tree['files'][rel][key] == expected[key] for key in ('sha256', 'bytes')),
                    'Selected raw artifact drift: ' + path)
        entries.append(dict(kind='raw_trial', task=row['task'], cell=row['cell'], source=source,
                            destination=source, status=row['status'], reward=row['reward'], raw_reward=row['raw_reward'], tree=tree))
    if report:
        source = workspace_path(report)
        tree = inventory(source)
        require(not tree['is_directory'], 'Report must be one existing regular file')
        entries.append(dict(kind='supplied_report', source=source, destination='report/' + Path(source).name, tree=tree))
    destinations(entries)
    captured = {}
    input_dir = plan_path.with_suffix('.inputs')
    require(not input_dir.exists(), 'Plan input snapshot already exists')
    input_dir.mkdir(parents=True)
    for name, content in documents.items():
        destination = input_dir / (sha(content) + '.json')
        if not destination.exists():
            destination.write_bytes(content)
        captured[name] = dict(path=destination.relative_to(ROOT).as_posix(), sha256=sha(content), bytes=len(content))
    selected_names = {row['trial'] for row in selected}
    case_names = {t['path'] for case in cases['cases'] for t in case['trials']}
    require(all(p in {r['trial'] for r in data['trials'] if r['task'] in RETAINED} for p in case_names),
            'Shortlist case is not a counted terminal retained-task trial')
    required_supporting.update(case_names - selected_names)
    # Required sources may be referenced more than once; copy each once, retaining its workspace path.
    required_supporting = {p for p in required_supporting if not any(
        Path(p).is_relative_to(q) and p != q for q in required_supporting)}
    extra = []
    for value in [*sorted(required_supporting), *supporting]:
        source = workspace_path(value)
        require(not (ROOT / source).is_relative_to(PACKAGING) and not PACKAGING.is_relative_to(ROOT / source),
                'Supporting source cannot contain packaging outputs')
        extra.append(dict(source=source, tree=inventory(source)))
    destinations([dict(**e, destination='workspace/' + e['source']) for e in extra], {'workspace'})
    evidence_tree = next(e['tree'] for e in extra if e['source'] == (BASE / 'evidence').relative_to(ROOT).as_posix())
    for key, name in (('cases', 'shortlist-cases.json'), ('final_controls', 'shortlist-controls-final.json')):
        require(evidence_tree['files'][name]['sha256'] == sha(documents[key]), 'Presentation evidence changed while planning')
    plan = dict(schema_version=1, created_at=datetime.now(timezone.utc).isoformat(),
                state='review_plan_only', coverage=coverage, frozen_scope_sha256=SCOPE_SHA,
                retained=retained, historical_retained=RETAINED, final_revision_evidence=revision_evidence,
                selection=data['selection'], entries=entries, captured_inputs=captured,
                selected_data=selected_data,
                task_inventory=dict(roots=list(TASK_ROOTS), paths=built_tasks, count=len(built_tasks)),
                required_supporting_sources=sorted(required_supporting),
                supporting_sources=extra, report=dict(supplied=bool(report), authorship_verified=False,
                requirement='The supplied brief requires one report with human-written prose. This script never writes report prose.'),
                readiness=dict(coverage_gate=coverage['ready'], report_supplied=bool(report),
                exact_control_evidence={x['task_id']: x['exact_revision_controls_pass'] for x in controls},
                case_trials_not_in_first_result_pack=sorted(case_names-selected_names)),
                sizes=dict(task_directories=sum(e['kind'].endswith('task') for e in entries),
                retained_task_directories=3, archived_task_directories=len(built_tasks)-3, raw_trials=len(selected),
                files=sum(len(e['tree']['files']) for e in entries), uncompressed_bytes=sum(
                    f['bytes'] for e in entries for f in e['tree']['files'].values()),
                final_raw_trials_expected=27, compressed_bytes_estimate=None,
                selected_data_files=len(selected_data['files']) if selected_data else 0,
                selected_data_uncompressed_bytes=sum(r['bytes'] for r in selected_data['files'].values()) if selected_data else 0,
                supporting_files=sum(len(e['tree']['files']) for e in extra),
                supporting_uncompressed_bytes=sum(f['bytes'] for e in extra for f in e['tree']['files'].values())),
                preservation=preservation)
    plan['plan_sha256'] = sha(canonical(plan))
    return plan


def inside_packaging(path):
    path = Path(path).absolute()
    require(path.is_relative_to(PACKAGING) and path != PACKAGING and path.resolve() == path,
            'Outputs must be real paths below presentation/packaging')
    return path


def verify_plan(plan):
    expected = plan['plan_sha256']
    require(sha(canonical({k: v for k, v in plan.items() if k != 'plan_sha256'})) == expected, 'Plan digest mismatch')
    documents = captured_documents(plan)
    verify_selected_data(plan, documents)
    data, scope = json.loads(documents['results']), json.loads(documents['scope'])
    first, task_maps, coverage = verify_selection(data, scope, documents)
    mapping = json.loads(documents['final_revision_mapping']) if 'final_revision_mapping' in documents else None
    retained, revision_sources, revision_evidence = final_revision_mapping(mapping, first, task_maps)
    require(plan['retained'] == retained and plan.get('historical_retained', RETAINED) == RETAINED
            and plan.get('final_revision_evidence', []) == revision_evidence, 'Final revision lineage mapping differs')
    controls = json.loads(documents['final_controls'])
    required_supporting = control_sources(controls, task_maps)
    required_supporting.update(revision_sources)
    required_supporting.add((BASE / 'evidence').relative_to(ROOT).as_posix())
    require(coverage == plan['coverage'], 'Frozen plan coverage differs')
    require(coverage['ready'] and coverage['covered'] == coverage['required'] == 99,
            f"99-case packaging gate is incomplete ({coverage['covered']}/99); --plan-only is available")
    selected = {x['trial'] for x in first if x['task'] in RETAINED}
    cases = json.loads(documents['cases'])
    required_supporting.update(t['path'] for c in cases['cases'] for t in c['trials'] if t['path'] not in selected)
    actual_supporting = {e['source'] for e in plan['supporting_sources']}
    require(all(any(Path(p).is_relative_to(q) for q in actual_supporting) for p in required_supporting),
            'Required final controls/case/evidence source is missing')
    require({e['source'] for e in plan['entries'] if e['kind'] == 'raw_trial'} == selected and len(selected) == 27,
            'Pack must contain all 27 first-counted retained-task trials')
    retained_map = {value: 'samples/' + Path(value).name for value in retained.values()}
    built_tasks = mapped_task_paths(retained)
    require(plan['task_inventory'] == dict(roots=list(TASK_ROOTS), paths=built_tasks, count=len(built_tasks)),
            'Built task inventory changed since planning')
    archive_map = {source: 'archive/' + Path(source).name for source in built_tasks if source not in retained_map}
    for kind, expected_map in (('retained_task', retained_map), ('discarded_task', archive_map)):
        actual = [e for e in plan['entries'] if e['kind'] == kind]
        require(len(actual) == len(expected_map) and {e['source']: e['destination'] for e in actual} == expected_map,
                'Task archive/retention mapping differs')
    require(all(e['destination'] == e['source'] for e in plan['entries'] if e['kind'] == 'raw_trial'),
            'Raw trial destinations must retain original job/trial names')
    require(all(e['kind'] in {'retained_task', 'discarded_task', 'raw_trial', 'supplied_report'} for e in plan['entries']),
            'Unknown pack entry kind')
    reports = [e for e in plan['entries'] if e['kind'] == 'supplied_report']
    require(len(reports) <= 1 and bool(reports) == plan['report']['supplied'] and all(
            e['destination'] == 'report/' + Path(e['source']).name and not e['tree']['is_directory'] for e in reports),
            'Report must remain one supplied file')
    destinations(plan['entries'])
    destinations([dict(**e, destination='workspace/' + e['source']) for e in plan['supporting_sources']], {'workspace'})
    for entry in plan['entries'] + plan['supporting_sources']:
        require(bytes_identity(inventory(entry['source'])) == bytes_identity(entry['tree']),
                'Source hash drift since planning: ' + entry['source'])
    for tasks in task_maps.values():
        for name, definition in tasks.items():
            if name in data['tasks']:
                entry = next(e for e in plan['entries'] if e['source'] == definition['path'])
                require(task_digest(entry['tree']) == definition.get('task_sha256', definition.get('validated_task_sha256')),
                        'Comparison task differs from the frozen campaign revision')


def copy_entry(entry, output):
    source = entry['source']
    dest = output / relative(entry['destination'])
    tree = entry['tree']
    if tree['is_directory']:
        for directory in tree['directories']:
            (dest / directory).mkdir(parents=True, exist_ok=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
    for name, expected in tree['files'].items():
        src = source + ('/' + name if name else '')
        target = dest / name if name else dest
        h, size = hashlib.sha256(), 0
        with safe_open(src) as incoming, target.open('xb') as outgoing:
            for block in iter(lambda: incoming.read(1024*1024), b''):
                h.update(block); size += len(block); outgoing.write(block)
            os.fchmod(outgoing.fileno(), expected['mode'])
        require(h.hexdigest() == expected['sha256'] and size == expected['bytes'], 'Source changed during copy: ' + src)
        os.utime(target, ns=(expected['mtime_ns'], expected['mtime_ns']))


def zip_verified(directory, archive):
    require(not archive.exists(), 'Archive destination exists')
    expected = {}
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6, strict_timestamps=False) as z:
        for p in sorted(directory.rglob('*')):
            name = p.relative_to(directory).as_posix() + ('/' if p.is_dir() else '')
            require(name not in expected, 'Duplicate ZIP destination')
            if p.is_dir():
                z.write(p, name)
                expected[name] = None
            else:
                expected[name] = file_info(p.relative_to(ROOT).as_posix())['sha256']
                z.write(p, name)
    with zipfile.ZipFile(archive) as z:
        require(len(z.namelist()) == len(set(z.namelist())) and set(z.namelist()) == set(expected), 'ZIP entry mismatch')
        for name, checksum in expected.items():
            if checksum is not None:
                h = hashlib.sha256()
                with z.open(name) as stream:
                    for block in iter(lambda: stream.read(1024*1024), b''):
                        h.update(block)
                require(h.hexdigest() == checksum, 'ZIP byte mismatch: ' + name)
    return file_info(archive.relative_to(ROOT).as_posix())


def assemble(plan, output):
    verify_plan(plan)
    before = original_materials()
    require(not output.exists(), 'Output directory exists')
    archive = output.with_name(output.name + '.zip')
    require(not archive.exists(), 'ZIP output exists')
    proof_path = output.parent / (output.name + '.verification.json')
    supporting = output.parent / (output.name + '-supporting-evidence')
    supporting_archive = supporting.with_name(supporting.name + '.zip')
    for path in (proof_path, supporting, supporting_archive):
        require(not path.exists(), 'Packaging output already exists: ' + path.name)
    staging = Path(tempfile.mkdtemp(prefix='.' + output.name + '-', dir=output.parent))
    try:
        for root in ('samples', 'jobs', 'archive', 'report'):
            (staging / root).mkdir()
        for entry in plan['entries']:
            copy_entry(entry, staging)
            copied = inventory((staging / entry['destination']).relative_to(ROOT).as_posix())
            require(bytes_identity(copied) == bytes_identity(entry['tree']), 'Copied bytes differ')
        # Recheck all sources after copying; no raw file is rewritten or renamed.
        verify_plan(plan)
        require(len(list((staging / 'jobs').glob('*/*/result.json'))) == 27, 'Raw result nesting/count differs')
        staging.rename(output)
        zipped = zip_verified(output, archive)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    proof = dict(created_at=datetime.now(timezone.utc).isoformat(), state='review_pack',
                 plan_sha256=plan['plan_sha256'], copied_bytes_verified=True, zip_bytes_verified=True,
                 zip=dict(path=archive.relative_to(ROOT).as_posix(), **zipped),
                 report=plan['report'], report_ready=False,
                 note='Assembly is a review pack. Human authorship and customer-facing report content are not machine verified.',
                 preservation_before=before, preservation_after=original_materials())
    # Provenance and optional presentation assets are outside the strict four-root ZIP.
    supporting.mkdir()
    captured = set()
    for value in plan['captured_inputs'].values():
        if value['path'] in captured:
            continue
        captured.add(value['path'])
        tree = inventory(value['path'])
        require(all(tree['files'][''][key] == value[key] for key in ('sha256', 'bytes')),
                'Frozen provenance changed before supporting copy')
        copy_entry(dict(source=value['path'], destination='inputs/' + Path(value['path']).name,
                        tree=tree), supporting)
    copy_selected_data(plan, supporting)
    for value in plan['supporting_sources']:
        entry = dict(**value, destination='workspace/' + value['source'])
        copy_entry(entry, supporting)
        require(bytes_identity(inventory(supporting / entry['destination'])) == bytes_identity(entry['tree']),
                'Supporting copied bytes differ')
    verify_plan(plan)
    (supporting / 'packaging-plan.json').write_text(json.dumps(plan, indent=2, sort_keys=True) + '\n')
    (supporting / proof_path.name).write_text(json.dumps(proof, indent=2, sort_keys=True) + '\n')
    proof['supporting_zip'] = zip_verified(supporting, supporting_archive)
    # This final receipt is the completion marker; partial failures never publish it.
    with proof_path.open('x') as stream:
        stream.write(json.dumps(proof, indent=2, sort_keys=True) + '\n')
    return proof


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan-only', action='store_true', help='Verify sources and estimate size without copying a submission pack')
    p.add_argument('--plan', type=Path, default=PACKAGING / 'plan.json')
    p.add_argument('--output', type=Path, default=PACKAGING / 'review-pack')
    p.add_argument('--report', type=Path, help='Existing human-authored report file; copied exactly, never generated')
    p.add_argument('--data-dir', type=Path,
                   help='Collector data directory for --plan-only; default data or a separate data-snapshots directory')
    p.add_argument('--final-revision-mapping', type=Path,
                   help='Optional fully validated Zenoh correction mapping; all paired evidence must already exist')
    p.add_argument('--supporting-source', action='append', default=[], type=Path,
                   help='Optional finalized deck/CSV/evidence outside the strict submission ZIP')
    args = p.parse_args()
    plan_path = inside_packaging(args.plan)
    PACKAGING.mkdir(parents=True, exist_ok=True)
    if args.plan_only:
        require(not plan_path.exists(), 'Plan exists; use a new plan filename to preserve provenance')
        plan = make_plan(plan_path, args.report, args.supporting_source, args.final_revision_mapping, args.data_dir)
        plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + '\n')
        print(json.dumps(dict(plan=plan_path.relative_to(ROOT).as_posix(), coverage=plan['coverage'],
                             sizes=plan['sizes'], report=plan['report'], plan_sha256=plan['plan_sha256']), indent=2))
    else:
        require(args.report is None and not args.supporting_source and args.final_revision_mapping is None and args.data_dir is None,
                'Freeze report/supporting/revision/data inputs with --plan-only first')
        plan = json.loads(read(plan_path.relative_to(ROOT).as_posix()))
        proof = assemble(plan, inside_packaging(args.output))
        print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        raise SystemExit('Packaging stopped: ' + str(error))
