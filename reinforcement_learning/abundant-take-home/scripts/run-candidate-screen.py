#!/usr/bin/env python3
"""Run a locked candidate/model/effort screen through one resource-aware queue."""
import argparse
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
import xml.etree.ElementTree as ET

from dotenv import load_dotenv
from harbor.models.job.config import JobConfig
from benchmark_recovery import memory_snapshot, memory_block_reason, prepare_resume
from benchmark_evidence import attach_to_record, EvidenceError

ROOT = Path(__file__).resolve().parents[1]
TASKS = ['luigi-generation-target', 'burn-scoped-checkpoint-remap',
         'sqlite-utils-schema-plan', 'diskcache-online-reshard', 'huey-sqlite-leases']
MODELS = ['fable-5-1', 'opus-5', 'sonnet-5']
EFFORTS = ['medium', 'high', 'max']


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


candidate = module('candidate_bench', 'candidate-bench.py')
common = module('confirmation', 'run-sonnet-confirmation.py')
dump, read_json = common.dump, common.read_json


def agent(model, effort):
    return {'name': 'mini-swe-agent', 'model_name': f'anthropic/claude-{model}',
            'kwargs': {'reasoning_effort': effort, 'version': '2.4.6'}}


def config(name, tasks, agents, attempts, workers):
    return JobConfig.model_validate({
        'job_name': name, 'jobs_dir': 'jobs', 'quiet': True,
        'n_attempts': attempts, 'n_concurrent_trials': workers,
        'retry': {'max_retries': 0}, 'agents': agents,
        'tasks': [{'path': task['path'] if isinstance(task, dict) else f'candidates/{task}'} for task in tasks],
    }).model_dump(mode='json')


def checks(directory, check_format=None):
    """Read existing verifier output; never regrade or modify the task/verifier."""
    if check_format == 'verifier_groups':
        score = read_json(directory / 'verifier/score.json')
        groups = score.get('groups', {})
        def passed(group):
            for key in ('ok', 'pass', 'passed_group'):
                if key in group:
                    return group[key] is True
            if 'status' in group:
                return group['status'] == 'pass'
            return bool(group.get('total')) and group.get('passed') == group['total']
        return (sum(passed(g) for g in groups.values()), len(groups)) if groups else (None, None)
    xml = directory / 'verifier/results.xml'
    if xml.exists():
        try:
            cases = list(ET.parse(xml).getroot().iter('testcase'))
            if cases:
                failed = sum(c.find('failure') is not None or c.find('error') is not None for c in cases)
                skipped = sum(c.find('skipped') is not None for c in cases)
                return len(cases) - failed - skipped, len(cases)
        except (OSError, ET.ParseError):
            pass
    cargo = directory / 'verifier/cargo-tests.log'
    if cargo.exists():
        outcomes = re.findall(r'^test .+ \.\.\. (ok|FAILED|ignored)\s*$', cargo.read_text(), re.M)
        if outcomes:
            return outcomes.count('ok'), len(outcomes)
    return None, None


