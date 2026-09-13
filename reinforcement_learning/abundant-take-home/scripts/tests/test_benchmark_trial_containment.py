"""Exercise Harbor's real queue/run boundary without Docker or model calls.

Run with Harbor's installed Python; the environment and agent are local fakes.
The separate Docker deadline fixture verifies the real guard/teardown path.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_deadline import AgentQuiescenceError, require_quiescence
import benchmark_trial_containment as containment

try:
    from harbor.trial.trial import Trial
    from harbor.trial.queue import TrialQueue
    from harbor.trial.hooks import TrialEvent
except ImportError:
    Trial = TrialQueue = TrialEvent = None


class Result:
    def __init__(self, name):
        self.trial_name = name
        self.finished_at = None
        self.exception_info = None
        self.verifier = None
        self.verifier_result = None

    def model_dump_json(self, **kwargs):
        values = vars(self).copy()
        if self.exception_info:
            values['exception_info'] = vars(self.exception_info)
        return json.dumps(values, default=str, **kwargs)


class FixtureTrial:
    """Real Trial.run/finalization/queue; only agent and environment are fakes."""
    def __init__(self, root, name, *, fails=False, contain=True):
        self.config = SimpleNamespace(trial_name=name)
        directory = root / name
        directory.mkdir()
        self.paths = SimpleNamespace(trial_dir=directory, result_path=directory / 'result.json')
        self.result = Result(name)
        self.logger = SimpleNamespace(debug=lambda *args, **kwargs: None)
        self._hooks = {event: [] for event in TrialEvent}
        self._is_agent_environment_stopped = False
        self._benchmark_deadline_state = {'quiescent': not fails}
        self.fails = fails
        self.contain = contain
        self.cancelled = False
        self.recovery_calls = 0
        self.grading_entered = False
        self.logger_closed = False
        self.after_finalize = None
        self.injected_error = AgentQuiescenceError if fails else None

    def _init_result(self):
        pass

    def add_hook(self, event, hook):
        self._hooks[event].append(hook)

    async def _emit(self, event):
        for hook in self._hooks[event]:
            await hook(SimpleNamespace(trial_id=self.config.trial_name))

    async def _prepare(self):
        await asyncio.sleep(0)

    async def _run(self):
        if self.injected_error:
            raise self.injected_error('injected missing cleanup proof')
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        require_quiescence(self)
        self.grading_entered = True

    def _record_exception(self, error):
        if self.result.exception_info is None:
            self.result.exception_info = SimpleNamespace(exception_type=type(error).__name__)

    async def _recover_outputs(self):
        self.recovery_calls += 1
        # This is the same second guard check as SingleStepTrial recovery.
        require_quiescence(self)

    async def _stop_agent_environment(self):
        self._is_agent_environment_stopped = True

    @staticmethod
    def _now():
        return Trial._now()

    async def _finalize(self):
        await Trial._finalize(self)
        if self.after_finalize:
            self.after_finalize(self)

    def _close_logger_handler(self):
        self.logger_closed = True

    async def run(self):
        if self.contain:
            return await containment.run_contained(self, Trial.run)
        return await Trial.run(self)


@unittest.skipUnless(Trial is not None, 'Requires the installed Harbor interpreter')
class ContainmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.probe = patch.object(containment, 'confirm_project_stopped',
                                  new_callable=AsyncMock, return_value='fixture-project')
        self.stopped = self.probe.start()
        self.addCleanup(self.probe.stop)

    async def run_pair(self, *, contain=True):
        failed = FixtureTrial(self.root, 'failed', fails=True, contain=contain)
        healthy = FixtureTrial(self.root, 'healthy', contain=contain)
        trials = {trial.config.trial_name: trial for trial in (failed, healthy)}
        ended = []

        async def on_end(event):
            ended.append(event.trial_id)

        queue = TrialQueue(n_concurrent=2).on_trial_ended(on_end)

        async def create(config):
            return trials[config.trial_name]

        self.pair = failed, healthy, ended
        with patch.object(Trial, 'create', side_effect=create):
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(queue.submit(trial.config)) for trial in trials.values()]
        return [task.result() for task in tasks]

    async def test_failed_cleanup_keeps_healthy_sibling_and_end_hooks_intact(self):
        results = await self.run_pair()
        failed, healthy, ended = self.pair
        self.assertEqual([result.trial_name for result in results], ['failed', 'healthy'])
        self.assertEqual(ended, ['failed', 'healthy'])
        self.assertEqual(failed.recovery_calls, 1)
        self.assertFalse(failed.grading_entered)
        self.assertTrue(healthy.grading_entered)
        self.assertFalse(healthy.cancelled)
        self.assertTrue(failed.logger_closed)
        self.assertEqual(failed.result.exception_info.exception_type, 'AgentQuiescenceError')
        self.assertIsNone(failed.result.verifier_result)
        marker = json.loads((failed.paths.trial_dir / 'benchmark-containment.json').read_text())
        self.assertTrue(marker['grading_withheld'])
        self.assertEqual(marker['running_project_containers'], 0)
        self.stopped.assert_awaited_once_with(failed)

    async def test_without_fix_same_harbor_recovery_error_cancels_sibling(self):
        with self.assertRaises(ExceptionGroup):
            await self.run_pair(contain=False)
        self.assertTrue(self.pair[1].cancelled)
        self.assertFalse(self.pair[1].grading_entered)

    async def test_unconfirmed_container_teardown_propagates_guard_error(self):
        self.stopped.return_value = None
        trial = FixtureTrial(self.root, 'unconfirmed', fails=True)
        with self.assertRaises(AgentQuiescenceError):
            await trial.run()
        self.assertFalse((trial.paths.trial_dir / 'benchmark-containment.json').exists())

    async def test_failed_teardown_probe_does_not_create_containment_proof(self):
        self.stopped.side_effect = OSError('Docker unavailable')
        trial = FixtureTrial(self.root, 'probe-failed', fails=True)
        with self.assertRaises(OSError):
            await trial.run()
        self.assertFalse((trial.paths.trial_dir / 'benchmark-containment.json').exists())

    async def test_missing_changed_graded_or_unfinished_result_is_not_contained(self):
        def remove(trial):
            trial.paths.result_path.unlink()

        def changed(trial):
            trial.paths.result_path.write_text('{}')

        def unfinished(trial):
            trial.result.finished_at = None
            trial.paths.result_path.write_text(trial.result.model_dump_json())

        def graded(trial):
            trial.result.verifier_result = {'rewards': {'reward': 1}}
            trial.paths.result_path.write_text(trial.result.model_dump_json())

        def verifier_entered(trial):
            trial.result.verifier = {'started_at': '2026-09-13T00:00:00Z'}
            trial.paths.result_path.write_text(trial.result.model_dump_json())

        def unstopped(trial):
            trial._is_agent_environment_stopped = False

        for change in (remove, changed, unfinished, graded, verifier_entered, unstopped):
            with self.subTest(change=change.__name__):
                trial = FixtureTrial(self.root, change.__name__, fails=True)
                trial.after_finalize = change
                with self.assertRaises((AgentQuiescenceError, OSError)):
                    await trial.run()
                self.assertFalse((trial.paths.trial_dir / 'benchmark-containment.json').exists())
        self.stopped.assert_not_awaited()

    async def test_end_hook_failure_is_not_suppressed_or_reemitted(self):
        trial = FixtureTrial(self.root, 'hook-failed', fails=True)
        hook = AsyncMock(side_effect=AgentQuiescenceError('uncompleted job hook'))
        trial.add_hook(TrialEvent.END, hook)
        with self.assertRaises(AgentQuiescenceError):
            await trial.run()
        hook.assert_awaited_once()
        self.stopped.assert_not_awaited()

    async def test_cancellation_is_never_converted_to_a_normal_result(self):
        trial = FixtureTrial(self.root, 'cancelled', fails=False)
        trial.injected_error = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await trial.run()
        self.stopped.assert_not_awaited()

    async def test_cancellation_followed_by_recovery_guard_failure_is_not_contained(self):
        trial = FixtureTrial(self.root, 'cancelled-guard', fails=True)
        trial.injected_error = asyncio.CancelledError
        with self.assertRaises(AgentQuiescenceError):
            await trial.run()
        self.assertEqual(trial.result.exception_info.exception_type, 'CancelledError')
        self.stopped.assert_not_awaited()

    async def test_task_cancellation_cannot_be_hidden_by_a_guard_exception(self):
        trial = FixtureTrial(self.root, 'cancel-requested', fails=True)
        started = asyncio.Event()

        async def cancelled_with_guard_error(_trial):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # A cleanup finally block can replace CancelledError with its
                # guard failure before Harbor has a chance to record it.
                trial._record_exception(AgentQuiescenceError('cleanup failed'))
                await trial._finalize()
                raise AgentQuiescenceError('cleanup failed')

        task = asyncio.create_task(containment.run_contained(trial, cancelled_with_guard_error))
        await started.wait()
        task.cancel()
        with self.assertRaises(AgentQuiescenceError):
            await task
        self.stopped.assert_not_awaited()

    async def test_unrelated_escaping_error_is_not_suppressed(self):
        trial = FixtureTrial(self.root, 'unrelated', fails=False)

        async def broken(_trial):
            raise RuntimeError('unrelated runner failure')

        with self.assertRaisesRegex(RuntimeError, 'unrelated runner failure'):
            await containment.run_contained(trial, broken)
        self.stopped.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
