"""Reconstruct the reviewer's misplaced JSON as diagnostic evidence only."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re
HERE=Path(__file__).resolve().parent
BASE=HERE.parent
ROOT=BASE.parents[1]
def read(p):return json.loads(p.read_text())
def ref(p):return {'path':str(p.relative_to(ROOT)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
def main():
    assert not (HERE/'diagnosis.json').exists()
    run=BASE/'reviews-final-001';summary=read(run/'summary.json');wrapper=run/'rs-zenoh-timestamp-instrumentation-v4-validation-proof/generated-wrapper'
    paths=list((run/'jobs').glob('*/*/result.json'));assert len(paths)==1
    result_path=paths[0];trial=result_path.parent;raw=read(result_path)
    native_path=trial/'agent/mini-swe-agent.trajectory.json';native=read(native_path)
    report_path=trial.parent/'check_report.json';report=read(report_path)
    assert raw['finished_at'] and raw['exception_info'] is None and raw['verifier_result']['rewards']['reward']==0
    assert report['results'][0]['checks']=={} and 'missing result file: check-result.json' in report['results'][0]['error']
    assert native['info']['exit_status']=='Submitted'
    candidates=[]
    for i,m in enumerate(native['messages']):
        for call in m.get('tool_calls') or []:
            f=call.get('function',{});a=f.get('arguments',{})
            if isinstance(a,str):a=json.loads(a)
            cmd=a.get('command','')
            if cmd.startswith("cat > /app/task/check-result.json << 'EOF'\n"):
                candidates.append((i,call,cmd))
    assert len(candidates)==1
    index,call,cmd=candidates[0]
    start=cmd.index('\n')+1;end=cmd.index('\nEOF',start);body=cmd[start:end]+'\n';checks=json.loads(body)
    criteria=read(wrapper/'tests/criteria.json')
    assert set(checks)==set(criteria) and len(criteria)==11
    assert all(set(c)=={'outcome','explanation'} and c['outcome']=='pass' and isinstance(c['explanation'],str) and c['explanation'].strip() for c in checks.values())
    response=native['messages'][index+1]
    assert response['role']=='tool' and response['tool_call_id']==call['id']
    assert response['extra']['returncode']==0 and 'valid json' in response['extra']['raw_output']
    assert '/app/task/check-result.json' in cmd[end+4:]
    instruction=(wrapper/'instruction.md').read_text()
    assert 'Do not modify any files under /app/task.' in instruction and 'check-result.json in your working directory is your only deliverable.' in instruction
    config=(wrapper/'task.toml').read_text();assert 'source = "/app/check-result.json"' in config and 'workdir = "/app"' in config
    assert not (trial/'artifacts/check-result.json').exists()
    output=HERE/'emitted-review-json.DIAGNOSTIC.json';output.write_text(body)
    transcript=HERE/'output-tool-call.json';transcript.write_text(json.dumps({'native_message_index':index,'call':call,'response':response},indent=2)+'\n')
    diagnosis={'schema_version':1,'kind':'reviewer_output_path_error_diagnostic','created_at':datetime.now(timezone.utc).isoformat(),
      'official_review_valid':False,'official_reward':0,'official_criteria':{},'new_model_calls':0,'new_control_runs':0,
      'raw_trial':str(trial.relative_to(ROOT)),'raw_result':ref(result_path),'official_report':ref(report_path),'native_trajectory':ref(native_path),
      'review_summary':ref(run/'summary.json'),'stopped_continuation':ref(BASE/'continuation-confirmed-002/summary.json'),
      'wrapper_instruction':ref(wrapper/'instruction.md'),'wrapper_config':ref(wrapper/'task.toml'),'wrapper_criteria':ref(wrapper/'tests/criteria.json'),
      'wrapper_validator':ref(wrapper/'tests/validate.py'),'artifact_manifest':ref(trial/'artifacts/manifest.json'),
      'emitted_json':ref(output),'output_tool_call':ref(transcript),'extractor':ref(Path(__file__).resolve()),
      'native_message_index':index,'native_assistant_turns':sum(m.get('role')=='assistant' for m in native['messages']),
      'expected_output_path':'/app/check-result.json','actual_tool_write_path':'/app/task/check-result.json','tool_returncode':0,
      'diagnostic_criterion_count':11,'diagnostic_outcomes':{k:v['outcome'] for k,v in checks.items()},
      'cost_usd':raw['agent_result']['cost_usd'],'agent_exception':None,'native_exit_status':'Submitted',
      'interpretation':'The reviewer emitted eleven pass judgments in a here-document written to the forbidden task-review directory rather than the explicitly configured working directory. The shell reported success and JSON validity, but Harbor collected no result at its required artifact path. These reconstructed bytes are diagnostic only; the official raw zero/error and empty criterion report remain unchanged. This is a reviewer output-placement error, not an observed failed task-quality criterion.'}
    (HERE/'diagnosis.json').write_text(json.dumps(diagnosis,indent=2)+'\n');print(json.dumps(ref(HERE/'diagnosis.json')))
if __name__=='__main__':main()
