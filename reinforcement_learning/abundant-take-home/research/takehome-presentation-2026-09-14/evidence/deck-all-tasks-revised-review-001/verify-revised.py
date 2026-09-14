"""Verify revised deck data and preserved evidence. No model or verifier calls."""
from pathlib import Path
from collections import Counter
from io import BytesIO
import hashlib, json, re, statistics, subprocess, zipfile
import xml.etree.ElementTree as ET

base=Path(__file__).resolve().parents[2]
qa=Path(__file__).resolve().parent
deck=base/'output/takehome-all-completed-tasks-99-revised.pptx'
old=base/'output/takehome-all-completed-tasks-99.pptx'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert sha(deck)=='ff4c43bd4ffbf4dbbab7c805ec794b401590c4218d05df2143552b191dc815ad'
assert sha(old)=='4d108ca979ec8061f18778edda2833dede6e4f5bf33c8bfbbc6c75f14f2b43c0'
assert sha(base/'evidence/deck-all-tasks-review-99-001/build-all-tasks-deck.mjs')=='eb587ce476ee2f676daf014570dfeff8f168ef34f114429a1044fc5c9f1a62d9'
assert sha(qa/'build-all-tasks-deck-revised.mjs')=='b807201f33f43cffc8f40d513cc3ca790a9ff4c0aa8f39b373cd679056a7434f'
data_path=base/'data-snapshots/99-20260914T1319/results.json'
assert sha(data_path)=='e780a2045232d215c26ad0893cf1df818e70a0e6caf9321d026c16d87cff2a9d'
data=json.loads(data_path.read_text())
scope_path=base/'evidence/completed-task-report-scope-001.json'
assert sha(scope_path)=='ff79622495a9c5eac6bdc031676ae07096a2d927e36cd9fdea937ae3ec5b6420'
scope=json.loads(scope_path.read_text())
models=['fable-5-1','opus-5','sonnet-5']; efforts=['medium','high','max']
ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main','c':'http://schemas.openxmlformats.org/drawingml/2006/chart','x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
z=zipfile.ZipFile(deck); oz=zipfile.ZipFile(old)
def xml(z,path):return ET.fromstring(z.read(path))
def rows(z,n):
 return [[''.join(cell.itertext()) for cell in row.findall('a:tc/a:txBody',ns)] for row in xml(z,f'ppt/slides/slide{n}.xml').findall('.//a:tbl/a:tr',ns)]
def text(z,n,notes=False):
 path=f'ppt/notesSlides/notesSlide{n}.xml' if notes else f'ppt/slides/slide{n}.xml'
 return '\n'.join(node.text or '' for node in xml(z,path).findall('.//a:t',ns))