def inspect(path, task_map, models=MODELS):
    path = Path(path).resolve()
    result = read_json(path)
    if not result.get('finished_at'):
        return None
    task = result['task_name']
    cfg = result['config']
    configured = cfg['agent']
    model = configured['model_name'].removeprefix('anthropic/claude-')
    effort = configured['kwargs']['reasoning_effort']
    assert task in task_map and model in models and effort in EFFORTS
    assert configured['model_name'] == f'anthropic/claude-{model}'
    assert configured['kwargs']['version'] == '2.4.6'
    expected = task_map[task]
    assert result['task_checksum'] == expected.get('harbor_task_checksum', expected.get('validated_harbor_task_checksum'))
    assert cfg['timeout_multiplier'] == 1 and not cfg.get('extra_instruction_paths')
    assert not configured.get('override_timeout_sec') and not configured.get('max_timeout_sec')
    assert not configured.get('override_setup_timeout_sec')
    assert all(v is None for k, v in cfg.items() if k.endswith('_timeout_multiplier'))
    assert all(v is None for k, v in cfg['environment'].items() if k.startswith('override_'))
    assert not cfg['verifier'].get('disable')
    assert not cfg['verifier'].get('override_timeout_sec') and not cfg['verifier'].get('max_timeout_sec')
    exception = result.get('exception_info') or {}
    error = exception.get('exception_type')
    reward = ((result.get('verifier_result') or {}).get('rewards') or {}).get('reward')
    record = {'trial': str(path.parent.relative_to(ROOT)), 'task': task,
              'model': model, 'effort': effort, 'status': 'review',
              'finished_at': result['finished_at'],
              'exception': error, 'raw_reward': reward, 'reward': None,
              'checks_passed': None, 'checks_total': None,
              'check_unit': expected.get('check_format', 'individual_checks'),
              'cost_usd': (result.get('agent_result') or {}).get('cost_usd')}
    evidence_ready = attach_to_record(record, path.parent)
    if evidence_ready is None:
        return None
    if not evidence_ready:
        return record
    trace = read_json(path.parent / 'agent/mini-swe-agent.trajectory.json')
    exit_status = trace.get('info', {}).get('exit_status')
    if error and error not in {'AgentTimeoutError', 'VerifierTimeoutError'}:
        before_agent = not (result.get('agent_execution') or {}).get('started_at')
        gateway = re.search(r'BadGatewayError|RateLimitError|APIConnectionError|APITimeoutError|'
                            r'ServiceUnavailableError|InternalServerError|502 Bad Gateway|503 Service Unavailable',
                            exception.get('exception_message', ''))
        if before_agent or error in common.INFRA_ERRORS or gateway or exit_status in common.INFRA_ERRORS:
            record['status'] = 'infrastructure'
            record['infrastructure_detail'] = exit_status or error
        return record
    settings = trace.get('info', {}).get('config', {}).get('model', {}).get('model_kwargs')
    if settings is None:
        assert error == 'AgentTimeoutError', 'Missing effective request configuration'
        record['effective_config_missing_after_timeout'] = True
    else:
        assert settings['output_config']['effort'] == effort
        assert settings['thinking'] == {'type': 'adaptive'}
        assert settings['max_tokens'] == 64000
    if error is None:
        assert result['agent_info']['version'] == '2.4.6' and reward in (0, 1)
    passed, total = checks(path.parent, expected.get('check_format'))
    if error == 'VerifierTimeoutError':
        # Success requires finishing the unchanged verifier within its original
        # budget. Preserve raw_reward=None and report budget failures separately;
        # replacing these trials would inflate the estimated success rate.
        # A timeout before any test progress still needs infrastructure review.
        output_path = path.parent / 'verifier/test-stdout.txt'
        marker_path = path.parent / 'verifier/reward.txt'
        output = output_path.read_text() if output_path.exists() else ''
        marker = marker_path.read_text().strip() if marker_path.exists() else ''
        progress_failure = any(re.fullmatch(r'[.FE]+', line.strip()) and
                               any(char in line for char in 'FE') for line in output.splitlines())
        progress = any(re.fullmatch(r'[.FEsxX]+', line.strip()) for line in output.splitlines())
        assert marker == '0' and (total or progress), 'Verifier timeout lacks evidence of test execution'
        assert exit_status == 'Submitted' and (path.parent / 'artifacts/submission').is_dir(), 'Verifier timeout lacks a submitted implementation'
        record.update(status='verifier_timeout', reward=0,
                      timeout_stage='verifier', outcome_basis='original_verifier_budget_exhausted',
                      failed_checks_before_timeout=bool((total and passed < total) or progress_failure),
                      checks_passed=passed, checks_total=total)
        return record
    if reward == 1:
        assert total and passed == total, 'Passing reward lacks passing check evidence'
        assert (path.parent / 'artifacts').is_dir(), 'Missing submitted source artifacts'
        if expected.get('check_format') == 'verifier_groups':
            score = read_json(path.parent / 'verifier/score.json')
            assert score.get('reward') == 1
            assert set(expected['expected_verifier_groups']) <= set(score['groups']), 'Passing result omitted verifier groups'
    record.update(status='timeout' if error else 'scored', reward=int(reward == 1),
                  checks_passed=passed, checks_total=total)
    return record


