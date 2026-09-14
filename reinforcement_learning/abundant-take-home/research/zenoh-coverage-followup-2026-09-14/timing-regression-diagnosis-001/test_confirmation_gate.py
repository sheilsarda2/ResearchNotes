"""Pure evidence-binding checks; no Docker or model calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import run_oracle_confirmation as confirmation


class TimingEvidenceGate(unittest.TestCase):
    def fixture(self, root):
        rows = []
        for variant in ('untouched_gold','corrected_reference'):
            for repeat in (1,2,3):
                path = root / f'{variant}-{repeat}.log'
                path.write_text('running 1 test\ntest scouting_delay_regression ... ok\n')
                rows.append({'variant':variant,'repeat':repeat,'log':path.name,
                             'log_sha256':confirmation.digest(path),'test_passed':True,'returncode':0})
        result = {'test_outcomes':rows}
        self.write(root,rows)
        return result

    def write(self, root, rows):
        (root/'comparison.json').write_text(json.dumps({'complete':True,'fixed_repeats_per_source':3,'cases':rows}))

    def test_six_distinct_raw_passes_are_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);result=self.fixture(root)
            self.assertEqual(len(confirmation.validate_timing_cases(result,root)),6)

    def test_duplicate_repeat_cannot_replace_missing_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);result=self.fixture(root)
            result['test_outcomes'][5]=copy.deepcopy(result['test_outcomes'][4])
            self.write(root,result['test_outcomes'])
            with self.assertRaises(AssertionError):confirmation.validate_timing_cases(result,root)

    def test_pass_flag_must_agree_with_hashed_runtime_log(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);result=self.fixture(root);row=result['test_outcomes'][-1]
            path=root/row['log'];path.write_text('running 1 test\ntest scouting_delay_regression ... FAILED\nexpected <400ms\n')
            row['log_sha256']=confirmation.digest(path);self.write(root,result['test_outcomes'])
            with self.assertRaises(AssertionError):confirmation.validate_timing_cases(result,root)

    def test_outer_projection_cannot_disagree_with_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);result=self.fixture(root);result['test_outcomes'][-1]['test_passed']=False
            with self.assertRaises(AssertionError):confirmation.validate_timing_cases(result,root)


if __name__=='__main__':unittest.main()
