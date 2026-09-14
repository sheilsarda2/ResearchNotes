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
        config = json.loads((root / campaign / 'config.json').read_text())
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
                receipts=receipts, installed_at=time.time())


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
    receipts = policy['receipts']
    if name in receipts:
        return 'interleaving: dispatch already recorded'
    head = next((n for n in QUEUES[key] if n not in receipts), None)
    if head != name:
        return 'interleaving: waiting for earlier attempt in cell'
    counts = policy['counts']
    unfinished = [counts[k] for k, cell in policy['cells'].items()
                  if counts[k] < cell['target']]
    if counts[key] >= policy['cells'][key]['target']:
        return 'interleaving: primary dispatch target reached'
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
