"""Remove explicitly excluded tasks from pending work, preserving past trials."""
from datetime import datetime, timezone
import json
from pathlib import Path


def exclude_pending(job, excluded):
    excluded = set(excluded)
    removed = {config.trial_name for config in job._remaining_trial_configs
               if config.task.path.name in excluded}
    if removed and not hasattr(job, '_benchmark_original_trial_configs'):
        job._benchmark_original_trial_configs = job._trial_configs
    job._remaining_trial_configs = [config for config in job._remaining_trial_configs
                                   if config.trial_name not in removed]
    job._trial_configs = [config for config in job._trial_configs
                         if config.trial_name not in removed]
    return len(removed)


def initialize_original_lock(job, initialize):
    # Harbor validates its original lock on resume. Keep that immutable
    # provenance while benchmark-schedule.json records the smaller live queue.
    selected = job._trial_configs
    job._trial_configs = getattr(job, '_benchmark_original_trial_configs', selected)
    try:
        return initialize(job)
    finally:
        job._trial_configs = selected


def install(control_path):
    from harbor.job import Job

    original = Job._init_remaining_trial_configs

    def selected_remaining(job):
        original(job)
        control = json.loads(Path(control_path).read_text())
        excluded = control.get('excluded_tasks', [])
        if not excluded:
            return
        removed = exclude_pending(job, excluded)
        # This records the revised queue without rewriting any raw trial or
        # original job configuration. Completed excluded trials stay historical.
        report = {'updated_at': datetime.now(timezone.utc).isoformat(),
                  'excluded_tasks': excluded, 'removed_pending_trials': removed,
                  'remaining_trials': len(job._remaining_trial_configs),
                  'total_including_historical_trials': len(job._trial_configs)}
        (job.job_dir / 'benchmark-schedule.json').write_text(json.dumps(report, indent=2) + '\n')

    Job._init_remaining_trial_configs = selected_remaining
    original_lock = Job._init_job_lock

    def preserved_lock(job):
        return initialize_original_lock(job, original_lock)

    Job._init_job_lock = preserved_lock
