"""No-process, no-model checks for guarded activation preconditions."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('activation', SCRIPTS / 'activate-benchmark-deadline.py')
activation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activation)


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.root_patch = patch.object(activation, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.temporary.cleanup)
        (self.root / 'jobs').mkdir()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def suite(self):
        sources = {}
        for name in activation.SOURCES:
            path = self.write(name, {'source': name})
            sources[name] = activation.digest(path)
        return self.write('suite.json', {
            'overall_passed': True, 'guarded': True, 'model_calls': 0,
            'source_sha256': sources,
            'cases': [{'mode': mode, 'passed': True, 'assertions': {'proof': True}}
                      for mode in activation.MODES]})

    def control(self):
        before = {'paused': False, 'max_active': 32}
        after = {**before, 'paused': True, 'pause_reason': 'our temporary pause'}
        self.write('jobs/campaign.control.json', after)
        return {'controls': {'campaign': {'path': 'jobs/campaign.control.json',
                                          'applied': True, 'before': before, 'after': after}}}

    def test_suite_requires_complete_guarded_case_set(self):
        path = self.suite()
        self.assertEqual(activation.validate_suite(path), activation.digest(path))
        value = activation.read(path)
        value['cases'].pop()
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError, 'five required'):
            activation.validate_suite(path)

    def test_suite_rejects_changed_source_or_summary(self):
        path = self.suite()
        pinned = activation.validate_suite(path)
        source = self.root / next(iter(activation.SOURCES))
        source.write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'sources changed'):
            activation.validate_suite(path, pinned)
        path.write_text(path.read_text() + '\n')
        with self.assertRaisesRegex(RuntimeError, 'summary changed'):
            activation.validate_suite(path, pinned)

    def test_restore_changes_only_previous_pause_fields(self):
        record = self.control()
        activation.restore_controls(record)
        self.assertEqual(activation.read(self.root / 'jobs/campaign.control.json'),
                         record['controls']['campaign']['before'])

    def test_concurrent_control_edit_is_not_overwritten(self):
        record = self.control()
        path = self.root / 'jobs/campaign.control.json'
        changed = {**activation.read(path), 'max_active': 8}
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(RuntimeError, 'control changed'):
            activation.restore_controls(record)
        self.assertEqual(activation.read(path), changed)

    def test_plan_config_hash_mismatch_blocks(self):
        record = self.control()
        for name in activation.LAUNCHERS:
            self.write(name, {'source': name})
        config = self.write('jobs/campaign.config.json', {'n_attempts': 3})
        self.write('jobs/campaign.plan.json', {'jobs': [
            {'config': 'jobs/campaign.config.json', 'sha256': activation.digest(config)}]})
        snapshot = activation.workload_snapshot(record)
        self.assertIn('jobs/campaign.plan.json', snapshot)
        config.write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'locked campaign plan'):
            activation.workload_snapshot(record)

    def test_supervisor_restart_preserves_virtualenv_interpreter(self):
        expected = {'args': ['/venv/bin/python', '/repo/scripts/run-candidate-screen.py'],
                    'executable': '/base/python', 'campaign': 'campaign', 'pid': 456}
        child = Mock(pid=123)
        child.poll.return_value = None
        (self.root / 'jobs/campaign.pid').write_text('123\n')
        self.write('jobs/campaign.summary.json', {'status': 'paused'})
        with patch.object(activation, 'stop_named'), patch.object(activation.time, 'time', return_value=0), \
                patch.object(activation.subprocess, 'Popen', return_value=child) as popen:
            result = activation.restart_supervisor(expected, lambda: None)
        self.assertEqual(result['new_pid'], 123)
        self.assertEqual(popen.call_args.args[0][0], '/venv/bin/python')
        self.assertNotIn('env', popen.call_args.kwargs)

    def test_missing_admission_stats_requires_empty_job_and_daemon(self):
        record = self.control()
        self.write('jobs/campaign/config.json', {'job_name': 'campaign'})
        runner = {'campaign': 'campaign', 'pid': 456}
        with patch.object(activation, 'same_process', return_value=runner), \
                patch.object(activation, 'job_containers', return_value=[]):
            self.assertTrue(activation.drain_state(runner, record)['ready'])
            (self.root / 'jobs/campaign/trial').mkdir()
            with self.assertRaisesRegex(RuntimeError, 'count is unavailable'):
                activation.drain_state(runner, record)

    def test_owned_running_container_blocks_missing_stats_inference(self):
        record = self.control()
        self.write('jobs/campaign/config.json', {'job_name': 'campaign'})
        runner = {'campaign': 'campaign', 'pid': 456}
        with patch.object(activation, 'same_process', return_value=runner), \
                patch.object(activation, 'job_containers', return_value=[{'state': {'Running': True}}]):
            with self.assertRaisesRegex(RuntimeError, 'count is unavailable'):
                activation.drain_state(runner, record)


if __name__ == '__main__':
    unittest.main()
