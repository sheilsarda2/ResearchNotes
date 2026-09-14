"""Private new-process bootstrap above the unchanged Mini tool bootstrap."""
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import platform
import runpy
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PREFIX = 'BENCHMARK_STREAMING_RUNTIME='


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def preflight():
    manifest = json.loads((HERE / 'streaming-manifest.json').read_text())
    if platform.python_version() != '3.12.11':
        raise RuntimeError('Prototype requires the actual agent Python 3.12.11')
    for name, expected in manifest['private_file_sha256'].items():
        if digest(HERE / name) != expected:
            raise RuntimeError('Private runtime source drift')
    for name, version in [('mini-swe-agent', '2.4.6'), ('litellm', '1.100.1')]:
        if importlib.metadata.version(name) != version:
            raise RuntimeError('Agent package version drift')
    packages = json.loads((HERE / 'package-identity.json').read_text())
    site_packages = Path(importlib.util.find_spec('minisweagent').origin).parent.parent
    litellm = Path(importlib.util.find_spec('litellm').origin).parent
    for name, expected in packages['mini_platformdirs_file_sha256'].items():
        if digest(site_packages / name) != expected:
            raise RuntimeError('Installed Mini/platformdirs source drift')
    for name, expected in packages['litellm_file_sha256'].items():
        if digest(litellm / name) != expected:
            raise RuntimeError('Installed LiteLLM source drift')
    from checked_anthropic_iterator import offline_checked_iterator
    with offline_checked_iterator():
        pass
    return dict(installed=True, python=platform.python_version(), mini_version='2.4.6',
                litellm_version='1.100.1', manifest_sha256=digest(HERE / 'streaming-manifest.json'),
                package_identity_sha256=digest(HERE / 'package-identity.json'),
                model_class='checked_streaming_model.CheckedStreamingModel',
                stream=True, complete_response=True, scope='new_agent_process_only')


def main():
    observed = preflight()
    print(PREFIX + json.dumps(observed, sort_keys=True), flush=True)
    if sys.argv[1:] == ['--streaming-preflight']:
        return
    if '--model-class' not in sys.argv or sys.argv[sys.argv.index('--model-class') + 1] != observed['model_class']:
        raise RuntimeError('Explicit streaming model-class selection missing')
    import checked_streaming_model
    checked_streaming_model.EVENT_PATH = '/logs/agent/benchmark-streaming-events.jsonl'
    # This runs its unchanged install_checked() and installed console script.
    # No PYTHONPATH/sitecustomize or installed package edits reach task tools.
    runpy.run_path(str(HERE / 'b.py'), run_name='__main__')


if __name__ == '__main__':
    main()
