"""Run the three authorized, routed Harbor quality reviews sequentially."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
TASKS = ['candidates_v2/rs-rerun-chunk-optimizer',
         'research/task-revisions/rs-burn-store-pytorch-reader-v4',
         'research/task-revisions/rs-zenoh-timestamp-instrumentation-v3']
SHARED = ROOT / 'jobs/candidate-campaigns-shared.control.json'
MODEL = 'anthropic/claude-sonnet-5'


def now(): return datetime.now(timezone.utc).isoformat()
def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def hashes(folder): return {str(p.relative_to(folder)): sha(p) for p in sorted(folder.rglob('*')) if p.is_file()}
def dump(p, value):
    tmp = p.with_suffix('.tmp'); tmp.write_text(json.dumps(value, indent=2) + '\n'); tmp.replace(p)


def observe(path, control, child):
    state = read(SHARED.with_suffix('.state.json')); shared = read(SHARED)
    row = {'at': now(), 'runner_pid': child.pid, 'shared_max_active': shared['max_active'],
           'total_claims': sum(len(p['trials']) for p in state['participants'].values()),
           'own_participants': {k:p for k,p in state['participants'].items() if p['control'] == str(control)}}
    resources = control.with_suffix('.resources.json')
    if resources.exists(): row['local_resources'] = read(resources)
    with path.open('a') as f: f.write(json.dumps(row) + '\n')


def validate(job, proof_dir, observations):
    paths = list(job.glob('*/result.json')); assert len(paths) == 1
    path = paths[0]; result = read(path); report = read(job/'check_report.json')
    proof = read(proof_dir/'runtime-source-proof.json'); wrapper = read(proof_dir/'wrapper-inputs.json')
    assert proof['captured_tree_unchanged'] and proof['original_tasks_unchanged'] and proof['all_local_imports_captured']
    assert result['task_checksum'] == wrapper['wrapper_checksum']
    agent = result['config']['agent']; assert agent['name'] == 'mini-swe-agent' and agent['model_name'] == MODEL
    assert agent['kwargs']['version'] == '2.4.6' and agent['kwargs']['reasoning_effort'] == 'high'
    assert result['exception_info'] is None, result['exception_info']['exception_type'] if result['exception_info'] else None
    assert result['finished_at'] and result['verifier_result']['rewards']['reward'] == 1
    assert len(report['results']) == 1 and report['results'][0]['error'] is None
    for name in ['benchmark-agent-runtime.json','benchmark-agent-tool-runtime.json']:
        preflight = read(path.parent/'agent'/name); assert preflight['preflight_passed'] is True
    deadline = read(path.parent/'benchmark-deadline.json'); assert deadline['quiescent'] is True and not deadline.get('cleanup_error')
    assert deadline['cleanup_finished_at_epoch'] <= datetime.fromisoformat(result['verifier']['started_at'].replace('Z','+00:00')).timestamp()
    rows = [json.loads(line) for line in observations.read_text().splitlines()]
    claims = [sum(len(p['trials']) for p in row['own_participants'].values()) for row in rows]
    assert max(claims) == 1 and claims[-1] == 0
    assert all(row['total_claims'] <= row['shared_max_active'] == 14 for row in rows)
    native = read(path.parent/'agent/mini-swe-agent.trajectory.json')
    atif = read(path.parent/'agent/trajectory.json')
    counts = {'native_assistant_turns': sum(m['role'] == 'assistant' for m in native['messages']),
              'atif_agent_steps': sum(s['source'] == 'agent' for s in atif['steps'])}
    assert counts['native_assistant_turns'] == counts['atif_agent_steps']
    checks = report['results'][0]['checks']
    return {'passed': True, 'model_calls': True, 'task': wrapper['original_task'],
            'original_task_checksum': wrapper['original_task_checksum'],
            'wrapper_checksum': wrapper['wrapper_checksum'], 'result': str(path.relative_to(ROOT)),
            'result_sha256': sha(path), 'checks': checks,
            'criteria_fail': [name for name,row in checks.items() if row['outcome'] == 'fail'],
            'agent_usage': result['agent_result'], 'counts': counts,
            'check_report': str((job/'check_report.json').relative_to(ROOT)),
            'check_report_sha256': sha(job/'check_report.json'),
            'trial_file_sha256': hashes(path.parent),
            'runtime_proof_sha256': sha(proof_dir/'runtime-source-proof.json'),
            'admission_observations_sha256': sha(observations),
            'peak_own_claims': 1, 'final_own_claims': 0, 'shared_cap_respected': True}


def main():
    from dotenv import load_dotenv
    from harbor.models.task.task import Task
    load_dotenv(ROOT / '.env')
    os.environ['ANTHROPIC_API_KEY'] = os.environ['TAKE_HOME_TOKEN']
    assert os.environ['ANTHROPIC_API_KEY'], 'Existing routed credential missing'
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--output-name', default='reviews-002')
    args = parser.parse_args()
    assert '/' not in args.output_name and args.output_name.startswith('reviews-')
    output = BASE / args.output_name; assert not output.exists(); output.mkdir()
    control = output/'control.json'
    dump(control, {'max_active':1, 'min_total_mb':30000, 'reserve_mb':4096,
                  'startup_reserve_mb':1536, 'startup_window_sec':120,
                  'start_interval_sec':5, 'max_memory_pressure_pct':1.0,
                  'pressure_cooldown_sec':60, 'network_pool_cidr':'172.31.0.0/16',
                  'paused':False, 'shared_pool':str(SHARED)})
    inputs = {name: {'task_checksum':Task(ROOT/name).checksum, 'files':hashes(ROOT/name)} for name in TASKS}
    dump(output/'run-identity.json', {'schema_version':1, 'started_at':now(), 'pid':os.getpid(),
         'model_calls':True, 'model':MODEL, 'reasoning_effort':'high', 'mini_version':'2.4.6',
         'inputs':inputs, 'control_sha256':sha(control),
         'harness_sha256':{p.name:sha(p) for p in [Path(__file__),BASE/'execute_quality_check.py']},
         'capture_manifest_sha256':sha(BASE/'immutable-tooling-v1/manifest.json')})
    summary = {'schema_version':1, 'started_at':now(), 'model_calls':True, 'passed':False, 'reviews':{}}
    dump(output/'summary.json',summary)
    env = dict(os.environ,HARBOR_ADMISSION_CONTROL=str(control),PYTHONDONTWRITEBYTECODE='1')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    for name in TASKS:
        task = ROOT/name; label = task.name; job_name = label+'-quality-'+stamp
        proof_dir = output/(label+'-proof'); observations = output/(label+'-admission.jsonl')
        argv = [sys.executable,'-B',str(BASE/'execute_quality_check.py'),'--proof-dir',str(proof_dir),'--',
                'check',str(task),'--agent','mini-swe-agent','--model',MODEL,
                '--ak','version=2.4.6','--ak','reasoning_effort=high',
                '--ae','ANTHROPIC_BASE_URL=https://take-home-automation.vercel.app',
                '--ae','ANTHROPIC_API_BASE=https://take-home-automation.vercel.app',
                '--n-concurrent','1','--n-attempts','1','--jobs-dir',str(output/'jobs'),'--job-name',job_name]
        record = {'task':name, 'command':argv, 'started_at':now(), 'passed':False}
        print(f'{now()} Start authorized Harbor quality review: {label}',flush=True)
        try:
            assert hashes(task) == inputs[name]['files']
            with (output/(label+'.log')).open('x') as log:
                child = subprocess.Popen(argv,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                while child.poll() is None:
                    observe(observations,control,child);time.sleep(1)
                observe(observations,control,child)
            record['exit_code'] = child.returncode
            assert child.returncode == 0, 'Harbor review process returned nonzero'
            record.update(validate(output/'jobs'/job_name,proof_dir,observations))
        except Exception as error:
            record.update(error_type=type(error).__name__, error=str(error))
        record['finished_at']=now();summary['reviews'][label]=record;dump(output/'summary.json',summary)
        print(f'{now()} Review {label}: completed={record["passed"]} failed_criteria={record.get("criteria_fail")}',flush=True)
    summary['inputs_unchanged'] = all(hashes(ROOT/name)==value['files'] for name,value in inputs.items())
    summary['finished_at']=now();summary['passed']=summary['inputs_unchanged'] and all(row['passed'] for row in summary['reviews'].values())
    dump(output/'summary.json',summary)
    if not summary['passed']:raise SystemExit(1)


if __name__ == '__main__':main()
