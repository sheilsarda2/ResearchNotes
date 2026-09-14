"""Read-only audit of completed review trials; write separate derived evidence."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
CAPTURE = BASE/'immutable-tooling-v1/files/scripts'
sys.path.insert(0,str(CAPTURE))
from benchmark_evidence import collect_evidence, hash_file


def main():
    run = BASE/'reviews-002'; summary=json.loads((run/'summary.json').read_text())
    out=BASE/'completed-review-audits';out.mkdir(exist_ok=True)
    for name,row in summary['reviews'].items():
        if not row.get('passed'):continue
        target=out/(name+'.json')
        if target.exists():continue
        trial=(ROOT/row['result']).parent
        snapshot=json.loads((trial/'benchmark-snapshot.json').read_text())
        evidence=collect_evidence(trial, expected_snapshot=snapshot)
        assert evidence['counts']['turn_counts_match']
        assert evidence['usage']['native_final']['api_calls']==evidence['counts']['native_assistant_turns']
        assert evidence['usage']['usage_censored'] is False
        assert evidence['usage']['provider_charge_may_be_missing'] is False
        assert evidence['completeness']['pre_verifier_inputs_unchanged']
        report=ROOT/row['check_report'];raw=json.loads(report.read_text())
        checks=raw['results'][0]['checks'];assert len(checks)==11
        caveats=[]
        if name=='rs-rerun-chunk-optimizer':
            caveats.append({'criterion':'anti_cheating_measures','type':'reviewer_explanation_overstatement',
              'reviewer_claim':'The environment is offline (no-network) for both agent build and verifier',
              'assessment':'Only the verifier is explicitly configured no-network. Cargo offline is a dependency-resolution setting, not whole-agent network isolation. Do not repeat this explanation as a proven network isolation property.',
              'source':'candidates_v2/rs-rerun-chunk-optimizer/task.toml',
              'source_sha256':hash_file(ROOT/'candidates_v2/rs-rerun-chunk-optimizer/task.toml'),
              'changes_required_to_task':False})
        artifact={'schema_version':1,'checked_at':datetime.now(timezone.utc).isoformat(),
                  'task':row['task'],'original_task_checksum':row['original_task_checksum'],
                  'check_report':row['check_report'],'check_report_sha256':hash_file(report),
                  'valid_review_report':True,'rubric_pass':sum(v['outcome']=='pass' for v in checks.values()),
                  'rubric_fail':[k for k,v in checks.items() if v['outcome']=='fail'],
                  'rubric_not_applicable':[k for k,v in checks.items() if v['outcome']=='not_applicable'],
                  'reviewer_explanation_caveats':caveats,'evidence':evidence,
                  'audit_source_sha256':hash_file(Path(__file__)),
                  'evidence_collector_sha256':hash_file(CAPTURE/'benchmark_evidence.py'),
                  'raw_trial_files_unchanged':True}
        assert all(hash_file(trial/name)==expected for name,expected in row['trial_file_sha256'].items())
        target.write_text(json.dumps(artifact,indent=2)+'\n')
        print(json.dumps({'path':str(target.relative_to(ROOT)),'sha256':hash_file(target),'criteria':len(checks),'counts':evidence['counts'],'cost':evidence['usage']['result']['cost_usd']}))


if __name__=='__main__':main()
