import json, os, pathlib, subprocess, sys, time
root = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
if len(sys.argv) > 3:
    root.joinpath('holder.json').write_text(json.dumps({
        'pid': os.getpid(), 'start_ticks': pathlib.Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
        'pgrp': os.getpgrp(), 'session': os.getsid(0)}))
    if mode == 'partial-utf8':
        os.write(1, b'partial-' + bytes([0xe2, 0x82]))
    elif mode not in ('quiet', 'normal-background'):
        print('holder-ready-' + chr(9731), flush=True)
        time.sleep(.55)
        print('late-output', flush=True)
    time.sleep(120)
else:
    options = {} if mode == 'same-group' else {'process_group': 0}
    if mode == 'normal-background':
        options.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.Popen([sys.executable, '-u', __file__, str(root), mode, 'holder'], **options)
    until = time.monotonic() + 2
    while not root.joinpath('holder.json').exists() and time.monotonic() < until:
        time.sleep(.005)
    if mode not in ('quiet', 'partial-utf8'):
        print('parent-ready', flush=True)
    if mode != 'normal-background':
        time.sleep(120)
