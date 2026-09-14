#!/usr/bin/env python3
"""Read-only inventory of restaurant trial evidence; writes separate audit data.

Never dumps messages, prompts, environment values or credentials. Does not run
agents, modify jobs, interpret correctness, or relabel infrastructure failures.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'research/restaurant-evidence-audit-2026-09-13'


def read(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def present(path):
    return path.is_file() and path.stat().st_size > 0


def summarize(rows):
    counts = Counter(row['state'] for row in rows)
    fields = ['result', 'atif', 'native', 'ctrf_five', 'verifier_stdout',
              'workbook', 'effort_request_matches', 'steps_match_native',
              'result_usage', 'result_cost', 'recovered_cost', 'stage_timing']
    return {'trials': len(rows), 'states': dict(counts),
            'coverage': {field: sum(row[field] is True for row in rows) for field in fields},
            'agent_steps': {'min': min((r['agent_steps'] for r in rows if r['agent_steps'] is not None), default=None),
                            'max': max((r['agent_steps'] for r in rows if r['agent_steps'] is not None), default=None)},
            'exceptions': dict(Counter(r['exception'] for r in rows if r['exception']))}


def main():
    records = []
    primary = list((ROOT / 'jobs').glob('*/restaurant-*/config.json'))
    archived = list((ROOT / 'jobs/recovery').rglob('restaurant-*/config.json'))
    for config_path in sorted(set(primary + archived)):
        trial = config_path.parent
        config = read(config_path) or {}
        result_path = trial / 'result.json'
        result = read(result_path)
        cfg = (result or {}).get('config') or config
        agent = cfg.get('agent') or {}
        if 'restaurant' not in str(cfg.get('task', {})) and 'restaurant' not in (result or {}).get('task_name', ''):
            continue
        exception = ((result or {}).get('exception_info') or {}).get('exception_type')
        reward = (((result or {}).get('verifier_result') or {}).get('rewards') or {}).get('reward')
        if agent.get('name') in ('oracle', 'nop'):
            state = 'control'
        elif not result or not result.get('finished_at'):
            state = 'incomplete'
        elif exception == 'AgentTimeoutError':
            state = 'agent_timeout'
        elif exception:
            state = 'other_exception'
        elif reward in (0, 1):
            state = 'scored_model'
        else:
            state = 'unscored_model'
        atif_path = trial / 'agent/trajectory.json'
        native_path = trial / 'agent/mini-swe-agent.trajectory.json'
        atif, native = read(atif_path), read(native_path)
        steps = [s for s in (atif or {}).get('steps', []) if s.get('source') == 'agent']
        messages = [m for m in (native or {}).get('messages', []) if m.get('role') == 'assistant']
        settings = (((native or {}).get('info') or {}).get('config') or {}).get('model', {}).get('model_kwargs', {})
        effective = (settings.get('output_config') or {}).get('effort')
        configured = (agent.get('kwargs') or {}).get('reasoning_effort')
        ar = (result or {}).get('agent_result') or {}
        final = (atif or {}).get('final_metrics') or {}
        native_stats = ((native or {}).get('info') or {}).get('model_stats') or {}
        ctrf = read(trial / 'verifier/ctrf.json') or {}
        tests = (ctrf.get('results') or {}).get('tests') or []
        workbook = trial / 'artifacts/root/audit_report.xlsx'
        manifest = read(trial / 'artifacts/manifest.json') or []
        recovered_cost = final.get('total_cost_usd', native_stats.get('instance_cost'))
        r = {'path': str(trial.relative_to(ROOT)), 'archived': 'recovery' in trial.relative_to(ROOT / 'jobs').parts,
             'trial_id': (result or {}).get('id'), 'state': state, 'model': agent.get('model_name'),
             'agent': agent.get('name'), 'effort_configured': configured, 'effort_request': effective,
             'effort_request_matches': effective == configured if effective is not None and configured is not None else None,
             'thinking_adaptive': settings.get('thinking') == {'type': 'adaptive'} if settings else None,
             'request_max_tokens': settings.get('max_tokens'), 'task_checksum': (result or {}).get('task_checksum'),
             'reward': reward, 'exception': exception, 'result': result is not None,
             'result_sha256': hashlib.sha256(result_path.read_bytes()).hexdigest() if result else None,
             'finished_at': (result or {}).get('finished_at'), 'atif': atif is not None, 'native': native is not None,
             'agent_steps': len(steps) if atif is not None else None,
             'native_assistant_turns': len(messages) if native is not None else None,
             'steps_match_native': len(steps) == len(messages) if atif is not None and native is not None else None,
             'tool_calls': sum(len(s.get('tool_calls') or []) for s in steps) if atif is not None else None,
             'steps_with_timestamps': sum(bool(s.get('timestamp')) for s in steps) if atif else None,
             'steps_with_metrics': sum(bool(s.get('metrics')) for s in steps) if atif else None,
             'steps_with_observations': sum(bool(s.get('observation')) for s in steps) if atif else None,
             'api_calls': native_stats.get('api_calls'), 'agent_version': (result or {}).get('agent_info', {}).get('version'),
             'result_usage': all(ar.get(k) is not None for k in ['n_input_tokens', 'n_output_tokens', 'n_cache_tokens']),
             'result_cost': ar.get('cost_usd') is not None, 'cost_usd': ar.get('cost_usd'),
             'recovered_cost': recovered_cost is not None, 'trajectory_cost_usd': recovered_cost,
             'native_final_cost_usd': native_stats.get('instance_cost'),
             'note_cost': 'Result/ATIF may be an earlier timeout snapshot; native final cost can include post-deadline work.',
             'usage': {k: ar.get(k) for k in ['n_input_tokens', 'n_output_tokens', 'n_cache_tokens']},
             'ctrf_five': len(tests) == 5 and all(t.get('status') in ('passed', 'failed') for t in tests),
             'tests': [{'name': t.get('name'), 'status': t.get('status')} for t in tests],
             'verifier_stdout': present(trial / 'verifier/test-stdout.txt'),
             'workbook': present(workbook),
             'workbook_manifest_status': next((x.get('status') for x in manifest if x.get('source') == '/root/audit_report.xlsx'), None),
             'stage_timing': all(((result or {}).get(k) or {}).get('started_at') and ((result or {}).get(k) or {}).get('finished_at') for k in ['agent_execution', 'verifier'])}
        records.append(r)
    current = [r for r in records if not r['archived']]
    groups = defaultdict(list)
    for r in current:
        groups[r['state']].append(r)
    by_job = defaultdict(list)
    for r in current:
        by_job[r['path'].split('/')[1]].append(r)
    summary = {'snapshot_at': datetime.now(timezone.utc).isoformat(), 'primary': summarize(current),
               'archived': summarize([r for r in records if r['archived']]),
               'by_state': {k: summarize(v) for k, v in groups.items()},
               'by_job': {k: summarize(v) for k, v in by_job.items()},
               'mismatches': [{'path': r['path'], 'effort_request_matches': r['effort_request_matches'],
                                'steps_match_native': r['steps_match_native']} for r in records
                              if r['effort_request_matches'] is False or r['steps_match_native'] is False]}
    OUT.mkdir(exist_ok=True)
    (OUT / 'inventory.json').write_text(json.dumps({'summary': summary, 'trials': records}, indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'by_job'}, indent=2))


if __name__ == '__main__':
    main()
