import copy
import unittest
import validate_existing_oracle as target

class CallbackGate(unittest.TestCase):
    def fixture(self):
        return {'kind':'single_full_oracle_confirmation','finished_at':'2026-09-14T08:00:00Z','passed':False,
            'inputs_unchanged':True,'tooling_unchanged':True,'decision_unchanged':True,
            'controls':{'oracle':{'passed':False,'exit_code':0,'error_type':'AssertionError','error':'',
                'observation':{'exit_code':0,'terminal_sample_valid':True,'terminal_sample_at':'2026-09-14T07:59:59Z',
                    'errors':[{'at':'2026-09-14T07:25:00Z','error_type':'AssertionError','error':'Private tag already identifies a different image','terminal':False}]}}}}
    def test_only_known_callback_failure_accepted_without_mutation(self):
        x=self.fixture();before=copy.deepcopy(x);row,errors=target.validate_callback_failure(x)
        self.assertEqual(x,before);self.assertEqual(row,x['controls']['oracle']);self.assertEqual(len(errors),1)
    def test_unknown_observation_failure_rejected(self):
        x=self.fixture();x['controls']['oracle']['observation']['errors'][0]['error']='Unknown failure'
        with self.assertRaises(AssertionError):target.validate_callback_failure(x)
    def test_additional_read_failure_is_not_hidden(self):
        x=self.fixture();x['controls']['oracle']['observation']['errors'].append({'error_type':'FileNotFoundError','error':'state missing','terminal':False})
        with self.assertRaises(AssertionError):target.validate_callback_failure(x)
    def test_failed_terminal_sample_rejected(self):
        x=self.fixture();x['controls']['oracle']['observation']['terminal_sample_valid']=False
        with self.assertRaises(AssertionError):target.validate_callback_failure(x)
    def test_process_failure_rejected(self):
        x=self.fixture();x['controls']['oracle']['exit_code']=1
        with self.assertRaises(AssertionError):target.validate_callback_failure(x)
    def test_changed_task_source_rejected(self):
        x=self.fixture();x['inputs_unchanged']=False
        with self.assertRaises(AssertionError):target.validate_callback_failure(x)
    def test_unfinished_control_rejected(self):
        x=self.fixture();x['finished_at']=None
        with self.assertRaises(AssertionError):target.validate_callback_failure(x)
    def test_terminal_callback_error_rejected(self):
        x=self.fixture();x['controls']['oracle']['observation']['errors'][0]['terminal']=True
        with self.assertRaises(AssertionError):target.validate_callback_failure(x)

if __name__=='__main__':unittest.main()
