"""Independent regression check based on Rerun issue #12828, with two controls."""
import json, pathlib, re, subprocess, tempfile
import rerun as rr
root=pathlib.Path(tempfile.mkdtemp(prefix='rerun-context-audit-'))
result={'version':rr.__version__, 'frames_expected':5, 'cases':{}}
for mode in ('reenter', 'direct', 'single_context'):
    stream=rr.RecordingStream('context-audit-'+mode)
    path=root/(mode+'.rrd')
    stream.save(str(path))
    def log_one(i):
        rr.set_time('frame',sequence=i)
        rr.log('value',rr.Scalars(float(i)))
    if mode=='reenter':
        for i in range(5):
            with stream: log_one(i)
    elif mode=='single_context':
        with stream:
            for i in range(5): log_one(i)
    else:
        for i in range(5):
            stream.set_time('frame',sequence=i)
            stream.log('value',rr.Scalars(float(i)))
    stream.disconnect()
    p=subprocess.run(['rerun','rrd','print','--entity','/value',str(path)],capture_output=True,text=True,check=True)
    rows=[int(m.group(1)) for line in p.stdout.splitlines() if '/value' in line for m in [re.search(r'with (\d+) rows',line)] if m]
    result['cases'][mode]={'rows':sum(rows),'chunks':len(rows),'rrd_print':p.stdout,'stderr':p.stderr}
print(json.dumps(result,indent=2))
assert result['cases']['direct']['rows']==5
assert result['cases']['single_context']['rows']==5
