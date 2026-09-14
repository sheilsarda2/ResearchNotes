#!/usr/bin/env python3
"""Copy Harbor validation results (candidates_v2/validation/<task>.json) into each task's provenance.json."""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]; V2 = ROOT/'candidates_v2'
def secs(t):
    from datetime import datetime
    if not t or not t.get('started_at') or not t.get('finished_at'): return None
    f = lambda s: datetime.fromisoformat(s.replace('Z','+00:00'))
    return round((f(t['finished_at'])-f(t['started_at'])).total_seconds())
for vf in sorted((V2/'validation').glob('*.json')):
    tid = vf.stem; task = V2/tid; pf = task/'provenance.json'
    if not pf.exists(): continue
    v = json.loads(vf.read_text()); prov = json.loads(pf.read_text())
    o, n = v.get('oracle', {}), v.get('nop', {})
    prov.setdefault('validation', {}); prov.setdefault('build', {}); prov.setdefault('funnel', {})
    prov['validation'].update(oracle_reward=o.get('reward'), nop_reward=n.get('reward'),
        harbor_job={'oracle': o.get('job'), 'nop': n.get('job')}, harbor_version='0.15.0',
        host='Docker Desktop 29.5.3, aarch64, ~8.3 GB VM', oracle_exception=o.get('exception'), nop_exception=n.get('exception'))
    ot = o.get('timings') or {}
    prov['build'].update(oracle_wall_seconds=o.get('wall_seconds'), env_setup_seconds=secs(ot.get('environment_setup')),
                         verifier_seconds=secs(ot.get('verifier')), nop_wall_seconds=n.get('wall_seconds'))
    stage = 'nop_pass' if (o.get('reward')==1.0 and n.get('reward')==0.0) else ('oracle_fail' if o.get('reward')==0.0 else ('built' if o.get('exception') else prov['funnel'].get('stage','drafted')))
    prov['funnel']['stage'] = stage
    pf.write_text(json.dumps(prov, indent=2) + '\n')
    print(f"{tid:44} oracle={o.get('reward')} nop={n.get('reward')} stage={stage} oracle_wall={o.get('wall_seconds')}s verifier={secs(ot.get('verifier'))}s")
