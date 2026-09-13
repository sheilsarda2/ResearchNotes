"""Validate the isolated TAR fixture revision with Python's standard library."""
import ast
import difflib
import hashlib
import io
import json
from pathlib import Path
import pickle
import pickletools
import re
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGET = 'tests/hidden/reader/mod.rs'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest(name):
    saved = json.loads((HERE / name).read_text())
    folder = ROOT / saved['root']
    current = {
        str(p.relative_to(folder)): {
            'sha256': digest(p), 'bytes': p.stat().st_size,
            'mode': oct(p.stat().st_mode & 0o777),
        }
        for p in sorted(folder.rglob('*')) if p.is_file()
    }
    assert current == saved['files'], f'{name} changed'
    return folder, current


source, before_manifest = verify_manifest('source-manifest.json')
revision, after_manifest = verify_manifest('revision-manifest.json')
assert before_manifest.keys() == after_manifest.keys()
assert [p for p in before_manifest if before_manifest[p] != after_manifest[p]] == [TARGET]
before = (source / TARGET).read_text()
after = (revision / TARGET).read_text()
start = after.index('fn test_tar_absurd_storage_count_is_an_error()')
end = after.index('\n}\n', start) + len('\n}\n')
function = after[start:end]
entries = dict((name, ast.literal_eval(value)) for name, value in re.findall(
    r'\(\s*"([a-z_]+)",\s*(b"(?:[^"\\]|\\.)*")\.as_slice\(\),?\s*\)', function))
assert list(entries) == ['sys_info', 'storages', 'tensors', 'pickle']
expected = {'protocol_version': 1001, 'little_endian': True,
            'type_sizes': {'short': 2, 'int': 4, 'long': 8}}
assert pickle.loads(entries['sys_info']) == expected
assert entries['sys_info'] == (HERE / 'sys_info.pickle').read_bytes()
operations = list(pickletools.genops(entries['sys_info']))
assert operations[0][0].name == 'PROTO' and operations[0][1] == 2
assert max(op.proto for op, _, _ in operations) <= 2
assert operations[-1][0].name == 'STOP'
assert pickle.loads(entries['storages']) == 2 ** 40
assert pickle.loads(entries['tensors']) == 0
assert pickle.loads(entries['pickle']) == {}

# Only a sys_info tuple was inserted; the original function, assertions, count,
# and every other test byte are retained in their original order.
before_lines, after_lines = before.splitlines(keepends=True), after.splitlines(keepends=True)
changes = [(tag, a, b, c, d) for tag, a, b, c, d in
           difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False).get_opcodes()
           if tag != 'equal']
assert len(changes) == 1 and changes[0][0] == 'insert'
_, _, _, first, last = changes[0]
inserted = ''.join(after_lines[first:last])
assert '"sys_info"' in inserted and 'assert' not in inserted
assert after.replace(inserted, '', 1) == before
assert re.findall(r'fn (test_\w+)\(', before) == re.findall(r'fn (test_\w+)\(', after)

# Verify that the byte strings also form the intended four-member TAR layout.
archive = io.BytesIO()
with tarfile.open(fileobj=archive, mode='w') as tar:
    for name, payload in entries.items():
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
archive.seek(0)
with tarfile.open(fileobj=archive) as tar:
    assert tar.getnames() == list(entries)
    assert {name: tar.extractfile(name).read() for name in tar.getnames()} == entries

origin = json.loads((HERE / 'origin.json').read_text())
assert origin['source_manifest_sha256'] == digest(HERE / 'source-manifest.json')
assert origin['revision_manifest_sha256'] == digest(HERE / 'revision-manifest.json')
assert origin['diff_sha256'] == digest(HERE / 'fixture.diff')
print(json.dumps({
    'passed': True, 'model_calls': 0, 'docker_runs': 0,
    'source_files': len(before_manifest), 'revision_files': len(after_manifest),
    'changed_files': [TARGET], 'pickle_protocol': 2, 'sys_info': expected,
    'sys_info_bytes': len(entries['sys_info']), 'storage_count': pickle.loads(entries['storages']),
    'tar_entries': list(entries), 'all_original_assertions_unchanged': True,
    'all_other_files_unchanged': True, 'source_unchanged': True,
    'rust_or_model_outcome_validated': False,
}, indent=2))
