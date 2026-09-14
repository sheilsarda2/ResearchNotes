"""Select explicitly planned jobs without duplicating active work."""


def select_jobs(jobs, active_names, finished_names):
    active = set(active_names)
    finished = set(finished_names)
    pending = [job for job in jobs if job['name'] not in active | finished]
    # Missing-count repairs are valid only after all planned work has ended.
    # Explicitly planned repairs can coexist with an active primary job, under
    # the same shared resource admission limits.
    return pending, not active and not pending
