"""Freeze expanded all-eleven report support; never modifies tasks or raw trials."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,subprocess,unicodedata
B=Path('research/takehome-presentation-2026-09-14');P=B/'packaging'
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
assert sha(P/'final-support-selection-008.json')=='9f098f167303a8393eabac3550c3b5f9e9900b05dcabd8fcfca23f8df0636b09'
assert sha('scripts/package-takehome-evidence.py')=='69abd6de1e2c0fd3f05a809f24908cdd134d533abc20bcff43ba574f9984f1d3'
old=json.loads((P/'final-support-selection-008.json').read_text())
data=json.loads((B/'data-snapshots/99-20260914T1319/results.json').read_text())
first=[t for t in data['trials'] if t['first_counted_result']]
shortlist={'rs-rerun-chunk-optimizer','rs-zenoh-timestamp-instrumentation-v3','rs-burn-store-pytorch-reader-v4'}
strict={t['trial'] for t in first if t['task'] in shortlist}
extra_first={t['trial'] for t in first if t['trial'] not in strict}
assert len(first)==99 and len(strict)==27 and len(extra_first)==72
additions=extra_first|{str(B/'.build/build-all-tasks-deck.mjs'),str(B/'output/takehome-all-completed-tasks-99.pptx'),str(B/'report-support-all-completed-tasks.md')}
auto=set(old['automatic_required_sources_actual_minimized'])
explicit=set(old['explicit_sources'])|additions
explicit={p for p in explicit if not any(p!=q and Path(p).is_relative_to(q) for q in explicit|auto)}
combined=sorted(explicit|auto)
assert len(combined)==len(explicit)+len(auto)
normalized=[unicodedata.normalize('NFC',s).casefold() for s in combined]
for i,x in enumerate(normalized):
 for y in normalized[i+1:]:assert x!=y and not x.startswith(y+'/') and not y.startswith(x+'/')
assert all(Path(s).exists() for s in combined)
for required in ['completed-task-report-scope-001.json','completed-task-profiles-001.json','deck-all-tasks-review-99-001/review.json','deck-all-tasks-root-review-99-001.json']:
 assert (B/'evidence'/required).exists(),required
supplemental={r['source'] for r in old['raw_roots']}|extra_first
assert len(supplemental)==87 and supplemental<=explicit|auto and not supplemental&strict
argv=['python3','-B','scripts/package-takehome-evidence.py','--plan-only','--data-dir',str(B/'data-snapshots/99-20260914T1319'),'--final-revision-mapping','research/zenoh-coverage-followup-2026-09-14/final-revision-mapping-001.json','--plan',str(P/'final-plan-99-002.json')]
for source in sorted(explicit):argv+=['--supporting-source',source]
selection={'schema_version':1,'kind':'all_completed_task_support_selection','created_at':datetime.now(timezone.utc).isoformat(),'basis':{'path':str(P/'final-support-selection-008.json'),'sha256':sha(P/'final-support-selection-008.json')},'packager_sha256':sha('scripts/package-takehome-evidence.py'),'data_sha256':sha(B/'data-snapshots/99-20260914T1319/results.json'),'report_scope_sha256':sha(B/'evidence/completed-task-report-scope-001.json'),'explicit_sources':sorted(explicit),'automatic_sources':sorted(auto),'supporting_source_count':len(combined),'first_result_trials':99,'strict_first_result_jobs':sorted(strict),'supplemental_full_raw_roots':sorted(supplemental),'supplemental_first_result_trials':sorted(extra_first),'supplemental_counted_repeats':13,'supplemental_operational_incidents':2,'three_samples_unchanged':True,'all11_tasks_reported':True,'added_sources':sorted(explicit-set(old['explicit_sources'])),'removed_redundant_descendants':sorted(set(old['explicit_sources'])-explicit),'source_paths_present_disjoint':True,'source_freeze_effective':True,'source_freeze_excludes_packaging_outputs':True,'ready_plan_argv':argv,'assembly_argv':['python3','-B','scripts/package-takehome-evidence.py','--plan',str(P/'final-plan-99-002.json'),'--output',str(P/'review-pack-99-002')]}
path=P/'final-support-selection-009.json';assert not path.exists();path.write_text(json.dumps(selection,indent=2,sort_keys=True)+'\n')
assert not (P/'final-plan-99-002.json').exists() and not (P/'final-plan-99-002.inputs').exists()
print(json.dumps({'selection':str(path),'sha256':sha(path),'explicit':len(explicit),'auto':len(auto),'source_count':len(combined),'supplemental_full_raw_roots':87}),flush=True)
with (P/'final-plan-99-002.log').open('x') as log:
 result=subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT)
print(json.dumps({'plan_exit':result.returncode}),flush=True)
if result.returncode: print((P/'final-plan-99-002.log').read_text()[-2500:])
raise SystemExit(result.returncode)
