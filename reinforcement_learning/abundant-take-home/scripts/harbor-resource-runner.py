#!/usr/bin/env python3
"""Run Harbor with resource admission before trial creation and its time budget."""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from harbor.cli.main import app
from harbor.trial.queue import TrialQueue
from harbor.job import Job
from benchmark_recovery import memory_snapshot
from benchmark_networks import install as install_trial_subnets
from benchmark_networks import release as release_trial_subnet
from benchmark_shared_admission import SharedAdmission
from benchmark_deadline import install as install_deadline_guard


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def resources():
    snapshot = memory_snapshot()
    return snapshot["available_mb"], snapshot["memory_pressure_pct"]


def interleave_tasks(configs):
    """Round-robin task/model/effort cells so large repeats cannot starve a cell."""
    groups = defaultdict(deque)
    for config in configs:
        key = (str(config.task.path), config.agent.name, config.agent.model_name,
               json.dumps(config.agent.kwargs, sort_keys=True))
        groups[key].append(config)
    task_order = {key: i for i, key in enumerate(dict.fromkeys(k[0] for k in groups))}
    agent_order = {key: i for i, key in enumerate(dict.fromkeys(k[1:] for k in groups))}
    group_order = sorted(groups, key=lambda key: (agent_order[key[1:]], task_order[key[0]]))
    ordered = []
    while any(groups.values()):
        for key in group_order:
            group = groups[key]
            if group:
                ordered.append(group.popleft())
    return ordered


class Admission:
    def __init__(self, control_path):
        self.path = Path(control_path)
        self.status_path = self.path.with_suffix(".resources.json")
        self.lock = asyncio.Lock()
        self.active = {}
        self.last_start = 0.0
        self.last_write = 0.0
        self.peak = 0
        self.last_pressure = float('-inf')
        shared = read_json(self.path).get('shared_pool')
        self.shared = SharedAdmission(shared, self.path) if shared else None

    def write_status(self, available, pressure, reason, force=False):
        now = time.monotonic()
        if not force and now - self.last_write < 5:
            return
        self.last_write = now
        data = {"updated_at": datetime.now(timezone.utc).isoformat(),
                "active_trials": len(self.active), "peak_active_trials": self.peak,
                "available_mb": available, "memory_pressure_pct": pressure,
                "admission_wait_reason": reason, "trial_names": list(self.active)}
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n")
        temporary.replace(self.status_path)
        with self.status_path.with_suffix(".jsonl").open("a") as history:
            history.write(json.dumps(data) + "\n")

    async def acquire(self, name, task_name=None):
        while True:
            async with self.lock:
                config = read_json(self.path)
                now = time.monotonic()
                snapshot = memory_snapshot()
                available, pressure = snapshot['available_mb'], snapshot['memory_pressure_pct']
                if pressure > config.get("max_memory_pressure_pct", 1.0):
                    self.last_pressure = now
                warmup = config.get("startup_window_sec", 45)
                reserve = config.get("reserve_mb", 1536)
                startup = config.get("startup_reserve_mb", 256)
                reserved = sum(now - started < warmup for started in self.active.values()) * startup
                readiness = read_json(Path(config['image_readiness'])) if config.get('image_readiness') else None
                if config.get("paused", False):
                    reason = "paused"
                elif readiness is not None and (task_name or name.rsplit('__', 1)[0]) not in readiness.get('ready_tasks', []):
                    reason = 'prebuilding task images'
                elif snapshot['total_mb'] < config.get("min_total_mb", 0):
                    reason = "Docker memory allocation"
                elif len(self.active) >= config.get("max_active", 32):
                    reason = "concurrency"
                elif available - reserved < reserve + startup:
                    reason = "memory"
                elif pressure > config.get("max_memory_pressure_pct", 1.0):
                    reason = "memory pressure"
                elif now - self.last_pressure < config.get("pressure_cooldown_sec", 0):
                    reason = "memory pressure cooldown"
                elif now - self.last_start < config.get("start_interval_sec", 3):
                    reason = "staggered startup"
                else:
                    reason = self.shared.try_acquire(name, snapshot, config) if self.shared else None
                    if not reason:
                        self.active[name] = now
                        self.last_start = now
                        self.peak = max(self.peak, len(self.active))
                        self.write_status(available, pressure, None, force=True)
                        return
                self.write_status(available, pressure, reason)
            await asyncio.sleep(2)

    async def release(self, name):
        async with self.lock:
            if self.shared:
                self.shared.release(name)
            self.active.pop(name, None)
            available, pressure = resources()
            self.write_status(available, pressure, None, force=True)


def main():
    install_deadline_guard()
    admission = Admission(os.environ["HARBOR_ADMISSION_CONTROL"])
    install_trial_subnets(admission.path)

    async def admitted_trial(queue, config):
        async with queue._semaphore:
            await admission.acquire(config.trial_name, config.task.path.name)
            try:
                return await queue._execute_trial_with_retries(config)
            finally:
                try:
                    release_trial_subnet(admission.path, config.trial_name)
                finally:
                    await admission.release(config.trial_name)

    # Admission and lifecycle fixes apply only in this process. Installed Harbor
    # and per-trial agent, verifier, CPU, and memory limits remain untouched.
    TrialQueue._run_trial = admitted_trial
    if os.environ.get('HARBOR_INTERLEAVE_TASKS') == '1':
        original = Job._init_remaining_trial_configs

        def interleaved_remaining(job):
            original(job)
            job._remaining_trial_configs = interleave_tasks(job._remaining_trial_configs)

        Job._init_remaining_trial_configs = interleaved_remaining
    app()


if __name__ == "__main__":
    main()
