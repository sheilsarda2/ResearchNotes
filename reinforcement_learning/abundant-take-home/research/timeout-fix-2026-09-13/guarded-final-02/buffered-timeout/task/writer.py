import json, os, pathlib, subprocess, sys, time
root = pathlib.Path('/workspace/deadline-evidence')
root.mkdir(parents=True, exist_ok=True)
logs = pathlib.Path('/logs/agent')
logs.mkdir(parents=True, exist_ok=True)
mode = sys.argv[1]
child = r"""import json, os, pathlib, sys, time
root = pathlib.Path('/workspace/deadline-evidence')
label = sys.argv[1]
root.joinpath(label + '.pid').write_text(str(os.getpid()))
root.joinpath(label + '.ready').write_text(str(time.monotonic_ns()))
started = time.monotonic()
while time.monotonic() - started < 8:
    data = json.dumps({'label': label, 'ns': time.monotonic_ns()})
    root.joinpath(label + '.progress').write_text(data)
    pathlib.Path('/logs/agent/' + label + '-trajectory.json').write_text(data)
    if time.monotonic() - started > 1.6:
        root.joinpath(label + '.late').write_text(data)
    time.sleep(.025)
"""
for label, detached in [('child', False), ('new-session', True)]:
    subprocess.Popen([sys.executable, '-c', child, label],
                     start_new_session=detached, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
daemon = r"""import os, subprocess, sys
if os.fork():
    sys.exit(0)
os.setsid()
if os.fork():
    sys.exit(0)
os.execl(sys.executable, sys.executable, '-c', sys.argv[1], 'daemon')
"""
subprocess.run([sys.executable, '-c', daemon, child],
               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, check=True)
root.joinpath('parent.pid').write_text(str(os.getpid()))
root.joinpath('parent.ready').write_text(str(time.monotonic_ns()))
print('no-model writer started', flush=True)
if mode == 'normal-background':
    time.sleep(.25)
    sys.exit(0)
time.sleep(8)
root.joinpath('parent.late').write_text(str(time.monotonic_ns()))
