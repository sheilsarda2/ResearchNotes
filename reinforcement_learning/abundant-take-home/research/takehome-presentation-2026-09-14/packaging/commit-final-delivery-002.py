"""Stage only reviewed take-home delivery roots, then author the requested commit."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re,subprocess
B=Path('research/takehome-presentation-2026-09-14');P=B/'packaging'
proof_path=P/'review-pack-99-002.verification.json'
proof=json.loads(proof_path.read_text())
assert proof['copied_bytes_verified'] and proof['zip_bytes_verified'] and proof['report_ready'] is False
review=json.loads((P/'scoped-commit-review-001.json').read_text());roots=review['roots'];excluded={e['path'] for e in review['excluded']}
repo=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip());prefix=Path.cwd().relative_to(repo).as_posix()
assert not subprocess.check_output(['git','-C',str(repo),'diff','--cached','--name-only','-z']), 'Existing staged files require separate review'
raw=subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z','--',*roots])
files=[]
for value in raw.split(b'\0'):
 if not value:continue
 name=value.decode();parts=Path(name).parts
 if name in excluded:continue
 if any(x.startswith('.chart-data-') or x.startswith('.review-pack-') for x in parts):continue
 if '.build' in parts and Path(name).name not in ['build-deck.mjs','build-all-tasks-deck.mjs']:continue
 if re.search(r'/output/takehome-(?:rust|all-tasks)-integration-\d+\.pptx$',name):continue
 if re.search(r'/packaging/review-pack-[^/]+/',name):continue
 if name.endswith('.zip') or Path(name).name.startswith('scoped-commit-paths-'):continue
 if Path(name).name in ['final-commit-process-002.log','final-commit-result-002.json']:continue
 files.append(name)
files.append('scripts/package-takehome-evidence.py');files=sorted(set(files))
assert str(B/'output/takehome-all-completed-tasks-99.pptx') in files
assert str(B/'.build/build-all-tasks-deck.mjs') in files
assert str(proof_path) in files
assert all(name.startswith(tuple(root+'/' for root in roots)) or name=='scripts/package-takehome-evidence.py' for name in files)
assert all(Path(name).is_file() or Path(name).is_symlink() for name in files)
originals=subprocess.check_output(['git','-C',str(repo),'ls-tree','-r','--name-only','c1ae968','--',prefix],text=True).splitlines();assert len(originals)==36
assert not ({prefix+'/'+name for name in files}&set(originals))
assert all((repo/name).read_bytes()==subprocess.check_output(['git','-C',str(repo),'show','c1ae968:'+name]) for name in originals)
listing=P/'scoped-commit-paths-002.nul';assert not listing.exists();listing.write_bytes(b'\0'.join(name.encode() for name in files)+b'\0')
subprocess.run(['git','add','--pathspec-from-file='+str(listing),'--pathspec-file-nul'],check=True)
cached={name.decode() for name in subprocess.check_output(['git','-C',str(repo),'diff','--cached','--name-only','-z']).split(b'\0') if name}
assert cached=={prefix+'/'+name for name in files},'Staged scope differs from reviewed list'
with (P/'final-commit-process-002.log').open('x') as log:
 result=subprocess.run(['git','commit','-F',str(P/'final-commit-message-002.txt')],stdout=log,stderr=subprocess.STDOUT)
if result.returncode:
 print((P/'final-commit-process-002.log').read_text()[-2500:]);raise SystemExit(result.returncode)
commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
committed={name.decode() for name in subprocess.check_output(['git','-C',str(repo),'diff-tree','--no-commit-id','--name-only','-r','-z',commit]).split(b'\0') if name}
assert committed==cached
assert all((repo/name).read_bytes()==subprocess.check_output(['git','-C',str(repo),'show','c1ae968:'+name]) for name in originals)
receipt={'at':datetime.now(timezone.utc).isoformat(),'commit':commit,'committed_files':len(files),'exact_scoped_index_verified':True,'unrelated_scheduler_control_viewer_changes_unstaged':True,'original36_unchanged':True,'path_list_sha256':hashlib.sha256(listing.read_bytes()).hexdigest(),'package_receipt_sha256':hashlib.sha256(proof_path.read_bytes()).hexdigest(),'prior_credential_review':str(P/'scoped-commit-review-001.json'),'new_source_scope':'Expanded report/deck/data evidence and ZIP timestamp correction; new raw trial directories remain in verified local evidence archives.'}
(P/'final-commit-result-002.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n');print(json.dumps(receipt))
