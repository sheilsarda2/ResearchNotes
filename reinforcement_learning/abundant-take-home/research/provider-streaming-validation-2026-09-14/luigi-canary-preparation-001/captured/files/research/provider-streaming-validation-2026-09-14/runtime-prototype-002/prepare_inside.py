"""Install only captured offline package bytes into a fresh target venv."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for part in iter(lambda: source.read(1024 * 1024), b''):
            value.update(part)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--proof', type=Path, required=True)
    args = parser.parse_args()
    fixture = Path('/fixture')
    manifest = json.loads((fixture / 'file-manifest.json').read_text())
    subprocess.run(['python3', '-m', 'venv', '--without-pip', '/opt/mini'], check=True)
    target = Path('/opt/mini/lib/python3.12/site-packages')
    seen = set()
    with tarfile.open(fixture / 'site-packages.tar') as archive:
        for item in archive:
            path = PurePosixPath(item.name)
            if not item.isfile() or path.parts[0] != 'site-packages' or '..' in path.parts or path.is_absolute():
                raise RuntimeError('Untrusted package archive member')
            name = path.relative_to('site-packages').as_posix()
            if name in seen or name not in manifest or item.size != manifest[name]['bytes']:
                raise RuntimeError('Package archive identity mismatch')
            seen.add(name)
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(item) as source, dest.open('wb') as output:
                shutil.copyfileobj(source, output)
            if digest(dest) != manifest[name]['sha256']:
                raise RuntimeError('Extracted package hash mismatch')
    if seen != set(manifest):
        raise RuntimeError('Missing package archive files')
    shutil.copyfile(fixture / 'mini-swe-agent', '/opt/mini/bin/mini-swe-agent')
    Path('/opt/mini/bin/mini-swe-agent').chmod(0o755)
    args.proof.write_text(json.dumps(dict(package_files=len(seen), every_package_hash_verified=True,
                                         package_installs=0, downloads=0, console_entry_sha256=digest('/opt/mini/bin/mini-swe-agent')),
                                   indent=2) + '\n')


if __name__ == '__main__':
    main()
