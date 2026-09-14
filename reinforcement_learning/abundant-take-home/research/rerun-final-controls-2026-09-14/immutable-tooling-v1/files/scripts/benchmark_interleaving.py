"""Persistent dispatch rounds across a family of Harbor jobs.

The shared admission lock owns both resource reservations and dispatch receipts.
Waiting coroutines are cheap; only resource admission starts a trial or its clock.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
import json
from pathlib import Path
import time

TRIALS = {}
QUEUES = defaultdict(list)
_cache = {}


def cell_key(task, model, effort):
    return json.dumps([str(task), model, effort], separators=(',', ':'))


def config_cell(config):
    return cell_key(Path(config.task.path).name, config.agent.model_name,
                    config.agent.kwargs.get('reasoning_effort'))


def seed(jobs_root, user_plan):
    """Snapshot primary dispatches; explicit replacement jobs remain retries."""
    root = Path(jobs_root)
    campaigns = [user_plan['active_full_campaign'], *user_plan.get('active_revision_campaigns', [])]
    cells, receipts = {}, {}
    for campaign in campaigns:
        plan = json.loads((root / f'{campaign}.plan.json').read_text())
        config_path = root / campaign / 'config.json'
        if not config_path.exists():
            config_path = root / f'{campaign}.config.json'
        config = json.loads(config_path.read_text())
        excluded = set(plan.get('excluded_tasks', [])) | set(user_plan.get('excluded_tasks', []))
        for agent in config['agents']:
            for task in config['tasks']:
                name = Path(task['path']).name
                if name in excluded:
                    continue
                key = cell_key(name, agent['model_name'], agent['kwargs'].get('reasoning_effort'))
                cells[key] = dict(task=name, model=agent['model_name'],
                                  effort=agent['kwargs'].get('reasoning_effort'),
                                  job=campaign, target=plan['attempts'])
        paths = list((root / campaign).glob('*/config.json'))
        for path in sorted(paths, key=lambda p: p.stat().st_mtime):
            value = json.loads(path.read_text())
            if 'task' not in value or 'agent' not in value:
                continue
            agent = value['agent']
            key = cell_key(Path(value['task']['path']).name, agent['model_name'],
                           agent['kwargs'].get('reasoning_effort'))
            if key not in cells or cells[key]['job'] != campaign:
                continue
            name = value.get('trial_name') or path.parent.name
            receipts.setdefault(name, dict(cell=key, job=campaign,
                                           started_at=path.stat().st_mtime, source='existing_start'))
    counts = dict.fromkeys(cells, 0)
    for receipt in sorted(receipts.values(), key=lambda r: r['started_at']):
        counts[receipt['cell']] += 1
        receipt['iteration'] = counts[receipt['cell']]
    return dict(version=1, campaigns=campaigns, cells=cells, counts=counts,
                receipts=receipts, installed_at=time.time(),
                excluded_tasks=user_plan.get('excluded_tasks', []))


def refresh_selection(shared):
    """Follow explicitly promoted task revisions when their next runner starts."""
    plan_path = shared.path.parent / 'benchmark-user-plan.json'
    with shared.locked() as (_, state):
        # A promotion writes policy and user plan under this same lock. Reading
        # before acquiring it can replay an old selection after promotion.
        if not plan_path.exists():
            return
        user_plan = json.loads(plan_path.read_text())
        campaigns = [user_plan['active_full_campaign'], *user_plan.get('active_revision_campaigns', [])]
        policy = state.get('interleaving')
        if not policy or (policy['campaigns'] == campaigns and
                          policy.get('excluded_tasks', []) == user_plan.get('excluded_tasks', [])):
            return
        fresh = seed(shared.path.parent, user_plan)
        retired = dict(policy.get('retired_receipts', {}))
        for name, receipt in policy['receipts'].items():
            cell = fresh['cells'].get(receipt['cell'])
            if cell and cell['job'] == receipt['job']:
                fresh['receipts'][name] = receipt
            else:
                retired[name] = {**receipt, 'reason': 'task version superseded by user plan'}
        fresh['counts'] = dict.fromkeys(fresh['cells'], 0)
        for receipt in fresh['receipts'].values():
            fresh['counts'][receipt['cell']] += 1
        fresh.update(retired_receipts=retired, installed_at=policy['installed_at'],
                     selection_updated_at=time.time())
        # Refresh dispatch fields without dropping revision archives or other
        # policy metadata owned by the promotion tooling.
        state['interleaving'] = {**policy, **fresh}
    _cache.clear()


def held_cell_keys(policy):
    """Read explicit temporary holds without changing dispatch history.

    Retired keys and holds for a superseded job remain audit metadata only.
    Malformed current holds fail closed instead of silently releasing work.
    """
    holds = policy.get('held_cells', {})
    if not isinstance(holds, dict):
        raise ValueError('held_cells must be a mapping')
    held = set()
    for key, hold in holds.items():
        if key not in policy['cells']:
            continue
        if (not isinstance(hold, dict) or not isinstance(hold.get('job'), str)
                or not isinstance(hold.get('reason'), str) or not hold['reason'].strip()):
            raise ValueError('current cell hold requires job and reason')
        if hold['job'] == policy['cells'][key]['job']:
            held.add(key)
    return held


def wait_reason(name, state):
    if state.get('interleaving_unavailable'):
        return 'interleaving: waiting for shared state'
    policy = state.get('interleaving')
    if not policy:
        return None
    trial = TRIALS.get(name)
    if not trial:
        return None  # Unmanaged jobs, including explicit repair jobs.
    key = trial['cell']
    if key not in policy['cells'] or policy['cells'][key]['job'] != trial['job']:
        return 'interleaving: task version retired from queued sweep'
    try:
        held = held_cell_keys(policy)
    except ValueError:
        return 'interleaving: waiting for valid hold metadata'
    if key in held:
        return 'interleaving: task temporarily held'
    receipts = policy['receipts']
    if name in receipts:
        return 'interleaving: dispatch already recorded'
    head = next((n for n in QUEUES[key] if n not in receipts), None)
    if head != name:
        return 'interleaving: waiting for earlier attempt in cell'
    counts = policy['counts']
    unfinished = [counts[k] for k, cell in policy['cells'].items()
                  if k not in held and counts[k] < cell['target']]
    if counts[key] >= policy['cells'][key]['target']:
        return 'interleaving: primary dispatch target reached'
    from benchmark_coverage_priority import priority_decision
    priority, coverage_wait = priority_decision(state, key, held)
    if coverage_wait:
        return coverage_wait
    if priority:
        return None
    if unfinished and counts[key] > min(unfinished):
        return 'interleaving: waiting for remaining cells in round'
    return None


def managed(name, state):
    return bool(state.get('interleaving') and name in TRIALS)


def record(name, state, now):
    if not managed(name, state):
        return
    policy, trial = state['interleaving'], TRIALS[name]
    key = trial['cell']
    policy['counts'][key] += 1
    policy['receipts'][name] = dict(**trial, iteration=policy['counts'][key],
                                   started_at=now, source='atomic_admission')
    _cache.clear()


def cached_state(shared):
    key, now = str(shared.state_path), time.monotonic()
    previous = _cache.get(key)
    if previous is None or now - previous[0] > 0.5:
        try:
            value = json.loads(shared.state_path.read_text())
        except (OSError, ValueError):
            # Shared workspaces can briefly hide an atomically replaced file.
            # A monitoring read must never fail Harbor's entire TaskGroup.
            value = previous[1] if previous else {'interleaving_unavailable': True}
        previous = (now, value)
        _cache[key] = previous
    return previous[1]


def register(job, shared):
    """Expose every remaining cell, retaining active coroutines and job locks."""
    refresh_selection(shared)
    state = json.loads(shared.state_path.read_text())
    policy = state.get('interleaving')
    if not policy or job.config.job_name not in policy['campaigns']:
        return 0
    if getattr(job, '_benchmark_interleaving_registered', False):
        return len(job._remaining_trial_configs)
    # Recover reservations whose process died before creating a trial directory,
    # or whose partial output was archived by the existing recovery procedure.
    # Preserve the receipt as history, but return its unfinished iteration slot.
    pending_names = {c.trial_name for c in job._remaining_trial_configs}
    with shared.locked() as (_, current):
        policy = current['interleaving']
        for name, receipt in list(policy['receipts'].items()):
            if (receipt['job'] == job.config.job_name and name not in pending_names
                    and not (shared.path.parent / receipt['job'] / name / 'config.json').exists()):
                policy.setdefault('retired_receipts', {})[name] = {
                    **receipt, 'retired_at': time.time(), 'reason': 'unfinished dispatch recovered'}
                policy['counts'][receipt['cell']] -= 1
                del policy['receipts'][name]
    job._benchmark_interleaving_registered = True
    groups = defaultdict(list)
    for config in job._remaining_trial_configs:
        key = config_cell(config)
        if key in policy['cells'] and policy['cells'][key]['job'] == job.config.job_name:
            groups[key].append(config)
            TRIALS[config.trial_name] = dict(cell=key, job=job.config.job_name)
            QUEUES[key].append(config.trial_name)
    ranks = {key: rank for rank, key in enumerate(policy['cells'])}
    ordered = [(policy['counts'][key] + offset, ranks[key], config)
               for key, configs in groups.items() for offset, config in enumerate(configs)]
    selected = [config for _, _, config in sorted(ordered, key=lambda item: item[:2])]
    unmanaged = [c for c in job._remaining_trial_configs if c.trial_name not in TRIALS]
    job._remaining_trial_configs = selected + unmanaged
    # This semaphore limits pending coroutines, NOT live agents. A small pending
    # window can contain only ahead-of-round cells and deadlock admission.
    # Keep the existing semaphore object: active waiters and holders own it.
    for _ in job._remaining_trial_configs:
        job._trial_queue._semaphore.release()
    return len(selected)


def install(admission_class, shared=None):
    """Install on startup, or on live workers at a Python main-thread safepoint."""
    from harbor.job import Job
    if not getattr(admission_class, '_benchmark_persistent_interleaving', False):
        original_acquire = admission_class.acquire

        async def priority_acquire(self, name, task_name=None):
            # Cache the inexpensive priority check before the existing resource
            # probe. Only one waiter per eligible cell probes host resources.
            while self.shared and name in TRIALS:
                if wait_reason(name, cached_state(self.shared)) is None:
                    break
                await asyncio.sleep(0.5)
            return await original_acquire(self, name, task_name)

        admission_class.acquire = priority_acquire
        admission_class._benchmark_persistent_interleaving = True
    if shared is not None and not getattr(Job, '_benchmark_interleaving_run_installed', False):
        original_run = Job._run_trials_with_queue

        async def run_with_rounds(job, *args, **kwargs):
            register(job, shared)
            return await original_run(job, *args, **kwargs)

        Job._run_trials_with_queue = run_with_rounds
        Job._benchmark_interleaving_run_installed = True
