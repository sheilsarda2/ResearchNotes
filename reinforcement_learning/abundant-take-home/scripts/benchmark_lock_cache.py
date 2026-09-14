"""Hash each immutable task once while constructing a large Harbor job lock."""
from contextlib import contextmanager
from pathlib import Path

from harbor.publisher.packager import Packager


@contextmanager
def cached_task_hashes():
    original = Packager.compute_content_hash
    cache = {}

    def compute(task_dir):
        key = Path(task_dir).resolve()
        if key not in cache:
            cache[key] = original(key)
        return cache[key]

    Packager.compute_content_hash = staticmethod(compute)
    try:
        yield
    finally:
        Packager.compute_content_hash = staticmethod(original)


def install():
    from harbor.job import Job
    original = Job._init_job_lock

    def initialize(job):
        # This scope ends before trials start. Normal per-trial checksums and
        # future lock construction still hash the files again.
        with cached_task_hashes():
            return original(job)

    Job._init_job_lock = initialize
