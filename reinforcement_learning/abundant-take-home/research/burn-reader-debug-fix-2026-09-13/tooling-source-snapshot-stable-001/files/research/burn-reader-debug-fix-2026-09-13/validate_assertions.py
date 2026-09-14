"""Validate the isolated removal of incidental PytorchReader Debug bounds."""
import ast
import difflib
import hashlib
import json
from pathlib import Path
import pickle
import re
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGET = 'tests/hidden/reader/mod.rs'
PATTERN = r'(PytorchReader::(?:new|with_top_level_key)\([^\n]+?\))\.expect_err\('


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest(name):
    saved = json.loads((HERE / name).read_text())
    folder = ROOT / saved['root']
    paths = sorted(folder.rglob('*'))
    assert not any(path.is_symlink() for path in paths)
    current = {
        str(path.relative_to(folder)): {
            'sha256': digest(path), 'bytes': path.stat().st_size,
            'mode': oct(path.stat().st_mode & 0o777),
        }
        for path in paths if path.is_file()
    }
    assert current == saved['files'], f'{name} changed'
    assert len(current) == 64
    return folder, current


source, before_manifest = verify_manifest('source-manifest.json')
revision, after_manifest = verify_manifest('revision-manifest.json')
assert before_manifest.keys() == after_manifest.keys()
assert [p for p in before_manifest if before_manifest[p] != after_manifest[p]] == [TARGET]
before = (source / TARGET).read_text()
after = (revision / TARGET).read_text()
expected, substitutions = re.subn(PATTERN, r'\1.err().expect(', before)
assert substitutions == 7 and after == expected
assert not re.search(PATTERN, after)
assert re.findall(r'fn (test_\w+)\(', before) == re.findall(r'fn (test_\w+)\(', after)
assert before.count('#[test]') == after.count('#[test]')
assert re.findall(r'\bassert\w*!\([^;]*;', before) == re.findall(r'\bassert\w*!\([^;]*;', after)
assert ''.join(difflib.unified_diff(
    before.splitlines(True), after.splitlines(True),
    fromfile='v3/' + TARGET, tofile='v4/' + TARGET)) == (HERE / 'assertion.diff').read_text()

# Debug was not part of the supplied base API or the documented reader interface.
with tarfile.open(source / 'environment/upstream.tar.gz', 'r:gz') as archive:
    upstream = archive.extractfile('crates/burn-store/src/pytorch/reader.rs').read().decode()
assert '\n/// ```\npub struct PytorchReader {' in upstream
assert not re.search(r'impl[^\n]*Debug\s+for\s+PytorchReader', upstream)
assert '// burn_store::pytorch::reader  (pub mod reader)\npub struct PytorchReader' in (source / 'instruction.md').read_text()
assert '+#[derive(Debug)]\n pub struct PytorchReader {' in (source / 'solution/changes.patch').read_text()

# The previous valid sys_info fix and all TAR payload bytes are retained.
start = after.index('fn test_tar_absurd_storage_count_is_an_error()')
end = after.index('\n}\n', start) + len('\n}\n')
function = after[start:end]
entries = dict((name, ast.literal_eval(value)) for name, value in re.findall(
    r'\(\s*"([a-z_]+)",\s*(b"(?:[^"\\]|\\.)*")\.as_slice\(\),?\s*\)', function))
assert list(entries) == ['sys_info', 'storages', 'tensors', 'pickle']
assert pickle.loads(entries['sys_info']) == {
    'protocol_version': 1001, 'little_endian': True,
    'type_sizes': {'short': 2, 'int': 4, 'long': 8}}
assert entries['sys_info'] == (ROOT / 'research/burn-reader-fixture-fix-2026-09-13/sys_info.pickle').read_bytes()
assert pickle.loads(entries['storages']) == 2 ** 40
assert pickle.loads(entries['tensors']) == 0 and pickle.loads(entries['pickle']) == {}

origin = json.loads((HERE / 'origin.json').read_text())
assert origin['source_manifest_sha256'] == digest(HERE / 'source-manifest.json')
assert origin['revision_manifest_sha256'] == digest(HERE / 'revision-manifest.json')
assert origin['diff_sha256'] == digest(HERE / 'assertion.diff')
print(json.dumps({
    'passed': True, 'model_calls': 0, 'docker_runs': 0,
    'source_files': len(before_manifest), 'revision_files': len(after_manifest),
    'changed_files': [TARGET], 'error_assertions_changed': substitutions,
    'all_error_messages_and_following_assertions_unchanged': True,
    'all_other_files_unchanged': True, 'source_unchanged': True,
    'test_names_and_counts_unchanged': True,
    'upstream_reader_has_no_debug_implementation': True,
    'instruction_has_no_reader_debug_requirement': True,
    'prior_tar_sys_info_correction_retained': True,
    'rust_or_model_outcome_validated': False,
}, indent=2))
