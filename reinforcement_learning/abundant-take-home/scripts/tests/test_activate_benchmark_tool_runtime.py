"""Rolling activation tests: synthetic jobs only, no Docker, signals, or models."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('tool_activation', SCRIPTS / 'activate-benchmark-tool-runtime.py')
activation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activation)


class RollingActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        root_patch = patch.object(activation, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.shared = self.write('jobs/shared.control.json', {'max_active': 12, 'reserve_mb': 4096})
        for name in activation.BOUND_SOURCES:
            self.write(name, {'source': name})
        for name in activation.LAUNCHERS:
            (self.root / name).write_text('from benchmark_mini_tool_runtime import install as install_agent_runtime\n')
        self.proof = self.write('research/proof.json', {
            'passed': True, 'model_calls': 0, 'assertions': {'offline_smoke': True},
            'source_sha256': {name: activation.digest(self.root / name)
                              for name in activation.RUNTIME_SOURCES.values()}})
        self.entries = [self.campaign('main', 101), self.campaign('revision', 202)]
        self.state = {
            'proof': str(self.proof.relative_to(self.root)), 'proof_sha256': activation.digest(self.proof),
            'source_sha256': activation.validate_proof(self.proof), 'campaigns': self.entries,
        }

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def campaign(self, name, pid):
        before = {'paused': False, 'max_active': 12, 'shared_pool': str(self.shared),
                  'excluded_tasks': [], 'reserve_mb': 4096}
        self.write(f'jobs/{name}.control.json', before)
        config = self.write(f'jobs/{name}.config.json', {'n_attempts': 20, 'job_name': name})
        self.write(f'jobs/{name}.plan.json', {'target': 180, 'jobs': [
            {'name': name, 'config': str(config.relative_to(self.root)), 'sha256': activation.digest(config)}]})
        self.write(f'jobs/{name}/config.json', {'n_attempts': 20, 'job_name': name})
        self.write(f'jobs/{name}/lock.json', {'task': name, 'checksum': 'untouched'})
        runner = {'pid': pid, 'start_ticks': str(pid + 100), 'job': name}
        with patch.object(activation, 'runner_identity', return_value=runner), \
                patch.object(activation, 'runners', return_value=[runner]):
            return activation.snapshot_campaign(name, pid, 'fixture')

    def paused(self, entry=None):
        entry = entry or self.entries[0]
        activation.set_pause(entry, True)
        return entry

    def resource_evidence(self, entry, active=0, claims=0, age=0):
        self.write(str(Path(entry['control']).with_suffix('.resources.json')), {
            'updated_at': activation.datetime.fromtimestamp(time.time() - age, activation.timezone.utc).isoformat(),
            'active_trials': active, 'trial_names': [f'trial{i}' for i in range(active)]})
        runner = entry['runner']
        self.write('jobs/shared.control.state.json', {'participants': {
            f"{runner['pid']}:{runner['start_ticks']}": {
                'pid': runner['pid'], 'identity': runner['start_ticks'],
                'control': str(self.root / entry['control']),
                'trials': {f'own{i}': {} for i in range(claims)}},
            'other:999': {'pid': 999, 'identity': '999',
                          'control': str(self.root / 'jobs/other.control.json'),
                          'trials': {'healthy_other_campaign': {}}}}})

    def runtime_sidecar(self, entry, name='new_trial', **changes):
        value = {key: self.state['source_sha256'][path] for key, path in activation.RUNTIME_SOURCES.items()}
        value.update(scope='agent_process_only', preflight_passed=True, installed=True,
                     python='3.12.11', package_version='2.4.6')
        value.update(changes)
        return self.write(f"jobs/{entry['runner']['job']}/{name}/agent/benchmark-agent-tool-runtime.json", value)

    def test_proof_rejects_failed_missing_assertions_or_missing_source(self):
        valid = activation.read(self.proof)
        for changed in ({**valid, 'passed': False}, {**valid, 'assertions': {}},
                        {**valid, 'assertions': {'bad': False}}, {**valid, 'model_calls': True},
                        {**valid, 'source_sha256': {}}):
            self.write('research/proof.json', changed)
            with self.assertRaises(RuntimeError):
                activation.validate_proof(self.proof)

    def test_proof_requires_both_launchers_and_current_sources(self):
        launcher = self.root / next(iter(activation.LAUNCHERS))
        launcher.write_text('from benchmark_agent_runtime import install as install_agent_runtime\n')
        with self.assertRaisesRegex(RuntimeError, 'Both runner'):
            activation.validate_proof(self.proof)
        launcher.write_text('from benchmark_mini_tool_runtime import install as install_agent_runtime\n')
        (self.root / activation.RUNTIME_SOURCES['helper_sha256']).write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'source hash'):
            activation.validate_proof(self.proof)

    def test_plan_config_mismatch_or_missing_job_lock_blocks_snapshot(self):
        entry = self.entries[0]
        runner = entry['runner']
        with patch.object(activation, 'runner_identity', return_value=runner), \
                patch.object(activation, 'runners', return_value=[runner]):
            (self.root / 'jobs/main/lock.json').unlink()
            with self.assertRaisesRegex(RuntimeError, 'raw config or lock'):
                activation.snapshot_campaign('main', 101, 'fixture')
            self.write('jobs/main/lock.json', {})
            self.write('jobs/main.config.json', {'n_attempts': 40})
            with self.assertRaisesRegex(RuntimeError, 'locked plan'):
                activation.snapshot_campaign('main', 101, 'fixture')

    def test_config_lock_proof_and_shared_cap_drift_all_block(self):
        for name in ('jobs/main/lock.json', 'jobs/main.config.json', 'research/proof.json',
                     'jobs/shared.control.json', 'scripts/benchmark_agent_runtime.py'):
            path = self.root / name
            before = path.read_bytes()
            path.write_bytes(before + b'\n')
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                activation.check_hashes(self.state)
            path.write_bytes(before)

    def test_prior_pause_is_not_owned(self):
        entry = self.entries[0]
        self.write(entry['control'], {**entry['before'], 'paused': True, 'pause_reason': 'human review'})
        with self.assertRaisesRegex(RuntimeError, 'already paused'):
            activation.snapshot_campaign('main', 101, 'fixture')

    def test_pause_and_restore_recover_write_intent_and_preserve_other_campaign(self):
        entry, other = self.entries
        other_bytes = (self.root / other['control']).read_bytes()
        for paused in (True, True, False, False):
            activation.set_pause(entry, paused)
            self.assertEqual(activation.read(self.root / entry['control']), entry['after' if paused else 'before'])
        self.assertEqual((self.root / other['control']).read_bytes(), other_bytes)

    def test_control_change_is_never_overwritten_during_pause_or_restore(self):
        entry = self.entries[0]
        changed = {**entry['before'], 'max_active': 8}
        self.write(entry['control'], changed)
        with self.assertRaisesRegex(RuntimeError, 'control changed'):
            activation.set_pause(entry, True)
        self.write(entry['control'], entry['after'])
        changed = {**entry['after'], 'pause_reason': 'new human decision'}
        self.write(entry['control'], changed)
        with self.assertRaisesRegex(RuntimeError, 'control changed'):
            activation.set_pause(entry, False)
        self.assertEqual(activation.read(self.root / entry['control']), changed)

    def test_three_empty_signals_are_independently_required_but_other_campaign_can_run(self):
        entry = self.paused()
        with patch.object(activation, 'same_process', return_value=entry['runner']), \
                patch.object(activation, 'runners', return_value=[entry['runner']]), \
                patch.object(activation, 'job_containers', return_value=[]) as containers:
            for active, claims in ((1, 0), (0, 1), (1, 1)):
                self.resource_evidence(entry, active, claims)
                self.assertFalse(activation.drain_state(entry)['ready'])
            self.resource_evidence(entry)
            containers.return_value = [{'state': {'Running': True}}]
            self.assertFalse(activation.drain_state(entry)['ready'])
            containers.return_value = []
            self.assertTrue(activation.drain_state(entry)['ready'])

    def test_stale_future_missing_or_wrong_identity_evidence_does_not_authorize_stop(self):
        entry = self.paused()
        with patch.object(activation, 'same_process', return_value=entry['runner']), \
                patch.object(activation, 'runners', return_value=[entry['runner']]), \
                patch.object(activation, 'job_containers', return_value=[]):
            for age in (100, -10):
                self.resource_evidence(entry, age=age)
                self.assertFalse(activation.drain_state(entry)['ready'])
            self.resource_evidence(entry)
            shared = activation.read(self.root / 'jobs/shared.control.state.json')
            next(iter(shared['participants'].values()))['identity'] = 'reused'
            self.write('jobs/shared.control.state.json', shared)
            with self.assertRaisesRegex(RuntimeError, 'identity'):
                activation.drain_state(entry)
            (self.root / Path(entry['control']).with_suffix('.resources.json')).unlink()
            with self.assertRaises(FileNotFoundError):
                activation.drain_state(entry)

    def test_final_recheck_prevents_any_signal_when_work_reappears(self):
        entry = self.paused()
        with patch.object(activation, 'same_process', return_value=entry['runner']), \
                patch.object(activation.os, 'pidfd_open', return_value=123, create=True), \
                patch.object(activation.os, 'close'), \
                patch.object(activation, 'drain_state', return_value={'ready': False}), \
                patch.object(activation.signal, 'pidfd_send_signal', create=True) as send:
            with self.assertRaisesRegex(RuntimeError, 'Work reappeared'):
                activation.stop_empty(entry, lambda: None)
            send.assert_not_called()

    def test_reused_pid_never_reaches_pidfd_or_signal(self):
        with patch.object(activation, 'same_process', side_effect=RuntimeError('PID reused')), \
                patch.object(activation.os, 'pidfd_open', create=True) as opened:
            with self.assertRaisesRegex(RuntimeError, 'PID reused'):
                activation.stop_empty(self.entries[0], lambda: None)
            opened.assert_not_called()

    def test_failed_resume_holds_only_unchanged_current_control(self):
        entry, other = self.entries
        entry['phase'] = 'waiting_for_evidence'
        activation.hold_failed_resume(self.state)
        self.assertEqual(entry['phase'], 'blocked_after_resume')
        self.assertEqual(activation.read(self.root / entry['control']), entry['after'])
        self.assertEqual(activation.read(self.root / other['control']), other['before'])
        entry['phase'] = 'waiting_for_evidence'
        changed = {**entry['before'], 'pause_reason': 'human decision', 'paused': True}
        self.write(entry['control'], changed)
        activation.hold_failed_resume(self.state)
        self.assertEqual(activation.read(self.root / entry['control']), changed)

    def test_sidecar_must_be_new_passing_and_match_all_three_sources(self):
        entry = self.entries[0]
        old = self.runtime_sidecar(entry, 'historical')
        entry.update(old_sidecars=[str(old.relative_to(self.root))], resume_started_at=0)
        self.assertIsNone(activation.runtime_evidence(entry, self.state))
        self.runtime_sidecar(entry, helper_sha256='wrong')
        with self.assertRaisesRegex(RuntimeError, 'preflight'):
            activation.runtime_evidence(entry, self.state)
        self.runtime_sidecar(entry, preflight_passed=False)
        with self.assertRaisesRegex(RuntimeError, 'preflight'):
            activation.runtime_evidence(entry, self.state)
        path = self.runtime_sidecar(entry)
        self.assertEqual(activation.runtime_evidence(entry, self.state)['sha256'], activation.digest(path))

    def test_rolling_state_machine_waits_for_new_runner_and_sidecar_before_next_campaign(self):
        entry, other = self.entries
        saved = []
        save = Mock(side_effect=lambda: saved.append(
            (entry['phase'], activation.read(self.root / entry['control'])['paused'])))
        empty = {'ready': True}
        alive = {'value': entry['runner']}
        found = {'value': [entry['runner']]}

        def stop(*args):
            alive['value'] = None
            found['value'] = []

        with patch.object(activation, 'runner_identity', return_value=entry['runner']), \
                patch.object(activation, 'same_process', side_effect=lambda *args: alive['value']), \
                patch.object(activation, 'runners', side_effect=lambda *args: found['value']), \
                patch.object(activation, 'drain_state', return_value=empty) as drain, \
                patch.object(activation, 'stop_empty', side_effect=stop) as stop_mock:
            activation.advance(entry, self.state, save)  # intent and pause in the same call
            self.assertEqual(entry['phase'], 'draining')
            self.assertEqual(saved, [('pausing', False), ('draining', True)])
            self.assertTrue(activation.read(self.root / entry['control'])['paused'])
            activation.advance(entry, self.state, save)  # one empty check
            stop_mock.assert_not_called()
            drain.return_value = {'ready': False}
            activation.advance(entry, self.state, save)  # active work resets evidence
            self.assertEqual(entry['consecutive_empty'], 0)
            drain.return_value = empty
            activation.advance(entry, self.state, save)
            activation.advance(entry, self.state, save)
            self.assertEqual(entry['phase'], 'stopping')
            activation.advance(entry, self.state, save)
            self.assertEqual(entry['phase'], 'restoring')
            activation.advance(entry, self.state, save)
            self.assertEqual(activation.read(self.root / entry['control']), entry['before'])
            activation.advance(entry, self.state, save)  # no replacement yet
            self.assertEqual(entry['phase'], 'resuming')
            found['value'] = [{'pid': 303, 'start_ticks': '500', 'job': 'main'}]
            activation.advance(entry, self.state, save)
            self.assertEqual(entry['phase'], 'waiting_for_evidence')
            self.assertEqual(other['phase'], 'pending')
            self.assertEqual(activation.read(self.root / other['control']), other['before'])
            self.runtime_sidecar(entry)
            activation.advance(entry, self.state, save)
            self.assertEqual(entry['phase'], 'activated')
            stop_mock.assert_called_once()
            self.assertEqual(other['phase'], 'pending')

    def test_two_empty_observations_cannot_be_bypassed_by_recovered_stopping_phase(self):
        entry = self.paused()
        entry.update(phase='stopping', consecutive_empty=1)
        with patch.object(activation, 'stop_empty') as stop:
            with self.assertRaisesRegex(RuntimeError, 'Two confirmed'):
                activation.advance(entry, self.state, lambda: None)
            stop.assert_not_called()

    def test_inspection_is_read_only_and_creates_no_output_directory(self):
        output = self.root / 'jobs/activation-output'
        argv = ['activate', '--campaign', 'main=101', '--campaign', 'revision=202',
                '--proof', str(self.proof), '--output', str(output)]
        with patch.object(sys, 'argv', argv), \
                patch.object(activation, 'snapshot_campaign', side_effect=self.entries), \
                patch.object(activation, 'dump') as dump, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(activation.main(), 0)
            dump.assert_not_called()
        self.assertFalse(output.exists())
        self.assertFalse((self.root / 'jobs/.tool-runtime-activation.lock').exists())

    def test_recovery_source_drift_reholds_resumed_campaign_without_starting_watcher(self):
        entry, other = self.entries
        entry['phase'] = 'waiting_for_evidence'
        self.state['requested'] = [{'campaign': 'main', 'pid': 101}, {'campaign': 'revision', 'pid': 202}]
        output = self.root / 'jobs/activation-output'
        self.write('jobs/activation-output/activation.json', self.state)
        (self.root / 'scripts/benchmark_agent_runtime.py').write_text('changed after crash')
        argv = ['activate', '--campaign', 'main=101', '--campaign', 'revision=202',
                '--proof', str(self.proof), '--output', str(output), '--apply']
        with patch.object(sys, 'argv', argv), \
                patch.object(activation, 'process', return_value={'pid': 7, 'start_ticks': '8'}), \
                patch.object(activation.threading.Thread, 'start') as start:
            self.assertEqual(activation.main(), 1)
            start.assert_not_called()
        result = activation.read(output / 'activation.json')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['campaigns'][0]['phase'], 'blocked_after_resume')
        self.assertEqual(activation.read(self.root / entry['control']), entry['after'])
        self.assertEqual(activation.read(self.root / other['control']), other['before'])


if __name__ == '__main__':
    unittest.main()
