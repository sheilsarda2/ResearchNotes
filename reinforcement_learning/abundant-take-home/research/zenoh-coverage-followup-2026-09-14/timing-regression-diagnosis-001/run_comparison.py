"""One fixed six-run comparison, after existing controls stop; no model calls."""
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
TOOLS = BASE / 'immutable-tooling-v1'
POOL = ROOT / 'jobs/candidate-campaigns-shared.control.json'
IMAGE = 'sha256:3dfc1ed548c18a983106072a298160d10110e799d500b5466f878de700fc09b5'


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(root):
    result = {}
    for p in sorted(root.rglob('*')):
        assert not p.is_symlink()
        if p.is_file():
            result[str(p.relative_to(root))] = digest(p)
    return result


def dump(path, obj):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(obj, indent=2) + '\n')
    temp.replace(path)


def capture():
    manifest = read(BASE / 'task-manifest.json')
    assert hashes(ROOT / manifest['task']) == manifest['task_file_sha256']
    trials = list((BASE / 'harbor-controls-final/jobs').glob('*oracle*/*/result.json'))
    assert len(trials) == 1
    result = read(trials[0])
    assert result['exception_info'] is None and result['verifier_result']['rewards']['reward'] == 0
    assert result['task_checksum'] == manifest['harbor_task_checksum']
    source = trials[0].parent / 'artifacts/submission'
    gold = read(BASE / 'mutants/gold-source-files.json')
    corrected = hashes(source)
    assert [n for n in sorted(set(gold) | set(corrected)) if gold.get(n) != corrected.get(n)] == ['zenoh/src/net/runtime/adminspace.rs']
    assert corrected['zenoh/src/net/runtime/adminspace.rs'] == read(BASE / 'reference-correction.json')['new_sha256']
    image = read(BASE / 'harbor-controls-final/verifier-image-provisional.json')
    assert image['verifier_image_id'] == IMAGE and image['final_task_checksum'] == result['task_checksum']
    inspected = read(Path(image['raw_docker_inspection_path']))
    assert digest(Path(image['raw_docker_inspection_path'])) == image['raw_docker_inspection_sha256']
    assert inspected['Image'] == IMAGE and inspected['HostConfig'] == image['observed_host_config']
    tool_manifest = read(TOOLS / 'manifest.json')
    assert hashes(TOOLS / 'files/scripts') == {k.removeprefix('scripts/'):v for k,v in tool_manifest['source_sha256'].items()}
    return {'kind': 'fixed_unchanged_timing_comparison', 'model_calls': 0, 'full_regrade': False,
            'variant_order': ['untouched_gold', 'corrected_reference'], 'repeats': 3,
            'source_path': str(source), 'gold_source_files': gold, 'corrected_source_files': corrected,
            'routing_sha256': digest(HERE / 'routing.rs'), 'task_manifest_sha256': digest(BASE / 'task-manifest.json'),
            'task_checksum': result['task_checksum'], 'failed_oracle_result': str(trials[0].relative_to(ROOT)),
            'failed_oracle_result_sha256': digest(trials[0]), 'image': IMAGE,
            'image_provisional_proof_sha256': digest(BASE / 'harbor-controls-final/verifier-image-provisional.json'),
            'host_config': image['observed_host_config'],
            'tool_manifest_sha256': digest(TOOLS / 'manifest.json'),
            'harness_sha256': {str(p.relative_to(ROOT)):digest(p) for p in
                (Path(__file__), HERE / 'compare_inside.py', BASE / 'paired-regrades/run_paired_regrades.py')},
            'limits': {'cpus':4, 'memory_mb':8192, 'declared_storage_mb':30720,
                       'observed_storage_quota_mb':None, 'network_mode':'none',
                       'case_timeout_sec':3600, 'shared_max_active':14, 'local_max_active':1}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    inputs = capture()
    output = HERE / 'comparison-001'
    assert not output.exists()
    if not args.run:
        print(json.dumps({'check_only': True, 'passed': True, 'launches': 0, 'inputs': inputs}, indent=2))
        return
    with (HERE / 'comparison.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output.mkdir()
        dump(output / 'inputs.json', inputs)
        deadline_wait = time.monotonic() + 21600
        while not read(BASE / 'harbor-controls-final/summary.json').get('finished_at') or not read(BASE / 'continuation-001/summary.json').get('finished_at'):
            assert time.monotonic() < deadline_wait, 'Waiting controls deadline; no process stopped'
            time.sleep(5)
        assert not read(BASE / 'continuation-001/summary.json')['stages'], 'No follow-up stage may overlap comparison'
        assert not (BASE / 'reviews-final-001').exists(), 'Never duplicate a paid quality review'
        nop = list((BASE / 'harbor-controls-final/jobs').glob('*nop*/*/result.json'))
        assert len(nop) == 1 and read(nop[0])['finished_at']
        assert read(nop[0])['exception_info'] is None and read(nop[0])['task_checksum'] == inputs['task_checksum']
        assert capture() == inputs
        local = {'max_active':1, 'min_total_mb':30000, 'reserve_mb':4096, 'startup_reserve_mb':1536,
                 'startup_window_sec':120, 'start_interval_sec':5, 'max_memory_pressure_pct':1.0,
                 'pressure_cooldown_sec':60, 'shared_pool':str(POOL)}
        control = output / 'control.json'
        dump(control, local)
        sys.path.insert(0, str(TOOLS / 'files/scripts'))
        from benchmark_shared_admission import SharedAdmission
        from benchmark_recovery import memory_snapshot
        admission = SharedAdmission(POOL, control)
        name = 'zenoh-routing-comparison-' + uuid.uuid4().hex[:12]
        record = {'started_at':datetime.now(timezone.utc).isoformat(), 'status':'waiting', 'model_calls':0,
                  'container':name, 'admission_key':admission.key, 'lifecycle':[], 'complete':False}
        claimed = False
        deadline = None
        child = None

        def event(label, **fields):
            record['lifecycle'].append({'at':datetime.now(timezone.utc).isoformat(), 'event':label, **fields})
            dump(output / 'result.json', record)

        def cmd(label, command, timeout=60, check=True):
            path = output / (label + '.log')
            with path.open('x') as log:
                run = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                     timeout=min(timeout, max(.1, deadline-time.monotonic())) if deadline else timeout)
            event(label, returncode=run.returncode, log=path.name, sha256=digest(path))
            if check:
                assert run.returncode == 0, label
            return run.returncode, path

        def host_sample():
            row = {'at':datetime.now(timezone.utc).isoformat(), 'memory_snapshot':memory_snapshot()}
            row['proc'] = {n:Path('/proc/' + n).read_text() for n in ('stat','loadavg','pressure/cpu','pressure/memory','pressure/io')}
            with (output / 'host-samples.jsonl').open('a') as log:
                log.write(json.dumps(row) + '\n')

        event('registered')
        try:
            while True:
                assert time.monotonic() < deadline_wait
                with admission.locked() as (config,state):
                    assert config['max_active'] == 14
                reason = admission.try_acquire(name, memory_snapshot(), local)
                if reason is None:
                    claimed = True
                    deadline = time.monotonic()+3600
                    with admission.locked() as (config,state):
                        total = sum(len(p['trials']) for p in state['participants'].values())
                        assert total <= 14 and len(state['participants'][admission.key]['trials']) == 1
                    event('claimed', shared_claims=total, own_claims=1)
                    break
                time.sleep(2)
            assert capture() == inputs
            host_sample()
            cmd('create', ['docker','create','--name',name,'--cpus','4','--memory','8192m',
                '--memory-swap',str(inputs['host_config']['MemorySwap']),'--network','none',
                '--entrypoint','sleep',IMAGE,'infinity'])
            cmd('start', ['docker','start',name])
            _, inspection = cmd('inspect', ['docker','inspect','--format',
                '{"Image":{{json .Image}},"HostConfig":{{json .HostConfig}}}',name])
            observed = read(inspection)
            assert observed['Image'] == IMAGE
            assert {k:observed['HostConfig'].get(k) for k in inputs['host_config']} == inputs['host_config']
            cmd('copy-source', ['docker','cp',inputs['source_path'],name+':/tmp/corrected-source'])
            cmd('copy-driver', ['docker','cp',str(HERE/'compare_inside.py'),name+':/tmp/timing-driver.py'])
            cmd('copy-inputs', ['docker','cp',str(output/'inputs.json'),name+':/tmp/timing-inputs.json'])
            with (output/'driver.log').open('x') as log:
                child = subprocess.Popen(['docker','exec',name,'python3','-B','/tmp/timing-driver.py'], stdout=log, stderr=subprocess.STDOUT)
                while child.poll() is None and time.monotonic() < deadline:
                    host_sample()
                    time.sleep(1)
                if child.poll() is None:
                    child.kill()  # Only this diagnostic Docker exec client; container is removed below.
                code = child.wait()
            event('driver-finished', returncode=code)
            assert code == 0
            record['status'] = 'workload_finished'
        except BaseException as error:
            record.update(status='error',error_type=type(error).__name__,error=str(error))
        finally:
            deadline = time.monotonic()+120
            cleanup_errors = []
            try:
                with admission.locked() as (_,state):
                    claimed = claimed or name in state['participants'].get(admission.key,{}).get('trials',{})
            except BaseException as error:
                cleanup_errors.append('ownership_lookup:'+type(error).__name__)
                record['claim_ownership_unknown'] = True
                claimed = True  # Conservatively prove absence before attempting release of our unique name.
            def cleanup(label, command):
                try:
                    code, path = cmd(label, command, check=False)
                    if code != 0:
                        cleanup_errors.append(label+':'+str(code))
                    return code, path
                except BaseException as error:
                    cleanup_errors.append(label+':'+type(error).__name__)
                    return 125, None
            if claimed:
                try:
                    cleanup('stop', ['docker','stop','-t','2',name])
                    code, stopped = cleanup('stopped', ['docker','inspect','--format','{{.State.Running}}',name])
                    if code == 0 and stopped is not None and stopped.read_text().strip() == 'false':
                        cleanup('collect', ['docker','cp',name+':/tmp/timing-output',str(output/'case')])
                finally:
                    cleanup('remove', ['docker','rm','-f',name])
                    code, absent = cleanup('absence', ['docker','ps','-aq','--filter','name=^/'+name+'$'])
                    record['container_absent'] = code == 0 and absent is not None and not absent.read_text().strip()
                    if record['container_absent']:
                        try:
                            admission.release(name)
                            with admission.locked() as (_,state):
                                record['claim_absent'] = name not in state['participants'].get(admission.key,{}).get('trials',{})
                            event('released')
                        except BaseException as error:
                            cleanup_errors.append('release:'+type(error).__name__)
            if child is not None:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()  # Only our already-stopped diagnostic exec client.
                    child.wait(timeout=10)
            record['cleanup_errors'] = cleanup_errors
            try:
                record['inputs_unchanged'] = capture() == inputs
                if record['status'] == 'workload_finished':
                    comparison = read(output/'case/comparison.json')
                    record['complete'] = comparison['complete'] and len(comparison['cases']) == 6 and record.get('container_absent') and record.get('claim_absent') and record['inputs_unchanged'] and not cleanup_errors
                    record['test_outcomes'] = comparison['cases']
            except BaseException as error:
                record.update(validation_error_type=type(error).__name__,validation_error=str(error))
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
            dump(output/'result.json',record)
        if not record['complete']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
