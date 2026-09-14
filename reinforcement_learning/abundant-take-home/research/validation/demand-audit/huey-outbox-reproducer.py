"""Reproduce the producer commit/enqueue gap using unchanged current Huey.

Run with Python 3.12 + Django 6.0; source checkout path is the only argument.
Only the crash case wraps Django's low-level commit to kill the process after
SQLite COMMIT succeeds and before Django runs its real on_commit callbacks.
No Huey source, decorator, callback, enqueue, serializer or storage is changed.
"""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile


def producer(source, db_path, mode):
    sys.path.insert(0, source)
    from huey import SqliteHuey
    broker = SqliteHuey('demand-audit', filename=db_path, immediate=False)
    from django.conf import settings
    settings.configure(DATABASES={'default': {
        'ENGINE': 'django.db.backends.sqlite3', 'NAME': db_path}},
        INSTALLED_APPS=[], HUEY=broker, USE_TZ=True, SECRET_KEY='local-reproduction')
    import django
    django.setup()
    from django.db import connection, transaction
    from huey.contrib.djhuey import on_commit_task

    @on_commit_task()
    def notify_order(order_id):
        return order_id

    connection.ensure_connection()
    real_commit = connection._commit
    if mode == 'kill_after_commit':
        def commit_then_kill():
            real_commit()
            os.kill(os.getpid(), signal.SIGKILL)
        connection._commit = commit_then_kill

    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO orders (id, state) VALUES (1, %s)',
                               ['accepted'])
            notify_order(1)
            if mode == 'rollback':
                raise ValueError('intentional rollback')
    except ValueError:
        if mode != 'rollback':
            raise
    connection.close()
    broker.storage.close()


def main(source):
    source = str(Path(source).resolve())
    rows = []
    with tempfile.TemporaryDirectory(prefix='huey-outbox-demand-') as directory:
        for mode in ('normal_commit', 'rollback', 'kill_after_commit'):
            db_path = str(Path(directory) / (mode + '.db'))
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE orders (id INTEGER PRIMARY KEY, state TEXT)')
            child = subprocess.run([sys.executable, __file__, '--producer', source,
                                    db_path, mode], capture_output=True, text=True)
            with sqlite3.connect(db_path) as db:
                row = {'mode': mode, 'producer_returncode': child.returncode,
                       'committed_orders': db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],
                       'queued_tasks': db.execute('SELECT COUNT(*) FROM task').fetchone()[0]}
            if child.stderr:
                row['stderr'] = child.stderr
            rows.append(row)
    expected = [(0, 1, 1), (0, 0, 0), (-signal.SIGKILL, 1, 0)]
    for row, counts in zip(rows, expected):
        assert tuple(row[key] for key in ('producer_returncode', 'committed_orders', 'queued_tasks')) == counts, row
    import django
    print(json.dumps({'python_version': sys.version.split()[0], 'django_version': django.get_version(),
                      'upstream_commit': 'dbd1aef45adf57d2169e80bdc9f18ea1cd704759',
                      'source': source, 'observations': rows}, indent=2))


if __name__ == '__main__':
    if sys.argv[1] == '--producer':
        producer(*sys.argv[2:])
    else:
        main(sys.argv[1])
