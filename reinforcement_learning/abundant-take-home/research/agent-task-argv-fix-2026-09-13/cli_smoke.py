"""Exercise mini 2.4.6's real CLI/config parser with offline factory stubs.

Fetches checksum-verified published wheels into a temporary directory. Only
model/environment/agent construction is stubbed; no model client is created.
"""
from pathlib import Path
import hashlib
import io
import json
import os
import shlex
import sys
import tempfile
import urllib.request
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import benchmark_agent_runtime as runtime


def wheel(name, version):
    metadata = json.load(urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json'))
    entry = next(row for row in metadata['urls'] if row['filename'].endswith('.whl'))
    data = urllib.request.urlopen(entry['url']).read()
    assert hashlib.sha256(data).hexdigest() == entry['digests']['sha256']
    return data


def main():
    package_bytes = wheel('mini-swe-agent', '2.4.6')
    with tempfile.TemporaryDirectory(prefix='benchmark-mini-cli-') as temporary:
        root = Path(temporary)
        package = root / 'package'
        zipfile.ZipFile(io.BytesIO(package_bytes)).extractall(package)
        for name, version in [('platformdirs', '4.4.0'), ('prompt_toolkit', '3.0.52'), ('wcwidth', '0.2.13')]:
            zipfile.ZipFile(io.BytesIO(wheel(name, version))).extractall(package)
        os.environ['MSWEA_GLOBAL_CONFIG_DIR'] = str(root / 'isolated-global-config')
        os.environ['MSWEA_CONFIGURED'] = 'true'
        sys.path.insert(0, str(package))
        from minisweagent.run import mini
        from typer.testing import CliRunner

        task = "Exact 🦀 task; zenohd advanced_pub_sub\r\nDon't expand $HOME or `id`.\n"
        with tempfile.TemporaryDirectory(prefix='benchmark-agent-input-', dir='/tmp') as config_dir:
            config = Path(config_dir) / 'task.yaml'
            custom = root / 'custom.yaml'
            custom.write_text(json.dumps({'run': {'task': 'wrong earlier task'},
                                          'model': {'model_kwargs': {'output_config': {'effort': 'medium'}}}}))
            command = (runtime.LAUNCH_PREFIX + '--yolo --model=anthropic/claude-sonnet-5 --task=' + shlex.quote(task) +
                       ' --output=' + str(root / 'out.json') + ' -c mini -c ' + str(custom) +
                       ' -c model.model_kwargs.output_config.effort=high' +
                       ' -c model.model_kwargs.thinking.type=adaptive' +
                       ' -c model.model_kwargs.max_tokens=64000 ' + runtime.LAUNCH_SUFFIX)
            rewritten, payload = runtime.task_file_command(command, str(config))
            config.write_bytes(payload)
            observed = {}

            class Capture:
                def run(self, value):
                    observed['task'] = value

            def model(**kwargs):
                observed['model'] = kwargs['config']
                return object()

            def environment(*args, **kwargs):
                return object()

            def agent(*args, **kwargs):
                observed['agent'] = args[2]
                return Capture()

            tokens = shlex.split(rewritten)
            args = tokens[tokens.index('mini-swe-agent') + 1:tokens.index('--exit-immediately') + 1]
            with patch.object(mini, 'configure_if_first_time'), patch.object(mini, 'get_model', side_effect=model), \
                    patch.object(mini, 'get_environment', side_effect=environment), patch.object(mini, 'get_agent', side_effect=agent):
                result = CliRunner().invoke(mini.app, args)
            assert result.exit_code == 0, type(result.exception).__name__
            settings = observed['model']['model_kwargs']
            checks = {'task_bytes_exact': observed['task'].encode() == task.encode(),
                      'task_absent_from_argv': all('zenohd' not in arg and 'advanced_pub_sub' not in arg for arg in args),
                      'effort_preserved': settings['output_config']['effort'] == 'high',
                      'adaptive_preserved': settings['thinking'] == {'type': 'adaptive'},
                      'max_tokens_preserved': settings['max_tokens'] == 64000,
                      'model_preserved': observed['model']['model_name'] == 'anthropic/claude-sonnet-5',
                      'yolo_preserved': observed['agent']['mode'] == 'yolo',
                      'exit_immediately_preserved': observed['agent']['confirm_exit'] is False}
            assert all(checks.values()), checks
            source_paths = ['scripts/benchmark_agent_runtime.py', 'scripts/tests/test_benchmark_agent_runtime.py',
                            str(Path(__file__).relative_to(ROOT))]
            proof = {'package': 'mini-swe-agent', 'version': '2.4.6',
                     'wheel_sha256': hashlib.sha256(package_bytes).hexdigest(),
                     'mode': 'real_typer_cli_with_offline_factory_stubs', 'model_calls': 0,
                     'checks': checks, 'passed': all(checks.values()),
                     'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_paths}}
            Path(__file__).with_name('cli-smoke.json').write_text(json.dumps(proof, indent=2) + '\n')
            print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
