import json
import pathlib
import re
import time
import subprocess
import xml.etree.ElementTree as ET

logs=pathlib.Path('/logs/verifier')
diagnostics={}
def run(name,args,timeout=240):
    started=time.monotonic()
    try:
        p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout)
        (logs/(name+'.log')).write_text(p.stdout)
        diagnostics[name]={'exit_code':p.returncode,'seconds':time.monotonic()-started}
        print(p.stdout)
        return p.returncode==0,p.stdout
    except Exception as e:
        diagnostics[name]={'error':repr(e)}
        return False,''

try:
    ok,_=run('compile-independent',['g++','-std=c++17','-O1','-I/workspace/repo/include',
        '/tests/check_schema.cpp','-o','/logs/verifier/check-schema'])
    if not ok:raise RuntimeError('independent tests did not compile')
    ok,out=run('independent',['/logs/verifier/check-schema','/tests/cases.json'])
    observed=json.loads(out.splitlines()[-1])
    fixtures=json.loads(pathlib.Path('/tests/cases.json').read_text())
    expected_cases=len(fixtures)
    assert expected_cases > 0 and len({c['name'] for c in fixtures}) == expected_cases
    expected_checks=3*sum(1+len(c['after_reset']) if 'after_reset' in c else 2 for c in fixtures)
    ok=ok and observed=={'cases':expected_cases,'checks':expected_checks,'failed':0}
    diagnostics['independent'].update(observed)
    tests='/opt/pristine-tests/unittest'
    built,_=run('compile-upstream',['g++','-std=c++17','-O1','-pthread','-I/workspace/repo/include',
        '-I'+tests,tests+'/unittest.cpp',tests+'/schematest.cpp','-lgtest','-o','/logs/verifier/upstream-schema'])
    if not built:raise RuntimeError('upstream tests did not compile')
    upstream,_=run('upstream',['/logs/verifier/upstream-schema','--gtest_filter=SchemaValidator.Object_*',
        '--gtest_output=xml:/logs/verifier/upstream.xml'])
    cases=ET.parse(logs/'upstream.xml').findall('.//testcase')
    expected_names=set(re.findall(r'^TEST\(SchemaValidator, (Object_\w+)\)', pathlib.Path(tests+'/schematest.cpp').read_text(),re.M))
    observed_names={c.get('name') for c in cases}
    upstream=upstream and bool(expected_names) and len(cases)==len(expected_names) and observed_names==expected_names and all(c.get('status')=='run' and c.find('failure') is None and c.find('error') is None and c.find('skipped') is None for c in cases)
    diagnostics['upstream']['expected_names']=sorted(expected_names)
    diagnostics['upstream']['observed_names']=sorted(observed_names)
    diagnostics['upstream']['collected']=len(cases)
    diagnostics['reward']=int(ok and upstream)
except Exception as e:
    diagnostics['verifier_error']=repr(e)
    diagnostics['reward']=0
(logs/'diagnostics.json').write_text(json.dumps(diagnostics,indent=2)+'\n')
(logs/'reward.txt').write_text(str(diagnostics['reward'])+'\n')
print(json.dumps(diagnostics))
