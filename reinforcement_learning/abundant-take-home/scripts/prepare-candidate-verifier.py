#!/usr/bin/env python3
"""Build a separate-verifier definition for one authored candidate (no model calls)."""
import argparse
from pathlib import Path
import shutil
import tomllib

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('task', type=Path)
parser.add_argument('--package', required=True)
args = parser.parse_args()
task = args.task.resolve()
config = task/'task.toml'
data = tomllib.loads(config.read_text())
if data.get('verifier', {}).get('environment'):
    raise SystemExit('Verifier already configured; review existing files instead of replacing them.')
if '/' in args.package or args.package in ('.', '..'):
    raise SystemExit('Package must be one directory directly inside /workspace/repo')
tests = task/'tests'
source = tests/'image-source'
source.mkdir(exist_ok=True)
for name in ('upstream.tar.gz', 'requirements.txt'):
    shutil.copyfile(task/'environment'/name, source/name)
dockerfile = (task/'environment/Dockerfile').read_text()
dockerfile = dockerfile.replace('COPY requirements.txt ', 'COPY image-source/requirements.txt ')
dockerfile = dockerfile.replace('ADD upstream.tar.gz ', 'ADD image-source/upstream.tar.gz ')
dockerfile = dockerfile.replace('COPY upstream.tar.gz ', 'COPY image-source/upstream.tar.gz ')
if args.package == 'huey':
    dockerfile += '\nRUN cp -a /workspace/repo/huey/tests /opt/pristine-huey-tests\n'
    entrypoint = tests/'test.sh'
    script = entrypoint.read_text()
    if '/opt/pristine-huey-tests' not in script:
        script = script.replace('cd /workspace/repo',
            'cd /workspace/repo\n# Restore regressions nested inside the submitted runtime package.\n'
            'rm -rf huey/tests\ncp -a /opt/pristine-huey-tests huey/tests || exit 1', 1)
        entrypoint.write_text(script)
dockerfile += '\nCOPY . /tests\n'
(tests/'Dockerfile').write_text(dockerfile)
text = config.read_text()
# Phase switching is unsupported by Harbor 0.15's Docker provider. The separate
# verifier's baseline supplies offline isolation without such a transition.
lines = text.splitlines()
section = ''
kept = []
for line in lines:
    if line.startswith('['):
        section = line.strip()
    if section == '[verifier]' and line.strip().startswith(('network_mode', 'allowed_hosts')):
        continue
    kept.append(line)
text = '\n'.join(kept) + '\n'
text += '''
[verifier.environment]
network_mode = "no-network"
build_timeout_sec = 900
cpus = 2
memory_mb = 4096
storage_mb = 10240

[[artifacts]]
source = "/workspace/repo/PACKAGE"
destination = "submission/PACKAGE"
exclude = ["__pycache__", "*.pyc"]
'''.replace('PACKAGE', args.package)
config.write_text(text)
with (task/'instruction.md').open('a') as out:
    out.write('\nEvaluation uses a fresh offline container with the pinned dependencies. '
              'Only `/workspace/repo/' + args.package + '/` is transferred as the submitted implementation. '
              'Keep runtime feature code inside that package; upstream tests and documentation '
              'may also be edited for development but do not replace the independent verifier.\n')
print(task.name + ': separate verifier prepared')
