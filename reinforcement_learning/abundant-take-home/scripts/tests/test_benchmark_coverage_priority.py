import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_coverage_priority as coverage
import benchmark_interleaving as rounds
from benchmark_shared_admission import SharedAdmission

SNAPSHOT = dict(total_mb=32768, available_mb=30000, memory_pressure_pct=0)


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = self.root/'shared.json'
        self.control.write_text(json.dumps(dict(max_active=12,min_total_mb=0,reserve_mb=0,start_interval_sec=0)))
        rounds.TRIALS.clear(); rounds.QUEUES.clear(); rounds._cache.clear()
        self.shared = SharedAdmission(self.control,self.root/'job.control.json')
        self.keys = [rounds.cell_key(task,'anthropic/claude-sonnet-5','high') for task in ['done','failed','unseen']]
        done,failed,unseen = self.keys
        self.policy = dict(version=1,campaigns=['job'],cells={k:dict(job='job',target=20) for k in self.keys},
            counts=dict(zip(self.keys,[1,2,0])),receipts={
                'done-old':dict(cell=done,job='job'),
                'failed-old1':dict(cell=failed,job='job'),
                'failed-old2':dict(cell=failed,job='job')})
        self.policy['first_sweep'] = dict(version=1,enabled=True,scope={k:'job' for k in self.keys},
            completed=dict(zip(self.keys,[1,0,0])),settled_receipts={n:{} for n in self.policy['receipts']})
        for key,name in zip(self.keys,['done-next','failed-next','unseen-next']):
            rounds.TRIALS[name] = dict(cell=key,job='job');rounds.QUEUES[key].append(name)
        self.save()

    def save(self):
        with self.shared.locked() as (_,state):state['interleaving']=copy.deepcopy(self.policy)

    def test_failed_start_is_prioritized_without_rewriting_dispatch_history(self):
        self.assertIsNone(self.shared.try_acquire('failed-next',SNAPSHOT,{}))
        state=json.loads(self.shared.state_path.read_text())
        self.assertEqual(state['interleaving']['counts'][self.keys[1]],3)
        self.assertIn('failed-old1',state['interleaving']['receipts'])
        self.assertIn('prioritizing',self.shared.try_acquire('done-next',SNAPSHOT,{}))
        self.assertIsNone(self.shared.try_acquire('unseen-next',SNAPSHOT,{}))

    def test_running_and_unclassified_first_result_cannot_get_duplicate(self):
        self.assertIsNone(self.shared.try_acquire('failed-next',SNAPSHOT,{}))
        name='failed-duplicate';rounds.TRIALS[name]=dict(cell=self.keys[1],job='job');rounds.QUEUES[self.keys[1]].append(name)
        self.assertIn('already running',self.shared.try_acquire(name,SNAPSHOT,{}))
        self.shared.release('failed-next')
        self.assertIn('awaiting classification',self.shared.try_acquire(name,SNAPSHOT,{}))
        with self.shared.locked() as (_,state):
            state['interleaving']['first_sweep']['settled_receipts']['failed-next']={}
        self.assertIsNone(self.shared.try_acquire(name,SNAPSHOT,{}))

    def test_finalized_result_still_cleaning_up_is_not_retried(self):
        with self.shared.locked() as (_,state):
            state['participants'][self.shared.key]['trials']['failed-old2']={'started_at':0,'startup_reserve_mb':0}
        self.assertIn('already running',self.shared.try_acquire('failed-next',SNAPSHOT,{}))

    def test_priority_does_not_bypass_memory_concurrency_or_holds(self):
        self.assertEqual(self.shared.try_acquire('failed-next',{**SNAPSHOT,'available_mb':0},{}),'shared memory reserve')
        with self.shared.locked() as (_,state):
            state['interleaving']['held_cells']={self.keys[1]:{'job':'job','reason':'external task review'}}
        self.assertIn('held',self.shared.try_acquire('failed-next',SNAPSHOT,{}))
        self.control.write_text(json.dumps(dict(max_active=0)))
        self.assertEqual(self.shared.try_acquire('unseen-next',SNAPSHOT,{}),'shared concurrency')

    def test_selection_mismatch_and_malformed_data_wait_without_throwing(self):
        for change in [{'completed':None},{'scope':{}},{'version':2},{'settled_receipts':None}]:
            with self.subTest(change=change):
                bad=copy.deepcopy(self.policy);bad['first_sweep'].update(change)
                priority,reason=coverage.priority_decision({'interleaving':bad},self.keys[1])
                self.assertFalse(priority);self.assertIn('valid first-result snapshot',reason)

    def test_existing_claim_and_raw_primary_limit_are_preserved(self):
        self.assertIsNone(self.shared.try_acquire('failed-next',SNAPSHOT,{}))
        with self.shared.locked() as (_,state):
            state['interleaving']['first_sweep']['completed']=None
        self.assertIsNone(self.shared.try_acquire('failed-next',SNAPSHOT,{}))
        self.policy['counts'][self.keys[1]]=20;self.save()
        self.assertIn('target reached',rounds.wait_reason('failed-next',{'interleaving':self.policy}))

    def test_publisher_counts_failures_and_timeouts_but_not_infrastructure(self):
        summary=dict(job='job',updated_at='2026-09-14T00:00:00Z',settings=[],trials=[])
        for task,completed,status in [('done',1,'scored'),('failed',0,'infrastructure'),('unseen',1,'timeout')]:
            summary['settings'].append(dict(task=task,model='sonnet-5',effort='high',target=20,completed=completed))
            summary['trials'].append(dict(task=task,model='sonnet-5',effort='high',status=status,
                trial='jobs/job/'+task+'-old',reward=0))
        path=self.root/'job.summary.json';path.write_text(json.dumps(summary))
        snap=coverage.build_snapshot(self.root,self.policy)
        self.assertEqual(list(snap['completed'].values()),[1,0,1])
        original=json.loads(self.shared.state_path.read_text())
        report=coverage.publish(self.control)
        new=json.loads(self.shared.state_path.read_text())
        self.assertEqual(report['covered'],2)
        self.assertEqual(original['participants'],new['participants'])
        self.assertEqual(original['interleaving']['receipts'],new['interleaving']['receipts'])
        self.assertEqual(original['interleaving']['counts'],new['interleaving']['counts'])
        saved=self.shared.state_path.read_bytes();path.write_text('{')
        with self.assertRaises(ValueError):coverage.publish(self.control)
        self.assertEqual(saved,self.shared.state_path.read_bytes())

    def test_complete_coverage_returns_to_existing_round_rule(self):
        self.policy['first_sweep']['completed']=dict.fromkeys(self.keys,1);self.save()
        self.assertIn('remaining cells',self.shared.try_acquire('failed-next',SNAPSHOT,{}))


if __name__ == '__main__':unittest.main()
