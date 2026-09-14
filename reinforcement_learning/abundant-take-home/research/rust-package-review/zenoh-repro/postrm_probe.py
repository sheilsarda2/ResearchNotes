"""Run only inside a disposable container. Mount source at /zenoh read-only."""
import json, pathlib, subprocess
cfg=pathlib.Path('/etc/zenohd')
state=pathlib.Path('/var/zenohd')
unrelated=pathlib.Path('/tmp/unrelated-audit-marker')
unrelated.write_text('preserve')
rows=[]
for action in ['upgrade','remove','failed-upgrade','abort-install','abort-upgrade','disappear','purge']:
    cfg.mkdir(parents=True,exist_ok=True);state.mkdir(parents=True,exist_ok=True)
    (cfg/'custom-config').write_text('synthetic configuration')
    (state/'persisted-state').write_text('synthetic data')
    before=(cfg/'custom-config').is_file() and (state/'persisted-state').is_file()
    r=subprocess.run(['sh','/zenoh/zenohd/.deb/postrm',action,'1.10.2'],capture_output=True,text=True)
    rows.append(dict(action=action,both_present_before=before,returncode=r.returncode,configuration_exists=cfg.exists(),state_exists=state.exists(),unrelated_preserved=unrelated.read_text()=='preserve'))
print(json.dumps(rows,indent=2))
