"""ZIP header timestamps may be clamped; source and copied files stay exact."""
import calendar
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('timestamp_packager', ROOT / 'scripts/package-takehome-evidence.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class ZipTimestampTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.root_patch = patch.object(p, 'ROOT', self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def identity(path):
        return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns

    def file_round_trip(self, stamp, expected_zip_time):
        source = self.root / 'source' / 'raw.bin'
        source.parent.mkdir()
        source.write_bytes(b'original raw bytes\x00\xff\n' * 100)
        os.utime(source, ns=(stamp, stamp))
        self.assertEqual(source.stat().st_mtime_ns, stamp)
        source_before = self.identity(source)
        tree = p.inventory('source')
        p.copy_entry(dict(source='source', destination='jobs/job/trial', tree=tree), self.root / 'pack')
        copied = self.root / 'pack/jobs/job/trial/raw.bin'
        self.assertEqual(self.identity(copied), source_before)
        copied_before = self.identity(copied)

        result = p.zip_verified(self.root / 'pack', self.root / 'pack.zip')

        self.assertEqual(self.identity(source), source_before)
        self.assertEqual(self.identity(copied), copied_before)
        self.assertEqual(tree['files']['raw.bin']['mtime_ns'], stamp)
        with zipfile.ZipFile(self.root / 'pack.zip') as archive:
            info = archive.getinfo('jobs/job/trial/raw.bin')
            self.assertEqual(info.date_time, expected_zip_time)
            self.assertEqual(hashlib.sha256(archive.read(info)).hexdigest(), source_before[0])
            self.assertIsNone(archive.testzip())
        self.assertEqual(result['sha256'], hashlib.sha256((self.root / 'pack.zip').read_bytes()).hexdigest())

    def test_epoch_mtime_only_clamps_zip_header(self):
        self.file_round_trip(0, (1980, 1, 1, 0, 0, 0))

    def test_pre_1980_mtime_only_clamps_zip_header(self):
        stamp = calendar.timegm((1975, 6, 15, 12, 34, 56)) * 10**9 + 123456789
        self.file_round_trip(stamp, (1980, 1, 1, 0, 0, 0))

    def test_post_2107_mtime_only_clamps_zip_header(self):
        stamp = calendar.timegm((2200, 6, 15, 12, 34, 56)) * 10**9 + 123456789
        self.file_round_trip(stamp, (2107, 12, 31, 23, 59, 58))

    def test_normal_mtime_uses_normal_dos_rounding(self):
        stamp = calendar.timegm((2026, 9, 14, 12, 34, 57)) * 10**9 + 123456789
        local = time.localtime(stamp / 10**9)[:6]
        self.file_round_trip(stamp, local[:5] + (local[5] // 2 * 2,))

    def test_empty_directory_headers_clamp_without_changing_directory_mtime(self):
        pack = self.root / 'pack'
        pack.mkdir()
        values = {
            'old': (0, (1980, 1, 1, 0, 0, 0)),
            'future': (calendar.timegm((2200, 6, 15, 12, 34, 56)) * 10**9,
                       (2107, 12, 31, 23, 59, 58)),
        }
        for name, (stamp, _) in values.items():
            directory = pack / name
            directory.mkdir()
            os.utime(directory, ns=(stamp, stamp))
        p.zip_verified(pack, self.root / 'directories.zip')
        with zipfile.ZipFile(self.root / 'directories.zip') as archive:
            self.assertEqual(set(archive.namelist()), {'old/', 'future/'})
            for name, (stamp, zip_time) in values.items():
                self.assertEqual((pack / name).stat().st_mtime_ns, stamp)
                self.assertEqual(archive.getinfo(name + '/').date_time, zip_time)
                self.assertEqual(archive.read(name + '/'), b'')


if __name__ == '__main__':
    unittest.main(verbosity=2)
