"""Diagnostic-only mutant: independently committed schedule removal/enqueues."""
def pytest_configure(config):
    from huey.api import Huey, LeasedSqliteHuey
    LeasedSqliteHuey.enqueue_due = Huey.enqueue_due
