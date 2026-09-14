"""Private agent entry point: install the reviewed tool fix, then run Mini's CLI."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import runpy
import sys

PREFIX = 'BENCHMARK_MINI_TOOL_RUNTIME='


def install_checked():
    if platform.python_version() != '3.12.11':
        raise RuntimeError('Unexpected agent Python version')
    helper_path = Path(__file__).with_name('c.py')
    spec = importlib.util.spec_from_file_location('benchmark_mini_tool_cleanup', helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load private tool runtime')
    helper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helper
    spec.loader.exec_module(helper)
    observed = helper.install()
    from minisweagent.environments import local
    if local._run is not helper.bounded_run or observed.get('installed') is not True:
        raise RuntimeError('Tool runner replacement was not installed')
    if observed.get('package_version') != '2.4.6':
        raise RuntimeError('Unexpected mini-swe-agent version')
    digest = hashlib.sha256(helper_path.read_bytes()).hexdigest()
    if observed.get('helper_sha256') != digest:
        raise RuntimeError('Tool runtime source hash mismatch')
    return dict(observed, python=platform.python_version(), executable=sys.executable,
                bootstrap_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def main():
    observed = install_checked()
    print(PREFIX + json.dumps(observed, sort_keys=True), flush=True)
    if sys.argv[1:] == ['--benchmark-preflight']:
        return
    # Running the installed console script preserves its argv and Typer handling.
    # No PYTHONPATH/sitecustomize injection reaches task Python subprocesses.
    entry = Path(sys.prefix) / 'bin' / 'mini-swe-agent'
    if not entry.is_file():
        raise RuntimeError('Installed Mini console entry point is missing')
    runpy.run_path(str(entry), run_name='__main__')


if __name__ == '__main__':
    main()
