import concurrent.futures,datetime,json,pathlib,subprocess,urllib.parse
root=pathlib.Path(__file__).parent/'sources'
requests=[]
for name,repo,ids in [('zenoh','eclipse-zenoh/zenoh',[2614,2767,2204,2516]),('rerun','rerun-io/rerun',[12596,12821])]:
 for label,path,paged in [('head',f'repos/{repo}/commits/main',False),('open-prs',f'repos/{repo}/pulls?state=open&per_page=100',True),('recent-closed-prs',f'repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100',False)]: requests.append((name+'-'+label,path,paged))
 for issue in ids:
  for suffix in ['', '/comments', '/timeline']:
   label=suffix.strip('/') or 'issue'; requests.append((f'{name}-{issue}-{label}',f'repos/{repo}/issues/{issue}{suffix}'+('?per_page=100' if suffix else ''),bool(suffix)))
  requests.append((f'{name}-{issue}-pr-search','search/issues?q='+urllib.parse.quote(f'repo:{repo} is:pr {issue}')+'&per_page=100',False))
 for term in (['reconnect query','queryable info','querier declaration','completeness'] if name=='zenoh' else ['time selection','selected export','truncate chunk']):
  requests.append((name+'-search-'+term.replace(' ','-'),'search/issues?q='+urllib.parse.quote(f'repo:{repo} is:pr {term}')+'&per_page=100',False))
def fetch(row):
 label,path,paged=row; cmd=['gh','api',path]
 if paged:cmd+=['--paginate','--slurp']
 p=subprocess.run(cmd,text=True,capture_output=True)
 obj={'url':'https://api.github.com/'+path,'checked_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 if p.returncode:obj['error']=p.stderr
 else:
  data=json.loads(p.stdout)
  if paged:data=[item for page in data for item in page]
  obj['data']=data
 (root/(label+'.json')).write_text(json.dumps(obj,indent=2)+'\n')
 print(label,('ERROR' if p.returncode else len(data) if isinstance(data,list) else 'ok'),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(fetch,requests))
