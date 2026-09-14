"""Pure guard/privacy fixtures. No package/model import or HTTP execution."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('prepared_probe_tests',HERE/'run_probe.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class Guards(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.output=Path(self.temp.name)
        self.e=p.Evidence(self.output,{'fixture':True})
        self.block={'type':'thinking','thinking':'PRIVATE-THINKING','signature':'PRIVATE-SIGNATURE'}
    def tearDown(self):self.temp.cleanup()
    def request(self,blocks=None):
        body=dict(model='claude-fable-5-1',stream=True,thinking={'type':'adaptive'},output_config={'effort':'max'},
            max_tokens=64000,tools=[{'name':'bash'}],messages=[{'role':'user','content':'fixture'}])
        if blocks is not None:body['messages'].append({'role':'assistant','content':blocks})
        return types.SimpleNamespace(url=p.EXPECTED_BASE+'/v1/messages',method='POST',content=p.canonical(body))
    def first(self,blocks=None):
        self.e.active_turn=1;self.e.network_guard(self.request())
        self.e.state['turns']['1']['provider_blocks']=p.signed_projection([self.block] if blocks is None else blocks)
    def test_two_requests_maximum(self):
        self.first();self.e.active_turn=2;self.e.network_guard(self.request([self.block]))
        self.e.active_turn=3
        with self.assertRaisesRegex(ValueError,'Two-request'):self.e.network_guard(self.request())
        self.assertEqual(self.e.state['model_requests_sent'],2)
    def test_implicit_retry_rejected(self):
        self.first()
        with self.assertRaisesRegex(ValueError,'retry'):self.e.network_guard(self.request())
        self.assertEqual(self.e.state['model_requests_sent'],1)
    def test_other_route_rejected_without_count(self):
        self.e.active_turn=1;r=self.request();r.url='https://example.invalid/v1/messages'
        with self.assertRaisesRegex(ValueError,'route'):self.e.network_guard(r)
        self.assertEqual(self.e.state['model_requests_sent'],0)
    def test_wire_settings_cannot_change(self):
        for key,value in [('model','other'),('max_tokens',100),('stream',False),('output_config',{'effort':'high'})]:
            with self.subTest(key=key):
                r=self.request();d=json.loads(r.content);d[key]=value;r.content=p.canonical(d);self.e.active_turn=1
                with self.assertRaisesRegex(ValueError,'settings'):self.e.network_guard(r)
        self.assertEqual(self.e.state['model_requests_sent'],0)
    def test_thinking_signature_or_order_change_blocks_second_send(self):
        self.first();self.e.active_turn=2
        for key in ('thinking','signature'):
            block=copy.deepcopy(self.block);block[key]+='changed'
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'Thinking blocks changed'):
                self.e.network_guard(self.request([block]))
        self.assertEqual(self.e.state['model_requests_sent'],1)
    def test_missing_signed_block_does_not_fabricate_signed_coverage(self):
        self.first([]);self.e.active_turn=2;self.e.network_guard(self.request([]))
        self.assertEqual(self.e.state['turns']['1']['provider_blocks']['signed_count'],0)
    def test_no_thinking_signature_headers_or_body_saved(self):
        self.first();self.e.active_turn=2;self.e.network_guard(self.request([self.block]));self.e.save()
        text='\n'.join(x.read_text() for x in self.output.iterdir() if x.is_file())
        for forbidden in ('PRIVATE-THINKING','PRIVATE-SIGNATURE','headers','Authorization','request_body"'):
            self.assertNotIn(forbidden,text)
        self.assertIn(p.hashed([self.block]),text)
    def test_usage_whitelist_discards_unexpected_strings(self):
        raw={'input_tokens':10,'output_tokens':20,'headers':'SECRET','cache_creation':{'ephemeral_5m_input_tokens':3,'key':'SECRET'}}
        self.assertEqual(p.numeric_usage(raw),{'input_tokens':10,'output_tokens':20,'cache_creation':{'ephemeral_5m_input_tokens':3}})
    def test_check_only_never_calls_execute(self):
        with patch.object(sys,'argv',['run_probe.py','--check-only']),patch.object(p,'preflight',return_value={'prepared_only':True}),patch.object(p,'execute') as execute,redirect_stdout(io.StringIO()):
            self.assertEqual(p.main(),0)
        execute.assert_not_called()
    def test_output_cannot_overwrite_or_escape_before_credentials(self):
        for target in (HERE, self.output):
            with self.subTest(target=target),self.assertRaises(ValueError):p.execute(target,{'fixture':True})
    def test_no_default_execution_flag(self):
        with patch.object(sys,'argv',['run_probe.py']),patch.object(p,'execute') as execute,patch.object(p,'preflight') as preflight:
            with self.assertRaises(SystemExit),patch('sys.stderr',io.StringIO()):p.main()
        execute.assert_not_called();preflight.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
