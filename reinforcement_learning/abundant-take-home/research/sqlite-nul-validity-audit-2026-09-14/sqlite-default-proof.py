import json
import sqlite3

connection = sqlite3.connect(':memory:')
connection.execute("CREATE TABLE t(v TEXT DEFAULT (CAST(X'610062' AS TEXT)))")
connection.execute('INSERT INTO t DEFAULT VALUES')
value = connection.execute('SELECT v FROM t').fetchone()[0]
raw_error = None
try:
    connection.execute("CREATE TABLE bad(v TEXT DEFAULT 'a\x00b')")
except Exception as error:
    raw_error = {'type': type(error).__name__, 'message': str(error)}
assert value == 'a\x00b'
assert raw_error is not None
print(json.dumps({'sqlite_version': sqlite3.sqlite_version, 'encoded_default_roundtrips': value == 'a\x00b', 'value_repr': repr(value), 'raw_NUL_in_SQL_error': raw_error}, indent=2))
