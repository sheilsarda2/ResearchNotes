#!/usr/bin/env python3
"""Summarize candidates_v2 funnel: per-task stage from funnel.jsonl + provenance, counts per stage, and a table."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / 'candidates_v2'
ORDER = ['mined', 'drafted', 'built', 'oracle_fail', 'oracle_pass', 'nop_pass', 'derivability_done', 'rollout_gated', 'accepted', 'rejected']

def main():
    latest = {}
    for line in (V2 / 'funnel.jsonl').read_text().splitlines() if (V2 / 'funnel.jsonl').exists() else []:
        line = line.strip()
        if not line: continue
        try: rec = json.loads(line)
        except json.JSONDecodeError: continue
        latest[rec['task_id']] = rec
    rows = []
    for d in sorted(p for p in V2.iterdir() if p.is_dir() and p.name not in ('mining', 'rejected', 'validation')):
        prov = {}
        if (d / 'provenance.json').exists():
            try: prov = json.loads((d / 'provenance.json').read_text())
            except json.JSONDecodeError: prov = {'error': 'invalid provenance.json'}
        rec = latest.get(d.name, {})
        stage = rec.get('stage') or (prov.get('funnel') or {}).get('stage') or 'drafting'
        rows.append(dict(task_id=d.name, language=prov.get('language'), form=prov.get('form'),
                         stage=stage, upstream=(prov.get('upstream') or {}).get('repo'),
                         feature=(prov.get('feature') or {}).get('ref'),
                         gold=(prov.get('feature') or {}).get('gold_patch'),
                         oracle=(prov.get('oracle') or {}).get('type'),
                         tier=(prov.get('contamination') or {}).get('tier'),
                         build_min=(prov.get('build') or {}).get('build_minutes'),
                         oracle_reward=(prov.get('validation') or {}).get('oracle_reward'),
                         nop_reward=(prov.get('validation') or {}).get('nop_reward'),
                         has=[n for n in ('instruction.md','task.toml','environment/Dockerfile','environment/upstream.tar.gz','tests/test.sh','tests/Dockerfile','solution/changes.patch','provenance.json','STATUS.md') if (d / n).exists()]))
    mined = sum(json.loads(p.read_text()).get('candidates', 0) for p in (V2 / 'mining').glob('*.json'))
    counts = {'mined_feature_scale_prs': mined}
    for s in ORDER[1:]:
        counts[s] = sum(1 for r in rows if r['stage'] == s)
    out = dict(counts=counts, tasks=rows)
    (V2 / 'funnel.json').write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps(counts, indent=1))
    for r in rows:
        g = r['gold'] or {}
        print(f"{r['task_id']:36} {str(r['language']):6} {str(r['form']):2} {r['stage']:16} files={len(r['has'])}/9 gold=+{g.get('additions','?')}/{g.get('files','?')}f oracle={r['oracle']} tier={r['tier']} oracle_r={r['oracle_reward']} nop_r={r['nop_reward']}")
if __name__ == '__main__':
    main()
