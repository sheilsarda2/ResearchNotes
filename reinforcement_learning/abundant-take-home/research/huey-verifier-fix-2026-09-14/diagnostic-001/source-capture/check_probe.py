"""Exercise the verifier's actual SQLite probe without loading any model code."""
import ast
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / 'research/task-revisions/huey-sqlite-leases-v2/tests/test_leases.py'


class Monkeypatch:
    @contextmanager
    def context(self):
        with ExitStack() as stack:
            class Active:
                def setattr(self, obj, name, value):
                    stack.enter_context(patch.object(obj, name, value))
            yield Active()


def main():
    nodes = [node for node in ast.parse(TEST.read_text()).body
             if isinstance(node, (ast.ClassDef, ast.FunctionDef))
             and node.name in ('SqliteInsertProbe', 'sqlite_insert_probe')]
    scope = dict(sqlite3=sqlite3, contextmanager=contextmanager)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(TEST), 'exec'), scope)
    observations = []
    for mode in ('execute', 'cursor', 'executemany', 'insert_select', 'non_atomic'):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'q.db'
            with scope['sqlite_insert_probe'](Monkeypatch(), path) as probe:
                class Storage:
                    def __init__(self):
                        self.conn = sqlite3.connect(path, isolation_level=None)
                        self.conn.execute('create table alternate_queue (data blob)')
                    def enqueue(self, data):
                        self.conn.execute('insert into alternate_queue values (?)', (data,))
                    def flush_queue(self):
                        self.conn.execute('delete from alternate_queue')
                    def queue_size(self):
                        return self.conn.execute('select count(*) from alternate_queue').fetchone()[0]
                    def inflight_count(self):
                        return 0
                storage = Storage()
                probe.watch_queue(storage)
                assert probe.queue_tables == {'alternate_queue'}
                def fail():
                    raise RuntimeError('insertion failure')
                probe.arm(3, fail)
                if mode != 'non_atomic':
                    storage.conn.execute('begin')
                try:
                    if mode == 'executemany':
                        storage.conn.executemany('insert into alternate_queue values (?)', [(i,) for i in range(5)])
                    elif mode == 'insert_select':
                        storage.conn.execute('insert into alternate_queue select column1 from (values (1),(2),(3),(4),(5))')
                    else:
                        target = storage.conn.cursor() if mode == 'cursor' else storage.conn
                        for i in range(5):
                            target.execute('insert into alternate_queue values (?)', (i,))
                except sqlite3.OperationalError as error:
                    assert 'user-defined function' in str(error)
                    storage.conn.rollback()
                else:
                    raise AssertionError('insertion failure not raised')
                assert probe.reached and probe.hits == 3
                rows = storage.queue_size()
                assert rows == (2 if mode == 'non_atomic' else 0)
                observations.append(dict(mode=mode, reached=True, inserted_calls=probe.hits,
                                         rows_after_failure=rows,
                                         rollback_contract_passes=rows == 0))
                storage.conn.close()
    print(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(),
                          verifier_test_sha256=hashlib.sha256(TEST.read_bytes()).hexdigest(),
                          checks=observations, passed=True, model_calls=0,
                          docker_containers_created=0), indent=2))


if __name__ == '__main__':
    main()
