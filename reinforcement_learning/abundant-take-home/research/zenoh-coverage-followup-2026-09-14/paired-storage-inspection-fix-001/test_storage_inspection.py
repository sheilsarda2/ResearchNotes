"""Exercise the real Docker CLI formatter against an isolated loopback fixture.

No request reaches the real Docker daemon; no container, claim, or model exists.
The exact production template and limit predicates are extracted from its AST.
"""
import ast
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest

BASE = Path(__file__).resolve().parents[1]
OLD = BASE / 'paired-regrades/run_paired_regrades.py'
NEW = BASE / 'paired-regrades/run_paired_regrades_v2.py'


def production_parts(path):
    tree = ast.parse(path.read_text())
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_case')
    assignments = [n for n in ast.walk(run) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'template' for t in n.targets)]
    assert len(assignments) == 1
    checks = [n for n in ast.walk(run) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
              and isinstance(n.value.func, ast.Name) and n.value.func.id == 'require'
              and len(n.value.args) == 2 and isinstance(n.value.args[1], ast.Constant)
              and n.value.args[1].value in ('Wrong actual container limits', 'Storage differs from oracle')]
    assert len(checks) == 2
    return ast.literal_eval(assignments[0].value), compile(ast.Module(body=checks, type_ignores=[]), str(path), 'exec')


TEMPLATE, CHECKS = production_parts(NEW)
OLD_TEMPLATE, _ = production_parts(OLD)
IMAGE = 'sha256:' + 'a' * 64
HOST = {'Memory': 8589934592, 'MemorySwap': 17179869184, 'NanoCpus': 4000000000,
        'NetworkMode': 'none'}


def production_validate(observed, oracle_storage=None):
    def require(value, message):
        if not value:
            raise ValueError(message)
    exec(CHECKS, {'observed': observed, 'args': SimpleNamespace(verifier_image_id=IMAGE),
                 'swap': HOST['MemorySwap'], 'inputs': {'oracle_observed_host_config': {'StorageOpt': oracle_storage}},
                 'require': require})


class StorageInspection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which('docker') is None:
            raise RuntimeError('Actual Docker CLI is required; no skipped regression is accepted')

    def inspect(self, host=None, image=IMAGE, state=None, template=TEMPLATE):
        fixture = {'Id': 'b' * 64, 'Name': '/fixture', 'Image': image,
                   'HostConfig': copy.deepcopy(HOST if host is None else host),
                   'State': {'Running': True} if state is None else state}
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(('GET', self.path))
                if self.path.endswith('/containers/fixture/json'):
                    payload = json.dumps(fixture).encode()
                    self.send_response(200)
                else:
                    payload = b'{"message":"unexpected fixture route"}'
                    self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def do_HEAD(self):
                requests.append(('HEAD', self.path))
                self.send_response(200 if self.path == '/_ping' else 404)
                self.send_header('API-Version', '1.47')
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='zenoh-docker-fixture-') as folder:
                env = {k: v for k, v in os.environ.items() if not k.startswith('DOCKER_')}
                env.update(DOCKER_API_VERSION='1.47', DOCKER_CONFIG=folder)
                result = subprocess.run(['docker', '--host', 'tcp://127.0.0.1:' + str(server.server_port),
                                         'inspect', '--type', 'container', '--format', template, 'fixture'],
                                        env=env, text=True, capture_output=True, timeout=10)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.assertTrue(requests)
        self.assertTrue(all(method in ('GET', 'HEAD') for method, _ in requests))
        self.assertTrue(all(path == '/_ping' or path.endswith('/containers/fixture/json') for _, path in requests))
        return result

    def observed(self, **kwargs):
        result = self.inspect(**kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_original_formatter_reproduces_missing_optional_key_failure(self):
        result = self.inspect(template=OLD_TEMPLATE)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('map has no entry for key "StorageOpt"', result.stderr)

    def test_missing_storage_is_null_and_matches_unenforced_oracle(self):
        observed = self.observed()
        self.assertIsNone(observed['storage_opt'])
        production_validate(observed)

    def test_explicit_null_storage_is_preserved(self):
        observed = self.observed(host={**HOST, 'StorageOpt': None})
        self.assertIsNone(observed['storage_opt'])
        production_validate(observed)

    def test_empty_storage_map_is_preserved(self):
        observed = self.observed(host={**HOST, 'StorageOpt': {}})
        self.assertEqual(observed['storage_opt'], {})
        production_validate(observed)

    def test_nonempty_storage_is_preserved_and_rejected_for_actual_oracle(self):
        quota = {'size': '30G'}
        observed = self.observed(host={**HOST, 'StorageOpt': quota})
        self.assertEqual(observed['storage_opt'], quota)
        with self.assertRaisesRegex(ValueError, 'Storage differs from oracle'):
            production_validate(observed)
        production_validate(observed, quota)

    def test_wrong_image_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Wrong actual container limits'):
            production_validate(self.observed(image='sha256:' + 'c' * 64))

    def test_cpu_memory_swap_network_limits_are_not_relaxed(self):
        for field, wrong in [('NanoCpus', 2000000000), ('Memory', 4294967296),
                             ('MemorySwap', 8589934592), ('NetworkMode', 'bridge')]:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, 'Wrong actual container limits'):
                    production_validate(self.observed(host={**HOST, field: wrong}))

    def test_stopped_container_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Wrong actual container limits'):
            production_validate(self.observed(state={'Running': False}))

    def test_missing_required_field_remains_a_formatter_error(self):
        for field in HOST:
            with self.subTest(field=field):
                host = dict(HOST)
                del host[field]
                result = self.inspect(host=host)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('map has no entry for key', result.stderr)

    def test_runtime_diff_changes_only_optional_lookup(self):
        expected = OLD.read_text().replace('"storage_opt":{{json .HostConfig.StorageOpt}}',
                                           '"storage_opt":{{json (index .HostConfig "StorageOpt")}}')
        self.assertEqual(NEW.read_text(), expected)
        self.assertNotEqual(TEMPLATE, OLD_TEMPLATE)


if __name__ == '__main__':
    unittest.main(verbosity=2)
