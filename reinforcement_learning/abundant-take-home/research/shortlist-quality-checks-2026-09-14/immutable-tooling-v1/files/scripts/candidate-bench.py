#!/usr/bin/env python3
"""Plan, run, and summarize reproducible Harbor candidate comparisons. Python 3.12+."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HARBOR_VERSION = '0.15.0'
MINI_VERSION = '2.4.6'


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def digest_task(task):
    # Bind the whole task directory, including construction provenance, because
    # Harbor's own checksum also covers that directory. Generated files added
    # after validation must not silently change the recorded task identity.
    for name in ('instruction.md', 'task.toml', 'provenance.json'):
        if not (task/name).is_file():
            raise ValueError('Missing required file: ' + str(task/name))
    hashes = {}
    for item in sorted(task.rglob('*')):
        if item.is_symlink():
            raise ValueError('Task symlinks are not supported: ' + str(item))
        if item.is_file():
            hashes[item.relative_to(task).as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def verify_validation(task):
    """Require saved, immutable result evidence for this exact candidate revision."""
    current = digest_task(ROOT/task['path'])
    if task.get('validated_task_sha256') != current:
        raise ValueError(task['id'] + ' changed since oracle/nop validation; rerun both')
    for kind, expected in (('oracle', 1), ('nop', 0)):
        path = ROOT/task['validation_evidence'][kind]
        if hashlib.sha256(path.read_bytes()).hexdigest() != task['validation_result_sha256'][kind]:
            raise ValueError(task['id'] + ': changed ' + kind + ' result evidence')
        result = json.loads(path.read_text())
        if (result.get('exception_info') is not None or
            (result.get('verifier_result') or {}).get('rewards', {}).get('reward') != expected or
            result.get('task_name') != task['id'] or
            result['config']['agent']['name'] != kind or
            result.get('task_checksum') != task['validated_harbor_task_checksum']):
            raise ValueError(task['id'] + ': invalid ' + kind + ' evidence')
    return current


def read_locked_config(lock_path, lock, name):
    path = lock_path.parent/name
    if hashlib.sha256(path.read_bytes()).hexdigest() != lock['config_sha256'][name]:
        raise ValueError(name + ' changed after planning; use the original configuration')
    return json.loads(path.read_text())


def harbor_command():
    runner = str(ROOT/'scripts/harbor-benchmark-runner.py')
    if shutil.which('uv'):
        return ['uv', 'tool', 'run', '--from', 'harbor==' + HARBOR_VERSION, 'python', runner]
    harbor = shutil.which('harbor')
    if harbor:
        version = subprocess.check_output([harbor, '--version'], text=True)
        if HARBOR_VERSION not in version:
            raise ValueError('Use Harbor ' + HARBOR_VERSION + ' or install uv')
        python = Path(harbor).resolve().with_name('python')
        if not python.is_file():
            raise ValueError('Harbor Python was not found next to its executable; install uv')
        return [str(python), runner]
    raise ValueError('Install uv or Harbor ' + HARBOR_VERSION)


def plan(args):
    shortlist = json.loads((ROOT/'research/shortlist.json').read_text())['tasks']
    selected = [t for t in shortlist if not args.tasks or t['id'] in args.tasks]
    if not selected or (args.tasks and set(args.tasks) != {t['id'] for t in selected}):
        raise ValueError('Unknown or empty task selection')
    for task in selected:
        if not (task['oracle_verified'] and task['nop_verified']):
            raise ValueError(task['id'] + ' has not passed final Harbor oracle/nop validation')
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or datetime.now(timezone.utc).strftime('candidates-%Y%m%dT%H%M%SZ')
    if re.fullmatch(r'[a-zA-Z0-9_-]+', prefix) is None:
        raise ValueError('Use letters, numbers, underscores, and hyphens in prefix')
    task_hashes = {t['id']: verify_validation(t) for t in selected}
    configs = []
    config_hashes = {}
    for model in args.models:
        for effort in args.efforts:
            slug = re.sub(r'[^a-zA-Z0-9_-]+', '-', model)
            name = prefix + '-' + slug + '-' + effort
            config = {'job_name':name, 'jobs_dir':'jobs', 'n_attempts':args.attempts,
                      'n_concurrent_trials':args.concurrency,
                      'agents':[{'name':'mini-swe-agent', 'model_name':model,
                                 'kwargs':{'reasoning_effort':effort, 'version':MINI_VERSION}}],
                      'tasks':[{'path':t['path']} for t in selected]}
            path = out/(name + '.json')
            if path.exists() or (ROOT/'jobs'/name).exists():
                raise ValueError('Refusing to overwrite existing config/job: ' + name)
            dump(path, config)
            configs.append(path.name)
            config_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    lock = {'created_at':datetime.now(timezone.utc).isoformat(), 'harbor_version':HARBOR_VERSION,
            'mini_swe_agent_version':MINI_VERSION, 'task_sha256':task_hashes, 'configs':configs,
            'config_sha256':config_hashes,
            'harbor_task_checksum':{t['id']:t['validated_harbor_task_checksum'] for t in selected},
            'planned_trials':len(selected)*len(args.models)*len(args.efforts)*args.attempts,
            'network':'Agent has public network for gateway/harness; verifier runs in a fresh no-network container.',
            'headroom':'Unmeasured until these trials run; analyze exceptions separately.'}
    lock_path = out/(prefix + '-lock.json')
    if lock_path.exists():
        raise ValueError('Lock file exists; use a new prefix/output directory')
    dump(lock_path, lock)
    print(json.dumps({'lock':str(lock_path), 'configs':configs, 'planned_trials':lock['planned_trials']}, indent=2))


def run(args):
    lock_path = Path(args.lock).resolve()
    lock = json.loads(lock_path.read_text())
    for name, expected in lock['task_sha256'].items():
        if digest_task(ROOT/'candidates'/name) != expected:
            raise ValueError(name + ' changed after planning; generate a new comparison plan')
    requested = set(args.configs or lock['configs'])
    if not requested.issubset(lock['configs']):
        raise ValueError('Config is not in this locked plan')
    command = harbor_command()
    for name in lock['configs']:
        if name not in requested:
            continue
        path = lock_path.parent/name
        if hashlib.sha256(path.read_bytes()).hexdigest() != lock['config_sha256'][name]:
            raise ValueError(name + ' changed after planning; generate a new plan')
        config = json.loads(path.read_text())
        if (ROOT/config['jobs_dir']/config['job_name']).exists():
            raise ValueError('Job already exists: ' + config['job_name'] + '; use harbor jobs resume explicitly')
        # Secrets are inherited from the caller, never read from files or logged.
        subprocess.run(command + ['run', '-c', str(path), '-y'], cwd=ROOT, check=True)


def seconds(timing):
    if not timing or not timing.get('started_at') or not timing.get('finished_at'):
        return None
    return (datetime.fromisoformat(timing['finished_at']) - datetime.fromisoformat(timing['started_at'])).total_seconds()


def wilson(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p = successes/total
    denom = 1 + z*z/total
    center = (p + z*z/(2*total))/denom
    delta = z*math.sqrt(p*(1-p)/total + z*z/(4*total*total))/denom
    return [max(0,center-delta),min(1,center+delta)]


def summarize(args):
    lock_path = Path(args.lock).resolve()
    lock = json.loads(lock_path.read_text())
    records = []
    groups = defaultdict(list)
    expected_groups = {}
    for name in lock['configs']:
        config = read_locked_config(lock_path, lock, name)
        job = ROOT/config['jobs_dir']/config['job_name']
        agent = config['agents'][0]
        for task in config['tasks']:
            key = (Path(task['path']).name, agent['model_name'], agent['kwargs']['reasoning_effort'])
            expected_groups[key] = expected_groups.get(key, 0) + config['n_attempts']
        for path in sorted(job.glob('*/result.json')):
            d = json.loads(path.read_text())
            actual_agent = d['config']['agent']
            if (actual_agent.get('model_name') != agent['model_name'] or
                actual_agent.get('kwargs', {}).get('reasoning_effort') != agent['kwargs']['reasoning_effort'] or
                d.get('task_checksum') != lock['harbor_task_checksum'].get(d['task_name'])):
                raise ValueError('Result model, effort, or task revision differs from plan: ' + str(path))
            rewards = (d.get('verifier_result') or {}).get('rewards') or {}
            reward = rewards.get('reward')
            error = (d.get('exception_info') or {}).get('exception_type')
            record = {'task':d['task_name'], 'model':agent['model_name'],
                      'effort':agent['kwargs']['reasoning_effort'], 'trial':d['trial_name'],
                      'result_path':str(path.relative_to(ROOT)), 'reward':reward,
                      'exception':error, 'agent_seconds':seconds(d.get('agent_execution')),
                      'agent_budget_exceeded':error == 'AgentTimeoutError',
                      'scored_without_exception':error is None and reward in (0,1),
                      'assistant_steps':None, 'tool_calls':None, 'cost_usd':None,
                      'observed_model_names':[], 'observed_reasoning_efforts':[],
                      'trajectory_path':None, 'harbor_task_checksum':d.get('task_checksum')}
            trajectory = path.parent/'agent/trajectory.json'
            if trajectory.is_file():
                trace = json.loads(trajectory.read_text())
                steps = [s for s in trace.get('steps',[]) if s.get('source') == 'agent']
                record.update(assistant_steps=len(steps),
                              tool_calls=sum(len(s.get('tool_calls') or []) for s in steps),
                              observed_model_names=sorted({s['model_name'] for s in steps if s.get('model_name')}),
                              observed_reasoning_efforts=sorted({s['reasoning_effort'] for s in steps if s.get('reasoning_effort')}),
                              cost_usd=(trace.get('final_metrics') or {}).get('total_cost_usd'),
                              trajectory_path=str(trajectory.relative_to(ROOT)))
            records.append(record)
            groups[(record['task'],record['model'],record['effort'])].append(record)
    summary = []
    for key, expected in sorted(expected_groups.items()):
        rows = groups[key]
        scored = [r for r in rows if r['scored_without_exception']]
        wins = [r for r in scored if r['reward'] == 1]
        budgeted = [r for r in rows if r['scored_without_exception'] or r['agent_budget_exceeded']]
        budgeted_wins = [r for r in budgeted if r['reward'] == 1]
        budgeted_total = len(budgeted)
        success_steps = [r['assistant_steps'] for r in budgeted_wins if r['assistant_steps'] is not None]
        summary.append({'task':key[0], 'model':key[1], 'effort':key[2], 'planned_trials':expected,
                        'result_files':len(rows), 'scored_without_exception':len(scored),
                        'exceptions_or_unscored':len(rows)-len(scored), 'conditional_scored_successes':len(wins),
                        'agent_budget_timeouts':sum(r['agent_budget_exceeded'] for r in rows),
                        'conditional_scored_success_rate':len(wins)/len(scored) if scored else None,
                        'conditional_scored_wilson_95pct':wilson(len(wins),len(scored)),
                        'budgeted_attempts':budgeted_total,
                        'budgeted_successes':len(budgeted_wins),
                        'budgeted_success_rate':len(budgeted_wins)/budgeted_total if budgeted_total else None,
                        'budgeted_wilson_95pct':wilson(len(budgeted_wins),budgeted_total),
                        'observed_attempt_success_rate':sum(r['reward']==1 for r in rows)/len(rows) if rows else None,
                        'other_exceptions_or_unscored':len(rows)-budgeted_total,
                        'successful_assistant_steps':success_steps,
                        'successful_step_median':statistics.median(success_steps) if success_steps else None,
                        'successful_trials_over_75_steps':sum(n>75 for n in success_steps),
                        'all_completed_results_present':len(rows)==expected})
    output = {'generated_at':datetime.now(timezone.utc).isoformat(), 'lock':str(lock_path),
              'step_definition':'One ATIF step with source=agent; tool call count also reported. This is not METR human task duration.',
              'interpretation':'Budgeted success includes agent timeouts in its denominator and counts their final artifact as a success only if verifier reward is 1; conditional scored success excludes all exceptions. Observed-attempt success includes every existing result, including infrastructure failures, as its denominator and is not a pure capability measure. Other exceptions/unscored trials require explicit review before rankings; missing result files remain separate. Inspect every excluded trial. Small-n intervals are wide. Review successful/failing trajectories and solution artifacts before choosing the final three.',
              'summary':summary, 'trials':records}
    dump(Path(args.output), output)
    print('Wrote ' + args.output + ' (' + str(len(records)) + ' trial records)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan', help='Write configs and a task checksum lock; no model calls')
    p.add_argument('--models', nargs='+', default=['anthropic/claude-sonnet-5','anthropic/claude-fable-5-1'])
    p.add_argument('--efforts', nargs='+', choices=['low','medium','high','xhigh','max'], default=['high'])
    p.add_argument('--tasks', nargs='+')
    p.add_argument('--attempts', type=int, default=3)
    p.add_argument('--concurrency', type=int, default=2)
    p.add_argument('--prefix')
    p.add_argument('--output', default=str(ROOT/'research/benchmark-configs'))
    p.set_defaults(func=plan)
    p = sub.add_parser('run', help='Execute the locked configurations; makes paid model calls')
    p.add_argument('--lock', required=True)
    p.add_argument('--configs', nargs='+', help='Optional config basenames from the lock')
    p.set_defaults(func=run)
    p = sub.add_parser('summarize', help='Read untouched Harbor results/ATIF; never regrade')
    p.add_argument('--lock', required=True)
    p.add_argument('--output', default=str(ROOT/'research/headroom-results.json'))
    p.set_defaults(func=summarize)
    args = parser.parse_args()
    if getattr(args,'attempts',1) < 1 or getattr(args,'concurrency',1) < 1:
        parser.error('attempts and concurrency must be positive')
    try:
        args.func(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
