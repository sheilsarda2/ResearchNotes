"""Viewer-only effort grouping; stored benchmark configurations stay untouched."""
from collections import Counter

from harbor.viewer.models import TaskSummary
from harbor.viewer.trial_utils import model_info_from_model_name


def configured_effort(config):
    kwargs = config.agent.kwargs
    return kwargs.get('reasoning_effort') or kwargs.get('effort') or kwargs.get('reasoningEffort')


def with_planned_groups(scanner, job_name, summaries):
    config = scanner.get_job_config(job_name)
    if not config:
        return summaries
    planned = Counter()
    for agent in config.agents:
        model = model_info_from_model_name(agent.model_name)
        effort = agent.kwargs.get('reasoning_effort') or agent.kwargs.get('effort') or agent.kwargs.get('reasoningEffort')
        for task in config.tasks:
            key = (agent.name or agent.import_path or 'unknown',
                   model.provider if model else None, model.name if model else None,
                   task.source, task.name or task.get_task_id().get_name(), effort)
            planned[key] += config.n_attempts
    existing = {(s.agent_name, s.model_provider, s.model_name, s.source, s.task_name, s.reasoning_effort): s
                for s in summaries}
    for key, total in planned.items():
        if key not in existing:
            agent, provider, model, source, task, effort = key
            summary = TaskSummary(task_name=task, source=source, agent_name=agent,
                                  model_provider=provider, model_name=model, reasoning_effort=effort)
            summaries.append(summary); existing[key] = summary
        existing[key].n_planned_trials = total
    return summaries
