"""Current unmodified Huey: SIGKILL during a real task, then restart.
Use fresh temporary DBs. This is a behavior reproduction, not a task grader.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from huey import SqliteHuey, signals
import huey.api as api_module
import huey.consumer as consumer_module
import huey.storage as storage_module

root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix='huey-loss-'))
huey = SqliteHuey('demand-audit', filename=str(root / 'queue.db'))

@huey.task(retries=3)
def work(label, wait_for_release=False):
    (root / (label + '.started')).write_text('started')
    while wait_for_release and not (root / 'release').exists():
        time.sleep(.01)
    (root / (label + '.done')).write_text('done')
    return label

@huey.signal(signals.SIGNAL_INTERRUPTED)
def requeue_interrupted(sig, task, *args, **kwargs):
    (root / 'interrupted.signal').write_text(task.id)
    huey.enqueue(task)

def await_file(name):
    deadline = time.monotonic() + 10
    while not (root / name).exists():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for ' + name)
        time.sleep(.01)

def start():
    return subprocess.Popen([sys.executable, __file__, 'consumer', str(root)])

def stop(p):
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=10)

if len(sys.argv) > 1 and sys.argv[1] == 'consumer':
    huey.create_consumer(workers=1, worker_type='thread', periodic=False,
                         initial_delay=.01, max_delay=.05,
                         graceful_signal='TERM', shutdown_timeout=1).run()
else:
    control = work('control')
    p = start()
    await_file('control.done')
    stop(p)
    assert control.get(blocking=True, timeout=1) == 'control'

    doomed = work('killed', True)
    before = huey.pending_count()
    p = start()
    await_file('killed.started')
    during = huey.pending_count()
    p.kill()  # Uncatchable process termination: all worker threads disappear.
    p.wait(timeout=10)
    (root / 'release').write_text('ready')

    # New API object and SQLite connection after process disappearance.
    reopened = SqliteHuey('demand-audit', filename=str(root / 'queue.db'))
    after = reopened.pending_count()
    restarted = start()
    probe = work('after-restart-probe')
    await_file('after-restart-probe.done')
    stop(restarted)

    report = {
        'source_hashes': {m.__name__: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                          for m in (api_module, consumer_module, storage_module)},
        'control_completed': True,
        'enqueued_before_first_consumer': before,
        'pending_after_task_started_before_kill': during,
        'killed_consumer_exitcode': p.returncode,
        'pending_after_reopen': after,
        'remaining_retries_configured': 3,
        'interruption_handler_was_registered': True,
        'interruption_handler_ran': (root / 'interrupted.signal').exists(),
        'new_work_completed_after_restart': probe.get(blocking=True, timeout=1),
        'killed_task_replayed': (root / 'killed.done').exists(),
        'killed_task_has_result': reopened.storage.has_data_for_key(doomed.id),
        'pending_final': reopened.pending_count(),
        'scheduled_final': reopened.scheduled_count(),
    }
    print(json.dumps(report, indent=2))
    assert report['enqueued_before_first_consumer'] == 1
    assert report['pending_after_task_started_before_kill'] == 0
    assert report['pending_after_reopen'] == 0
    assert not report['killed_task_replayed']
    assert not report['killed_task_has_result']
    assert not report['interruption_handler_ran']
