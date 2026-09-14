#!/usr/bin/env python3
"""Read-only independent first-99 audit; writes only its own concise receipt."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
BASE = ROOT / 'research/takehome-presentation-2026-09-14'
SNAP = BASE / 'data-snapshots/99-20260914T1319'
COUNTED = {'scored', 'timeout', 'verifier_timeout'}
REFERENCES = {}


def digest_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    assert path.is_file() and not path.is_symlink(), str(path)
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), str(path)
    info = {'path': str(path.relative_to(ROOT)), 'sha256': digest_bytes(raw), 'bytes': len(raw)}
    REFERENCES[info['path']] = info
    return raw, info


def js(path):
    raw, info = read(path)
    return json.loads(raw), info


def key(row):
    model = row['model']
    if not model.startswith('anthropic/'):
        model = 'anthropic/claude-' + model
    return json.dumps([row['task'], model, row['effort']], separators=(',', ':'))


def instant(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    assert value.tzinfo
    return value.astimezone(timezone.utc)


def stats(rows):
    steps = [r['assistant_steps'] for r in rows if r['assistant_steps'] is not None]
    successes = [r['assistant_steps'] for r in rows if r['reward'] == 1 and r['assistant_steps'] is not None]
    return {'counted': len(rows), 'passes': sum(r['reward'] == 1 for r in rows),
        'statuses': dict(Counter(r['status'] for r in rows)), 'steps_available': len(steps),
        'median_assistant_steps': statistics.median(steps) if steps else None,
        'over_75_steps': sum(n > 75 for n in steps),
        'successful_steps_available': len(successes),
        'min_successful_assistant_steps': min(successes) if successes else None,
        'median_successful_assistant_steps': statistics.median(successes) if successes else None,
        'max_successful_assistant_steps': max(successes) if successes else None,
        'successful_over_75_steps': sum(n > 75 for n in successes),
        'recorded_cost_usd': sum(r.get('cost_usd') or 0 for r in rows),
        'cost_censored_trials': sum(r['usage_censored'] is not False for r in rows)}


def check_stats(actual, expected):
    assert actual.keys() == expected.keys()
    for k in actual:
        if k == 'recorded_cost_usd':
            assert math.isclose(actual[k], expected[k], abs_tol=1e-8)
        else:
            assert actual[k] == expected[k], (k, actual[k], expected[k])


def counted_checks(trial, unit):
    if unit == 'verifier_groups':
        score, _ = js(trial / 'verifier/score.json')
        groups = score.get('groups', {})
        if not groups:
            return None, None
        passed = 0
        for g in groups.values():
            explicit = next((g[k] for k in ('ok', 'pass', 'passed_group') if k in g), None)
            if explicit is not None:
                passed += explicit is True
            elif 'status' in g:
                passed += g['status'] == 'pass'
            else:
                passed += bool(g.get('total')) and g.get('passed') == g['total']
        return passed, len(groups)
    xml = trial / 'verifier/results.xml'
    if xml.exists():
        raw, _ = read(xml)
        cases = list(ET.fromstring(raw).iter('testcase'))
        if cases:
            return sum(not any(c.find(t) is not None for t in ('failure', 'error', 'skipped'))
                       for c in cases), len(cases)
    cargo = trial / 'verifier/cargo-tests.log'
    if cargo.exists():
        raw, _ = read(cargo)
        outcomes = re.findall(r'^test .+ \.\.\. (ok|FAILED|ignored)\s*$', raw.decode(), re.M)
        if outcomes:
            return outcomes.count('ok'), len(outcomes)
    return None, None


def canonical_snapshot(snapshot):
    payload = {k: v for k, v in snapshot.items() if k != 'sha256'}
    assert digest_bytes(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                  allow_nan=False).encode()) == snapshot['sha256']


def main():
    assert not (OUT / 'review.json').exists(), 'Never overwrite receipt'
    started = datetime.now(timezone.utc).isoformat()
    data, data_ref = js(SNAP / 'results.json')
    snap_map = {str(p.relative_to(SNAP)): digest_bytes(p.read_bytes())
                for p in sorted(SNAP.rglob('*')) if p.is_file()}
    def captured(ref):
        path = SNAP / ref['snapshot']
        assert path.resolve().is_relative_to(SNAP.resolve())
        value, observed = js(path)
        assert observed['sha256'] == ref['sha256']
        assert path.name == ref['sha256'] + '.json'
        return value
    scope = captured(data['scope_source'])
    assert data['scope_sha256'] == data['scope_source']['sha256']
    assert scope['count'] == len(scope['cells']) == 99
    assert all(key(value) == cell for cell, value in scope['cells'].items())
    tasks = {v['task'] for v in scope['cells'].values()}
    expected_cells = {json.dumps(list(v), separators=(',', ':')) for v in itertools.product(
        tasks, ['anthropic/claude-fable-5-1', 'anthropic/claude-opus-5', 'anthropic/claude-sonnet-5'],
        ['medium', 'high', 'max'])}
    assert len(tasks) == 11 and set(scope['cells']) == expected_cells
    assert set(data['counted_statuses']) == COUNTED and data['timeouts_are_failures'] is True
    _, collector_ref = read(ROOT / 'scripts/collect-takehome-evidence.py')
    assert collector_ref['sha256'] == data['collector_sha256']
    plans = {campaign: captured(ref) for campaign, ref in data['campaign_plans'].items()}
    assert len(plans) == len(data['sources']) == 4
    summary_rows = []
    plan_configs = {}
    for campaign, plan in plans.items():
        for job in plan['jobs']:
            _, ref = read(ROOT / job['config'])
            assert ref['sha256'] == job['sha256']
            plan_configs[job['name']] = ref
        source = data['sources']['jobs/' + campaign + '.summary.json']
        summary = captured(source)
        assert summary['job'] == campaign and source['updated_at'] == summary['updated_at']
        rows = [r for r in summary['trials'] if key(r) in scope['cells']
                and scope['cells'][key(r)]['job'] == campaign and r['status'] in COUNTED]
        counts = Counter(key(r) for r in rows)
        for setting in summary['settings']:
            if key(setting) in scope['cells']:
                assert counts[key(setting)] == setting['completed']
        summary_rows.extend(rows)
    declared = {r['trial']: r for r in data['trials']}
    assert len(declared) == len(data['trials']) == len(summary_rows) == 207
    assert set(declared) == {r['trial'] for r in summary_rows}
    grouped = defaultdict(list)
    for r in summary_rows:
        projected = declared[r['trial']]
        for name in ('task', 'model', 'effort', 'status', 'finished_at', 'reward', 'raw_reward',
                     'assistant_steps', 'tool_calls', 'checks_passed', 'checks_total', 'cost_usd'):
            assert projected[name] == r.get(name), (r['trial'], name)
        grouped[key(r)].append(r)
    earliest = {cell: min(rows, key=lambda r: (instant(r['finished_at']), r['trial']))['trial']
                for cell, rows in grouped.items()}
    first = [r for r in data['trials'] if r['first_counted_result']]
    assert len(first) == 99 and {r['cell']: r['trial'] for r in first} == earliest
    assert data['coverage'] == {'covered': 99, 'required': 99, 'ready': True, 'missing': []}
    check_stats(stats(first), data['first_results'])
    check_stats(stats(data['trials']), data['all_counted'])
    for task, metrics in data['tasks'].items():
        check_stats(stats([r for r in first if r['task'] == task]), metrics['first_results'])
        check_stats(stats([r for r in data['trials'] if r['task'] == task]), metrics['all_counted'])
        for model, expected in metrics['by_model'].items():
            check_stats(stats([r for r in first if r['task'] == task and r['model'] == model]), expected)
    audited = []
    artifact_count = artifact_bytes = 0
    for row in first:
        trial = ROOT / row['trial']
        result, result_ref = js(trial / 'result.json')
        evidence, ev_ref = js(trial / 'benchmark-evidence.json')
        pre, pre_ref = js(trial / 'benchmark-snapshot.json')
        native, native_ref = js(trial / 'agent/mini-swe-agent.trajectory.json')
        atif, atif_ref = js(trial / 'agent/trajectory.json')
        plan = plans[row['campaign']]
        jobs = {j['name']: j for j in plan['jobs']}
        assert trial.parent.name in jobs
        if trial.parent.name != row['campaign']:
            assert any(a['trial'] == row['trial'] and a['declaration'] == jobs[trial.parent.name]
                       for a in data['adopted_jobs'])
            assert jobs[trial.parent.name].get('replacement_for')
        task = next(t for t in plan['tasks'] if t['id'] == row['task'])
        assert result['task_checksum'] == row['task_checksum'] == task.get(
            'harbor_task_checksum', task.get('validated_harbor_task_checksum'))
        cfg = result['config']['agent']
        assert cfg['name'] == 'mini-swe-agent' and cfg['kwargs']['version'] == '2.4.6'
        assert cfg['model_name'] == 'anthropic/claude-' + row['model']
        assert cfg['kwargs']['reasoning_effort'] == row['effort']
        assert result['finished_at'] == row['finished_at']
        assert result['task_name'] == row['task']
        raw_reward = (result.get('verifier_result') or {}).get('rewards', {}).get('reward')
        assert row['raw_reward'] == raw_reward
        if row['status'] == 'scored':
            assert row['reward'] == raw_reward
        elif row['status'] == 'verifier_timeout':
            assert row['reward'] == 0
        else:
            assert row['reward'] == int(raw_reward == 1)
        marker, _ = read(trial / 'verifier/reward.txt')
        assert float(marker) == (raw_reward if raw_reward is not None else 0)
        assert counted_checks(trial, row['check_unit']) == (row['checks_passed'], row['checks_total'])
        native_turns = sum(m.get('role') == 'assistant' for m in native['messages'])
        atif_turns = sum(s.get('source') == 'agent' for s in atif['steps'])
        atif_tools = sum(len(s.get('tool_calls') or []) for s in atif['steps'] if s.get('source') == 'agent')
        native_tools = sum(len(m.get('tool_calls') or []) for m in native['messages'] if m.get('role') == 'assistant')
        assert native_turns == atif_turns == row['assistant_steps'] == evidence['counts']['atif_agent_steps']
        assert native_tools == atif_tools == row['tool_calls'] == evidence['counts']['atif_tool_calls']
        assert evidence['request'] == row['request'] and evidence['request']['effort_matches'] is True
        assert row['usage_censored'] == evidence['usage']['usage_censored']
        canonical_snapshot(pre)
        canonical_snapshot(evidence['input_snapshot'])
        assert pre['sha256'] == evidence['pre_verifier_snapshot_check']['expected_sha256']
        assert evidence['pre_verifier_snapshot_check']['matches'] is True
        for name, expected in row['artifacts'].items():
            assert (ROOT / name).resolve().is_relative_to(trial.resolve())
            info = REFERENCES.get(name)
            if info is None:
                _, info = read(ROOT / name)
            assert info['sha256'] == expected['sha256'] and info['bytes'] == expected['bytes'], name
            bound = evidence['input_snapshot']['entries'].get(str((ROOT / name).relative_to(trial)))
            if bound and bound.get('kind') == 'file':
                assert info['sha256'] == bound['sha256'] and info['bytes'] == bound['size']
            artifact_count += 1
            artifact_bytes += info['bytes']
        audited.append({'cell': row['cell'], 'trial': row['trial'], 'status': row['status'],
            'reward': row['reward'], 'raw_reward': raw_reward, 'steps': native_turns, 'tool_calls': atif_tools,
            'checks': [row['checks_passed'], row['checks_total']], 'result': result_ref,
            'evidence': ev_ref, 'native': native_ref, 'atif': atif_ref,
            'native_exit_status': native.get('info', {}).get('exit_status'),
            'outer_exception': (result.get('exception_info') or {}).get('exception_type'),
            'usage_censored': row['usage_censored']})
    old, old_ref = js(BASE / 'data-snapshots/94-20260914T0620/results.json')
    shortlist = {'rs-rerun-chunk-optimizer', 'rs-burn-store-pytorch-reader-v4',
                 'rs-zenoh-timestamp-instrumentation-v3'}
    assert {r['trial'] for r in old['trials'] if r['first_counted_result'] and r['task'] in shortlist} == {
        r['trial'] for r in first if r['task'] in shortlist}
    assert {str(p.relative_to(SNAP)): digest_bytes(p.read_bytes())
            for p in sorted(SNAP.rglob('*')) if p.is_file()} == snap_map
    protected = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', 'c1ae968', '--', '.'],
                                        cwd=ROOT, text=True).splitlines()
    assert len(protected) == 36
    for name in protected:
        assert (ROOT / name).read_bytes() == subprocess.check_output(
            ['git', 'show', 'c1ae968:./' + name], cwd=ROOT)
    receipt = {'schema_version': 1, 'kind': 'independent_first99_snapshot_review',
        'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
        'passed_identity_selection_and_statistics_audit': True, 'clean_99_claim_supported': False,
        'source': data_ref, 'scope': data['scope_source'], 'snapshot_file_sha256': snap_map,
        'four_captured_plans_and_summaries_verified': True,
        'exact_11_tasks_by_3_models_by_3_efforts': True,
        'earliest_counted_finished_at_then_path_verified': True,
        'all_207_summary_projection_memberships_verified': True,
        'foxglove_adopted_job_explicitly_declared': data['adopted_jobs'],
        'first99_artifact_files_rehashed': artifact_count, 'first99_artifact_bytes_rehashed': artifact_bytes,
        'repeat_artifact_logs_rehashed': 0, 'first_results': stats(first),
        'all_counted_statistics_from_captured_summaries': stats(data['trials']),
        'task_first_result_statistics': {task: stats([r for r in first if r['task'] == task]) for task in sorted(tasks)},
        'original_shortlist27_unchanged_from94': True, 'prior94': old_ref,
        'audited_first99': audited,
        'caveats': ['Coverage99 follows the existing counted-status policy, not99 clean model completions.',
            'Diskcache Fable/max uuqa2f6 ended with native BadRequestError quota rejection after gateway retries; its counted scored0 is provider-confounded.',
            'Luigi Fable/max6ooqjT5 is a counted agent timeout with gateway-retry evidence; its0 is not clean functional headroom.',
            'Preserve raw scores, frozen selections and quota/timeout evidence; no classifier or cohort change is made here.',
            'Original Zenohv3 scores are not final-revision paired-regrade results; final whole-grader results and controls require separate provenance.',
            'No causal replay, model ranking inference, full submitted-source-tree rehash or paid call was performed.'],
        'source_sha256': digest_bytes(Path(__file__).read_bytes()), 'snapshot_unchanged': True,
        'supplied_original_files_unchanged': 36, 'model_calls': 0, 'replays': 0,
        'task_runtime_classifier_control_edits': False}
    with (OUT / 'review.json').open('x') as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write('\n')
    print(json.dumps({'passed': True, 'review_sha256': digest_bytes((OUT / 'review.json').read_bytes()),
        'first99_artifact_files': artifact_count, 'artifact_bytes': artifact_bytes,
        'first_statistics': stats(first), 'shortlist27_unchanged': True}, indent=2))


if __name__ == '__main__':
    main()
