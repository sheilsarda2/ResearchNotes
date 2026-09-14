"""No Docker/model calls: an observer failure must not abandon a live control."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

spec = importlib.util.spec_from_file_location('control', Path(__file__).with_name('validate_controls_immutable.py'))
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class ObservationLifecycle(unittest.TestCase):
    def test_transient_observation_error_waits_for_child_exit(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.1); raise SystemExit(23)'])
        observed = []
        def observe():
            observed.append(child.poll())
            if len(observed) == 1:
                raise ValueError('synthetic partial JSON read')
        try:
            errors = control.wait_with_observation(child, observe, interval=0.01)
            self.assertEqual(child.returncode, 23)
            self.assertIsNone(observed[0])
            self.assertEqual(observed[-1], 23)
            self.assertEqual(errors, [{'error_type': 'ValueError', 'error': 'synthetic partial JSON read'}])
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_persistent_observer_failure_still_reaps_same_child(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.1)'])
        calls = []
        def observe():
            calls.append(child.poll())
            raise OSError('synthetic unavailable evidence path')
        try:
            errors = control.wait_with_observation(child, observe, interval=0.01)
            self.assertEqual(child.returncode, 0)
            self.assertIsNone(calls[0])
            self.assertEqual(calls[-1], 0)
            self.assertGreater(len(calls), 1)
            self.assertEqual(len(errors), 1)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


if __name__ == '__main__':
    unittest.main()