def verify_tasks(plan):
    for task in plan['tasks']:
        if task.get('validation_basis') == 'saved_controls_with_metadata_updates':
            assert candidate.digest_task(ROOT/task['path']) == task['task_sha256'], task['id'] + ': task files changed'
            for kind, reward in (('oracle', 1), ('nop', 0)):
                path = ROOT/task['validation_evidence'][kind]
                assert hashlib.sha256(path.read_bytes()).hexdigest() == task['validation_result_sha256'][kind]
                result = read_json(path)
                assert result['task_name'] == task['id'] and result['config']['agent']['name'] == kind
                assert result.get('exception_info') is None and result['verifier_result']['rewards']['reward'] == reward
                assert result['task_checksum'] == task['historical_validation_checksum']
        else:
            assert candidate.verify_validation(task) == task['validated_task_sha256']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--attempts', type=int, default=3)
    parser.add_argument('--workers', type=int, default=32)
    parser.add_argument('--models', nargs='+', choices=MODELS, default=MODELS)
    parser.add_argument('--campaign', default='candidates-top5')
    parser.add_argument('--task-manifest', type=Path,
                        help='Explicit frozen task selection; no pilot is added')
    parser.add_argument('--after-summary', type=Path,
                        help='Wait for this screened campaign to complete before launching trials')
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    assert re.fullmatch(r'[\w-]+', args.run_id)
    assert re.fullmatch(r'[\w-]+', args.campaign)
    assert args.attempts > 0 and 1 <= args.workers <= 32
    assert len(set(args.models)) == len(args.models)
    models = args.models
    after_summary = str(args.after_summary.resolve()) if args.after_summary else None
    os.chdir(ROOT)
    selection = read_json(args.task_manifest.resolve())['tasks'] if args.task_manifest else None
    task_names = [t['id'] for t in selection] if selection else TASKS
    assert len(task_names) == len(set(task_names)) and task_names
    base = f'{args.campaign}-efforts-{args.attempts}-{args.run_id}'
    prefix = ROOT / 'jobs' / base
    lock = prefix.with_suffix('.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    plan_path = prefix.with_suffix('.plan.json')
    plan = read_json(plan_path)
    if not plan:
        shortlist = read_json(ROOT / 'research/shortlist.json')['tasks']
        tasks = selection or [next(t for t in shortlist if t['id'] == name) for name in TASKS]
        verify_tasks({'tasks': tasks})
        initial = prefix.with_suffix('.config.json')
        dump(initial, config(base, tasks, [agent(m, e) for e in EFFORTS for m in models],
                             args.attempts, args.workers))
        plan = {'created_at': datetime.now(timezone.utc).isoformat(), 'tasks': tasks,
                'models': models, 'efforts': EFFORTS, 'attempts': args.attempts,
                'workers': args.workers, 'target': len(tasks)*len(models)*len(EFFORTS)*args.attempts,
                'after_summary': after_summary,
                'harbor_version': '0.15.0', 'agent_version': '2.4.6',
                'jobs': [{'name': base, 'config': str(initial.relative_to(ROOT)),
                          'sha256': hashlib.sha256(initial.read_bytes()).hexdigest()}]}
        dump(plan_path, plan)
    assert plan['attempts'] == args.attempts and plan['workers'] == args.workers
    assert plan['models'] == models and plan['efforts'] == EFFORTS
    assert plan.get('after_summary') == after_summary
    assert [t['id'] for t in plan['tasks']] == task_names
    if selection:
        assert plan['tasks'] == selection, 'Task manifest differs from the locked plan'
    verify_tasks(plan)
    control_path = prefix.with_suffix('.control.json')
    if not control_path.exists():
        dump(control_path, {'max_active': args.workers, 'min_total_mb': 30000,
                            'reserve_mb': 4096, 'startup_reserve_mb': 768,
                            'startup_window_sec': 120, 'start_interval_sec': 5,
                            'max_memory_pressure_pct': 1.0, 'pressure_cooldown_sec': 60,
                            'network_pool_cidr': '172.30.0.0/16',
                            'paused': False})
    if args.plan_only:
        print(f'Planned {plan["target"]} trials: {plan_path}')
        return
    load_dotenv(ROOT / '.env')
    os.environ['ANTHROPIC_API_KEY'] = os.environ['TAKE_HOME_TOKEN']
    os.environ['HARBOR_ADMISSION_CONTROL'] = str(control_path)
    os.environ['HARBOR_INTERLEAVE_TASKS'] = '1'
    prefix.with_suffix('.pid').write_text(str(os.getpid()) + '\n')
    task_map = {t['id']: t for t in plan['tasks']}
    cache, children, prepared = {}, {}, set()
    previous = None
    last_start = 0.0
    while True:
        children = {n: p for n, p in children.items() if p.poll() is None}
        records = []
        for job in plan['jobs']:
            for path in sorted((ROOT / 'jobs' / job['name']).glob('*/result.json')):
                evidence = [path, path.parent/'agent/mini-swe-agent.trajectory.json',
                            path.parent/'agent/trajectory.json',
                            path.parent/'benchmark-runtime.json',
                            path.parent/'benchmark-evidence.json',
                            path.parent/'verifier/results.xml', path.parent/'verifier/cargo-tests.log',
                            path.parent/'verifier/score.json']
                stamp = tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None for p in evidence)
                if path not in cache or cache[path][0] != stamp or cache[path][1] is None:
                    try:
                        record = inspect(path, task_map, models)
                    except (AssertionError, KeyError, TypeError, ValueError, EvidenceError, OSError) as error:
                        raw = read_json(path)
                        a = raw.get('config', {}).get('agent', {})
                        record = {'trial': str(path.parent.relative_to(ROOT)), 'task': raw.get('task_name'),
                                  'model': a.get('model_name', '').removeprefix('anthropic/claude-'),
                                  'effort': a.get('kwargs', {}).get('reasoning_effort'),
                                  'status': 'review', 'exception': f'EvidenceMismatch:{type(error).__name__}:{error}',
                                  'reward': None, 'cost_usd': None}
                    cache[path] = stamp, record
                if cache[path][1]:
                    records.append(cache[path][1])
        rows = []
        for task in task_names:
            for model in models:
                for effort in EFFORTS:
                    group = [r for r in records if (r['task'], r['model'], r['effort']) == (task, model, effort)]
                    eligible = [r for r in group if r['status'] in {'scored', 'timeout', 'verifier_timeout'}]
                    assert len(eligible) <= args.attempts, 'Too many counted trials'
                    wins = sum(r['reward'] for r in eligible)
                    passed = sum(r.get('checks_passed') or 0 for r in eligible)
                    total = sum(r.get('checks_total') or 0 for r in eligible)
                    rows.append({'task': task, 'model': model, 'effort': effort, 'target': args.attempts,
                                 'check_unit': task_map[task].get('check_format', 'individual_checks'),
                                 'completed': len(eligible), 'passes': wins,
                                 'pass_rate': wins/len(eligible) if eligible else None,
                                 'wilson_95pct': common.wilson(wins, len(eligible)),
                                 'checks_passed': passed, 'checks_total': total,
                                 'checks_pass_pct': 100*passed/total if total else None,
                                 'timeouts': sum(r['status'] == 'timeout' for r in group),
                                 'verifier_timeouts': sum(r['status'] == 'verifier_timeout' for r in group),
                                 'infrastructure': sum(r['status'] == 'infrastructure' for r in group),
                                 'review': sum(r['status'] == 'review' for r in group),
                                 'cost_usd': sum(r.get('cost_usd') or 0 for r in group)})
        completed = sum(r['completed'] for r in rows)
        review = sum(r['status'] == 'review' for r in records)
        active = {job['name']: pid for job in plan['jobs'] if (pid := common.active_pid(job['name']))}
        control = read_json(control_path)
        if review and not control.get('paused'):
            control.update(paused=True, pause_reason='Unclassified result or evidence mismatch requires review')
            dump(control_path, control)
        # Stop admitting new work if infrastructure failures become sustained.
        # Ordinary model failures and verified failing implementations that
        # exhaust the verifier budget remain in the success-rate denominator.
        now = datetime.now(timezone.utc)
        recent = [r for r in records if r.get('finished_at') and
                  (now-datetime.fromisoformat(r['finished_at'].replace('Z', '+00:00'))).total_seconds() <= 1800]
        recent_infra = sum(r['status'] == 'infrastructure' for r in recent)
        unhealthy = recent_infra >= 5 and recent_infra >= len(recent)/2
        if unhealthy and not control.get('paused'):
            control.update(paused=True, pause_reason='Sustained infrastructure failures require review')
            dump(control_path, control)
        snapshot = memory_snapshot()
        blocked = memory_block_reason(control, snapshot)
        done = completed == plan['target'] and not active
        dependency = read_json(Path(after_summary)) if after_summary else None
        dependency_ready = dependency is None or (dependency.get('status') == 'complete' and
                            dependency.get('health', {}).get('ok_to_expand') is True)
        summary = {'updated_at': datetime.now(timezone.utc).isoformat(), 'job': base,
                   'status': 'complete' if done else 'needs_review' if review else 'paused' if control.get('paused') else 'queued' if not dependency_ready else 'running' if active else 'waiting_for_memory' if blocked else 'starting',
                   'completed': completed, 'target': plan['target'], 'active_jobs': active,
                   'target_concurrency': args.workers, 'settings': rows, 'trials': records,
                   'after_summary': after_summary,
                   'health': {'ok_to_expand': not review and not unhealthy and not control.get('paused'),
                              'recent_results': len(recent), 'recent_infrastructure': recent_infra,
                              'unclassified_results': review},
                   'resources': read_json(control_path.with_suffix('.resources.json')) if active else snapshot}
        dump(prefix.with_suffix('.summary.json'), summary)
        with prefix.with_suffix('.csv').open('w', newline='') as out:
            writer = csv.DictWriter(out, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        progress = (summary['status'], completed, review, tuple(active))
        if progress != previous:
            print(f'{summary["status"]}: {completed}/{plan["target"]}; {review} need review; '
                  f'{snapshot["available_mb"]} MB available', flush=True)
            previous = progress
        if done:
            verify_tasks(plan)
            print('COMPLETE: all task/model/effort groups reached their sample size.', flush=True)
            return
        if dependency_ready and not active and not review and not control.get('paused') and time.monotonic()-last_start > 60:
            unfinished = [job for job in plan['jobs']
                          if not read_json(ROOT/'jobs'/job['name']/'result.json').get('finished_at')]
            if not unfinished and not blocked:
                for task in task_names:
                    missing = [agent(r['model'], r['effort']) for r in rows if r['task'] == task
                               for _ in range(args.attempts-r['completed'])]
                    if not missing:
                        continue
                    name = f'{base}-repair-{len(plan["jobs"]):03d}'
                    cfg_path = ROOT/'jobs'/f'{name}.config.json'
                    dump(cfg_path, config(name, [task_map[task]], missing, 1, args.workers))
                    job = {'name': name, 'config': str(cfg_path.relative_to(ROOT)),
                           'sha256': hashlib.sha256(cfg_path.read_bytes()).hexdigest()}
                    plan['jobs'].append(job); unfinished.append(job)
                dump(plan_path, plan)
            if unfinished:
                job = unfinished[0]
                name = job['name']; directory = ROOT/'jobs'/name
                if (directory/'config.json').exists() and name not in prepared:
                    archive = prepare_resume(directory)
                    if archive:
                        print(f'Preserved interrupted work: {archive}', flush=True)
                    prepared.add(name)
                if not blocked:
                    verify_tasks(plan)
                    cfg_path = ROOT/job['config']
                    assert hashlib.sha256(cfg_path.read_bytes()).hexdigest() == job['sha256']
                    children[name] = common.start_job(cfg_path, name)
                    prepared.discard(name)
                    last_start = time.monotonic()
        time.sleep(10)


if __name__ == '__main__':
    main()
