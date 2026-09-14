"""Real Git regressions for the no-model patch path preflight."""
import contextlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('focused_v3', BASE / 'harness/focused_validation_v3.py')
focused = importlib.util.module_from_spec(spec)
spec.loader.exec_module(focused)


def diff(path):
    return (f'diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n'
            '@@ -1 +1 @@\n-old\n+new\n')


@contextlib.contextmanager
def working_directory(path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class PatchInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='patch-inspection-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['git', 'init', '--quiet', str(self.root)], check=True)
        self.nested = self.root / 'nested/project'
        self.nested.mkdir(parents=True)
        self.patch = self.root / 'change.patch'

    def inspect(self, body):
        self.patch.write_text(body)
        with working_directory(self.nested):
            return focused.patch_paths(self.patch)

    def test_reproduces_silent_subdirectory_filter_then_reads_exact_path(self):
        name = 'commons/zenoh-codec/src/network/timestamp_stack.rs'
        self.patch.write_text(diff(name))
        legacy = subprocess.run(['git', 'apply', '--numstat', '-z', str(self.patch)],
                                cwd=self.nested, capture_output=True, check=True)
        self.assertEqual(legacy.stdout, b'')
        self.assertEqual(self.inspect(diff(name)), [name])

    def test_all_four_collected_source_roots_are_accepted(self):
        paths = [root + '/sample.rs' for root in focused.SRC_DIRS]
        self.assertEqual(self.inspect(''.join(diff(path) for path in paths)), paths)

    def test_mixed_source_and_hidden_test_patch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'four collected src'):
            self.inspect(diff('zenoh/src/sample.rs') + diff('tests/hidden.rs'))

    def test_source_prefix_lookalike_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'four collected src'):
            self.inspect(diff('zenoh/src-other/sample.rs'))

    def test_parent_traversal_is_rejected(self):
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            self.inspect(diff('zenoh/src/../../outside.rs'))

    def test_absolute_path_is_rejected(self):
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            self.inspect(diff('/tmp/outside.rs'))

    def test_inherited_git_context_cannot_hide_or_redirect_paths(self):
        with patch.dict(os.environ, {'GIT_DIR': str(self.root / 'absent.git'),
                                     'GIT_WORK_TREE': str(self.nested),
                                     'GIT_PREFIX': 'nested/project/'}):
            self.assertEqual(self.inspect(diff('zenoh/src/sample.rs')), ['zenoh/src/sample.rs'])

    def test_inspection_does_not_apply_patch(self):
        source = self.root / 'zenoh/src/sample.rs'
        source.parent.mkdir(parents=True)
        source.write_text('old\n')
        before = source.read_bytes()
        self.assertEqual(self.inspect(diff('zenoh/src/sample.rs')), ['zenoh/src/sample.rs'])
        self.assertEqual(source.read_bytes(), before)

    def test_empty_patch_fails_closed(self):
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            self.inspect('')


if __name__ == '__main__':
    unittest.main(verbosity=2)
