"""Bounded current-release check of unreachable-sink SDK shutdown.
No remote service is contacted: a reserved loopback port is closed before use.
"""
import json, os, pathlib, socket, subprocess, sys, tempfile, time
import rerun as rr
if len(sys.argv)>1:
    import numpy as np
    mode,port,ready=sys.argv[1:]
    stream=rr.RecordingStream('shutdown-audit')
    stream.connect_grpc('rerun+http://127.0.0.1:'+port+'/proxy')
    n=1000000 if mode!='single' else 1
    start=time.monotonic()
    if mode=='columns':
        stream.send_columns('value',indexes=[rr.TimeColumn('step',sequence=np.arange(n))],columns=rr.Scalars.columns(scalars=np.arange(n,dtype=np.float64)))
    else:
        scalar=rr.Scalars(42.)
        for i in range(n):
            stream.set_time('step',sequence=i)
            stream.log('value',scalar)
    pathlib.Path(ready).write_text(json.dumps({'logged':n,'logging_seconds':time.monotonic()-start}))
    del stream
else:
    root=pathlib.Path(tempfile.mkdtemp(prefix='rerun-shutdown-audit-'))
    report={'version':rr.__version__,'cases':{}}
    for mode in ('single','columns','rows'):
        with socket.socket() as s:
            s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        marker=root/(mode+'.ready')
        with (root/(mode+'.stderr')).open('w') as err:
            p=subprocess.Popen([sys.executable,__file__,mode,str(port),str(marker)],stderr=err)
            t=time.monotonic()
            while not marker.exists() and p.poll() is None and time.monotonic()-t<120:
                time.sleep(.05)
            if not marker.exists():
                if p.poll() is None:p.kill()
                p.wait();result={'phase':'logging_not_completed','exitcode':p.returncode}
            else:
                result=json.loads(marker.read_text());t=time.monotonic()
                try:p.wait(timeout=20);result['shutdown']='returned'
                except subprocess.TimeoutExpired:p.kill();p.wait();result['shutdown']='watchdog_20s'
                result['shutdown_seconds']=time.monotonic()-t;result['exitcode']=p.returncode
        result['stderr']=(root/(mode+'.stderr')).read_text()
        report['cases'][mode]=result
        print(json.dumps({'case':mode,**result}),flush=True)
    print(json.dumps(report,indent=2))
