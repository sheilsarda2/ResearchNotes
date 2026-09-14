"""Check the two-slide executive-summary reorder against the preserved deck."""
from pathlib import Path
from io import BytesIO
from collections import Counter
import hashlib,json,re,subprocess,zipfile
import xml.etree.ElementTree as E
base=Path(__file__).resolve().parents[2];qa=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
old=base/'output/takehome-all-completed-tasks-99-revised.pptx'
new=base/'output/takehome-all-completed-tasks-99-exec-summary.pptx'
assert sha(old)=='ff4c43bd4ffbf4dbbab7c805ec794b401590c4218d05df2143552b191dc815ad'
assert sha(new)=='f9999fd8e776293e3aa7b5b837baf8b176b89c586b5e715715c31a24386acda5'
assert sha(base/'.build/build-all-tasks-deck-revised.mjs')=='b807201f33f43cffc8f40d513cc3ca790a9ff4c0aa8f39b373cd679056a7434f'
assert sha(qa/'build-all-tasks-deck-exec-summary.mjs')==sha(base/'.build/build-all-tasks-deck-exec-summary.mjs')
ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main','c':'http://schemas.openxmlformats.org/drawingml/2006/chart','p':'http://schemas.openxmlformats.org/presentationml/2006/main'}
a=zipfile.ZipFile(old);b=zipfile.ZipFile(new)
xml=lambda z,p:E.fromstring(z.read(p))
texts=lambda root:[n.text or '' for n in root.findall('.//a:t',ns)]
order=[1,11,2,3,4,5,6,7,8,9,10,12,13,14,15,16,17,18,19]
assert len([p for p in b.namelist() if re.fullmatch(r'ppt/slides/slide\d+.xml',p)])==19
mapping=[];table_count=0
for new_n,old_n in enumerate(order,1):
 x=xml(a,f'ppt/slides/slide{old_n}.xml');y=xml(b,f'ppt/slides/slide{new_n}.xml')
 xt=texts(x);yt=texts(y)
 foot=next(i for i,t in enumerate(xt) if t.startswith('Snapshot 2026-09-14 13:19 UTC.'))
 assert xt[foot+1]==str(old_n)
 xt[foot+1]=str(new_n)
 if old_n==11:
  assert xt[0]=='Recommended Harbor samples'
  xt[0]='Executive summary: task selection'
 assert xt==yt,(old_n,new_n)
 assert texts(xml(a,f'ppt/notesSlides/notesSlide{old_n}.xml'))==texts(xml(b,f'ppt/notesSlides/notesSlide{new_n}.xml')),(old_n,new_n,'notes')
 tx=x.findall('.//a:tbl',ns);ty=y.findall('.//a:tbl',ns)
 assert len(tx)==len(ty)
 assert [E.tostring(t) for t in tx]==[E.tostring(t) for t in ty],(old_n,new_n,'native table')
 table_count+=len(tx)
 # Native shape geometry and shape text formatting do not change.
 for path in ['.//a:xfrm','.//p:xfrm','.//a:rPr','.//a:pPr','.//a:bodyPr']:
  assert [E.tostring(t) for t in x.findall(path,ns)]==[E.tostring(t) for t in y.findall(path,ns)],(old_n,new_n,path)
 mapping.append({'new_slide':new_n,'old_slide':old_n,'title':yt[0],'only_permitted_text_changes':(['title','slide_number'] if old_n==11 else ['slide_number'] if old_n!=new_n else []),'all_notes_preserved':True})
assert table_count==10
chart_path='ppt/slides/charts/chart1.xml'
assert a.read(chart_path)==b.read(chart_path)
wa=zipfile.ZipFile(BytesIO(a.read('ppt/embeddings/chart-data-snapshot-001.xlsx')))
wb=zipfile.ZipFile(BytesIO(b.read('ppt/embeddings/chart-data-snapshot-001.xlsx')))
assert wa.read('xl/worksheets/sheet1.xml')==wb.read('xl/worksheets/sheet1.xml')
def rows(n):
 return [[''.join(cell.itertext()) for cell in row.findall('a:tc/a:txBody',ns)] for row in xml(b,f'ppt/slides/slide{n}.xml').findall('.//a:tbl/a:tr',ns)]
data_path=base/'data-snapshots/99-20260914T1319/results.json'
assert sha(data_path)=='e780a2045232d215c26ad0893cf1df818e70a0e6caf9321d026c16d87cff2a9d'
data=json.loads(data_path.read_text());first=[r for r in data['trials'] if r['first_counted_result']]
lookup={(r['task'],r['model'],r['effort']):r for r in first};assert len(lookup)==99
scope=json.loads((base/'evidence/completed-task-report-scope-001.json').read_text())
grid=rows(6)[1:]+rows(7)[1:];codes=[]
for i,t in enumerate(scope['tasks']):
 for j,(m,e) in enumerate((m,e) for m in ['fable-5-1','opus-5','sonnet-5'] for e in ['medium','high','max']):
  r=lookup[(t['task'],m,e)]
  code='V' if r['status']=='verifier_timeout' else 'A' if r['status']=='timeout' else 'P' if r['reward']==1 else 'F'
  value=f"{code} ({r['assistant_steps']})"+('*' if r['trial'].endswith(('__uuqa2f6','__6ooqjT5')) else '')
  assert grid[i][j+1]==value
  codes.append(code)
assert Counter(codes)=={'P':76,'F':19,'A':3,'V':1}
# Preserve all evidence from the immediately preceding editorial revision.
prior=base/'evidence/deck-all-tasks-revised-review-001/review.json'
assert sha(prior)=='5828a4bde833d2ddbb4e2297b16e12a05d067e9aed61b6130d2e849cf70ba1de'
pr=json.loads(prior.read_text())
assert all(sha(base/f['path'])==f['sha256'] for f in pr['evidence_files'])
cwd=base.parents[1];repo=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip())
prefix=cwd.relative_to(repo).as_posix()
originals=subprocess.check_output(['git','-C',str(repo),'ls-tree','-r','--name-only','c1ae968','--',prefix],text=True).splitlines()
assert len(originals)==36
assert all((repo/p).read_bytes()==subprocess.check_output(['git','-C',str(repo),'show','c1ae968:'+p]) for p in originals)
result={'status':'pass','previous_deck_sha256':sha(old),'new_deck_sha256':sha(new),'data_sha256':sha(data_path),'new_source_sha256':sha(qa/'build-all-tasks-deck-exec-summary.mjs'),'order':order,'mapping':mapping,'all_slide_text_preserved_except_title_and_slide_numbers':True,'all_19_notes_preserved':True,'all_native_table_xml_preserved':True,'native_table_count':table_count,'chart_xml_preserved':True,'embedded_worksheet_xml_preserved':True,'shape_geometry_and_text_formatting_preserved':True,'raw_grid_cells_verified':99,'grid_status_counts':dict(Counter(codes)),'previous_editorial_evidence_files_preserved':len(pr['evidence_files']),'original_files_preserved':36,'no_new_trials_or_provider_calls':True}
(qa/'reorder-verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='mapping'}))