assert len([p for p in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+.xml',p)])==19
first=[r for r in data['trials'] if r['first_counted_result']]
assert len(first)==99
lookup={(r['task'],r['model'],r['effort']):r for r in first}; assert len(lookup)==99
scope_lookup={(t['task'],c['model'],c['effort']):c for t in scope['tasks'] for c in t['cells']}
for key,r in lookup.items():
 for k in ['trial','status','reward','assistant_steps']:assert r[k]==scope_lookup[key][k],(key,k)
coverage=rows(z,4); assert coverage==rows(oz,3)
grid=rows(z,5)[1:]+rows(z,6)[1:]
assert rows(z,5)==rows(oz,4) and rows(z,6)==rows(oz,5)
verified=[];codes=[]
for i,t in enumerate(scope['tasks']):
 task=t['task']; taskrows=[r for r in first if r['task']==task]
 assert coverage[i+1]==[t['label']]+[f"{sum(r['reward']==1 for r in taskrows if r['model']==m)}/3" for m in models]+['9/9']
 for j,(model,effort) in enumerate((m,e) for m in models for e in efforts):
  r=lookup[(task,model,effort)]
  code='V' if r['status']=='verifier_timeout' else 'A' if r['status']=='timeout' else 'P' if r['reward']==1 else 'F'
  service=r['trial'].endswith(('__uuqa2f6','__6ooqjT5'))
  expected=f"{code} ({r['assistant_steps']})"+('*' if service else '')
  assert grid[i][j+1]==expected,(task,model,effort,grid[i][j+1],expected)
  codes.append(code)
  verified.append({'task':task,'model':model,'effort':effort,'trial':r['trial'],'status':r['status'],'reward':r['reward'],'assistant_turns':r['assistant_steps'],'service_marker':service,'displayed':expected})
assert Counter(codes)=={'P':76,'F':19,'A':3,'V':1}
assert sum(r['service_marker'] for r in verified)==2
successful=[r for r in first if r['reward']==1]
assert len(successful)==76 and sum(r['assistant_steps']>75 for r in successful)==64
selected=['rs-rerun-chunk-optimizer','rs-zenoh-timestamp-instrumentation-v3','rs-burn-store-pytorch-reader-v4']
selected_rows=[r for r in first if r['task'] in selected]
assert sum(r['reward']==1 for r in selected_rows if r['model'] in models[:2])==18
assert sum(r['reward']==1 for r in selected_rows if r['model']==models[2])==5
labels={t['task']:t['label'] for t in scope['tasks']}
expected_chart=sorted([(labels[k],statistics.median(r['assistant_steps'] for r in first if r['task']==k and r['reward']==1)) for k in labels],key=lambda v:-v[1])
chart=xml(z,'ppt/slides/charts/chart1.xml')
cats=[v.text for v in chart.findall('.//c:ser/c:cat//c:pt/c:v',ns)]
vals=[float(v.text) for v in chart.findall('.//c:ser/c:val//c:pt/c:v',ns)]
assert list(zip(cats,vals))==expected_chart
old_chart=xml(oz,'ppt/slides/charts/chart1.xml')
assert ET.tostring(chart)==ET.tostring(old_chart)
wbpath='ppt/embeddings/chart-data-snapshot-001.xlsx'
old_w=zipfile.ZipFile(BytesIO(oz.read(wbpath)))
w=zipfile.ZipFile(BytesIO(z.read(wbpath)))
sheet=xml(w,'xl/worksheets/sheet1.xml')
assert w.read('xl/worksheets/sheet1.xml')==old_w.read('xl/worksheets/sheet1.xml')
wbrows=[]
for row in sheet.findall('x:sheetData/x:row',ns)[1:]:
 cells=row.findall('x:c',ns)
 wbrows.append((''.join(cells[0].find('x:is',ns).itertext()),float(cells[1].find('x:v',ns).text)))
assert wbrows==expected_chart
# Check individual language summary pass rates and success medians from raw rows.
summary_rows=rows(z,7)[1:]+rows(z,8)[1:]+rows(z,9)[1:]
for t,row in zip(scope['tasks'],summary_rows):
 taskrows=[r for r in first if r['task']==t['task']]
 successes=[r for r in taskrows if r['reward']==1]
 median=statistics.median(r['assistant_steps'] for r in successes)
 median=str(int(median)) if int(median)==median else str(median)
 assert f"{len(successes)}/9 pass, {median} turns" in row[0],(t['task'],row[0])
# Preserve every old quoted passage, URL, evidence filename, path and SHA in its shifted slide/notes.
quote_count=0;reference_counts=Counter()
patterns={'urls':r'https?://[^\s<>]+','sha256':r'\b[0-9a-f]{64}\b','evidence_filenames':r'[\w.-]+\.(?:json|md|csv|html)\b','evidence_paths':r'(?:research|evidence|candidates_v2)/[^\s;]+?\.(?:json|md)(?:\b|(?=\}))'}
for old_n in range(1,19):
 old_text=text(oz,old_n); new_text=text(z,old_n+1)
 quotes=re.findall('“[^”]+”',old_text)
 for q in quotes:assert q in new_text,(old_n,q)
 quote_count+=len(quotes)
 for notes in [False,True]:
  old_t=text(oz,old_n,notes); new_t=text(z,old_n+1,notes)
  for label,pattern in patterns.items():
   values=re.findall(pattern,old_t)
   for value in values:assert value in new_t,(old_n,label,value)
   reference_counts[label]+=len(values)
assert quote_count==4
all_notes='\n'.join(text(z,n,True) for n in range(1,20))
for bad in ['June5','reports20tasks','than30%','turn138','step140','frozen99']:assert bad not in all_notes,bad
assert 'zero model calls and add zero counted sweep trials' in all_notes
assert 'We have not established exhaustive Send/Route cap coverage' in all_notes
assert 'The repeat used v3, not the corrected v4 verifier.' in all_notes
assert 'We found no evidence of cheating.' in all_notes
assert 'The held SQLite task is outside the 11-task result grid' in text(z,15)
assert 'Counted failures include service interruptions.' in text(z,1)
assert 'On the three samples' in text(z,1)
assert 'We cannot rank models or attribute changes to effort.' in text(z,17)
# Supplied take-home materials must retain original bytes.
cwd=base.parents[1]; repo=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip())
prefix=cwd.relative_to(repo).as_posix()
originals=subprocess.check_output(['git','-C',str(repo),'ls-tree','-r','--name-only','c1ae968','--',prefix],text=True).splitlines()
assert len(originals)==36
assert all((repo/p).read_bytes()==subprocess.check_output(['git','-C',str(repo),'show','c1ae968:'+p]) for p in originals)
(qa/'verified-grid-cells.json').write_text(json.dumps(verified,indent=2)+'\n')
report={'status':'pass','deck_sha256':sha(deck),'slide_count':19,'source_sha256':sha(qa/'build-all-tasks-deck-revised.mjs'),'data_sha256':sha(data_path),'scope_sha256':sha(scope_path),'grid_cells_verified':99,'grid_status_counts':dict(Counter(codes)),'service_markers_verified':2,'coverage_rows_verified':11,'language_summary_rows_verified':11,'native_chart_points_verified':11,'embedded_workbook_points_verified':11,'native_chart_xml_unchanged':True,'embedded_workbook_sheet_xml_unchanged':True,'quote_count_preserved':quote_count,'preserved_reference_occurrences':dict(reference_counts),'requested_note_spacing_corrected':True,'scientific_caveats_checked':True,'original_files_byte_identical':36,'previous_18_slide_deck_and_source_unchanged':True,'new_model_calls':0,'claim_boundary':'Frozen-data and package-text comparison. Does not rerun benchmark trials or establish causal model effects.'}
(qa/'data-and-reference-verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
