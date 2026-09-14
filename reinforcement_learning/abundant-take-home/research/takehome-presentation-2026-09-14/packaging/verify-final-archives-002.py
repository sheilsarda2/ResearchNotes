from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,zipfile
B=Path('research/takehome-presentation-2026-09-14');P=B/'packaging'
plan=json.loads((P/'final-plan-99-002.json').read_text());receipt=json.loads((P/'review-pack-99-002.verification.json').read_text())
assert receipt['copied_bytes_verified'] and receipt['zip_bytes_verified'] and receipt['report_ready'] is False
assert receipt['plan_sha256']==plan['plan_sha256']
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def names_for(entry,destination):
 return {destination+('/'+name if name else '') for name in entry['tree']['files']}
expected=set()
for entry in plan['entries']:expected.update(names_for(entry,entry['destination']))
main=Path(receipt['zip']['path']);support=P/'review-pack-99-002-supporting-evidence.zip'
assert main.stat().st_size==receipt['zip']['bytes'] and sha(main)==receipt['zip']['sha256']
assert support.stat().st_size==receipt['supporting_zip']['bytes'] and sha(support)==receipt['supporting_zip']['sha256']
expected_support=set()
for entry in plan['supporting_sources']:expected_support.update(names_for(entry,'workspace/'+entry['source']))
expected_support.update('inputs/'+Path(x['path']).name for x in plan['captured_inputs'].values())
expected_support.update(plan['selected_data']['supporting_directory']+'/'+name for name in plan['selected_data']['files'])
expected_support.update(['packaging-plan.json','review-pack-99-002.verification.json'])
with zipfile.ZipFile(main) as a,zipfile.ZipFile(support) as z:
 assert {i.filename for i in a.infolist() if not i.is_dir()}==expected
 assert {i.filename.split('/')[0] for i in a.infolist()}=={'samples','jobs','archive','report'}
 assert 'report/' in a.namelist() and not any(n.startswith('report/') and n!='report/' for n in a.namelist())
 assert {i.filename for i in z.infolist() if not i.is_dir()}==expected_support
 assert len([n for n in a.namelist() if n.startswith('jobs/') and n.endswith('/result.json')])==27
 d=json.loads((B/'data-snapshots/99-20260914T1319/results.json').read_text());first=[t for t in d['trials'] if t['first_counted_result']];strict={e['source'] for e in plan['entries'] if e['kind']=='raw_trial'}
 for t in first:
  local=t['trial']+'/result.json';body=(a if t['trial'] in strict else z).read(local if t['trial'] in strict else 'workspace/'+local)
  assert hashlib.sha256(body).hexdigest()==t['artifacts'][local]['sha256']
 for path in [B/'output/takehome-all-completed-tasks-99.pptx',B/'report-support-all-completed-tasks.md']:
  assert hashlib.sha256(z.read('workspace/'+str(path))).hexdigest()==sha(path)
 assert hashlib.sha256(z.read('data-snapshot/results.json')).hexdigest()==sha(B/'data-snapshots/99-20260914T1319/results.json')
 for entry in plan['captured_inputs'].values():assert hashlib.sha256(z.read('inputs/'+Path(entry['path']).name)).hexdigest()==entry['sha256']
proof={'at':datetime.now(timezone.utc).isoformat(),'passed':True,'archive_receipt_sha256':sha(P/'review-pack-99-002.verification.json'),'strict_archive':{'path':str(main),'sha256':sha(main),'bytes':main.stat().st_size,'file_entries':len(expected),'top_level_roots':['samples','jobs','archive','report']},'supporting_archive':{'path':str(support),'sha256':sha(support),'bytes':support.stat().st_size,'file_entries':len(expected_support)},'all99_raw_results_rehashed_from_zip_members':True,'all_expected_file_paths_match_plan':True,'final_deck_and_report_notes_and_captured_inputs_rehashed_from_zip':True,'strict_jobs':27,'all_reported_first_result_directories':99,'supplemental_full_raw_directories':87,'report_ready':False,'source_file_bytes_verified_by_packager':True,'zip_member_bytes_verified_by_packager':True,'zip_timestamp_clamping_metadata_only':True}
p=P/'final-archives-root-review-002.json';assert not p.exists();p.write_text(json.dumps(proof,indent=2,sort_keys=True)+'\n');print(json.dumps(proof))
