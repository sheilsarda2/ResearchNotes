"""Actual package execution on synthetic HTTP/SSE; two expected failures are blockers."""
import json
import unittest
import offline_probe as p

class OfflineStreaming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proof=p.probe();cls.rows={r['name']:r for r in cls.proof['outcomes']}
        (p.BASE/'validation-results.json').write_text(json.dumps(cls.proof,indent=2,default=str)+'\n')
    def response(self,name):
        row=self.rows[name];self.assertTrue(row['returned']);value=row['response']
        return value['extra']['response'] if row['mini'] else value
    def test_no_network_or_tool_execution(self):
        self.assertEqual(self.proof['network_attempts_blocked'],[])
        self.assertEqual(self.proof['model_calls'],0);self.assertEqual(self.proof['tool_executions'],0)
    def test_exact_model_effort_budget_and_native_route(self):
        for row in self.rows.values():
            self.assertEqual(len(row['requests']),1)
            req=row['requests'][0];data=req['data']
            self.assertEqual(req['url'],'https://offline.invalid/v1/messages')
            self.assertEqual(data['model'],'claude-fable-5-1');self.assertEqual(data['max_tokens'],64000)
            self.assertEqual(data['thinking'],{'type':'adaptive'});self.assertEqual(data['output_config'],{'effort':'max'})
            self.assertNotIn('complete_response',data)
            if row['name']!='nonstream_baseline':self.assertIs(data['stream'],True)
    def test_fragmented_tool_arguments_and_single_mini_step(self):
        r=self.rows['complete_mini'];self.assertEqual(r['mini_counted_calls'],1)
        self.assertEqual(r['response']['extra']['actions'],[{'command':'printf fixture','tool_call_id':'toolu_fixture'}])
        message=self.response('complete_mini')['choices'][0]['message']
        self.assertEqual(json.loads(message['tool_calls'][0]['function']['arguments']),{'command':'printf fixture'})
    def test_usage_cache_counts_and_synthetic_cost(self):
        for name in ('complete_mini','nonstream_baseline'):
            usage=self.response(name)['usage']
            self.assertEqual((usage['prompt_tokens'],usage['completion_tokens'],usage['total_tokens']),(150,25,175))
            self.assertEqual(usage['cache_creation_input_tokens'],20);self.assertEqual(usage['cache_read_input_tokens'],30)
            self.assertAlmostEqual(self.rows[name]['response']['extra']['cost'],0.000178)
    def test_finish_reasons_preserved(self):
        self.assertEqual(self.response('complete')['choices'][0]['finish_reason'],'tool_calls')
        self.assertEqual(self.response('max_tokens_finish')['choices'][0]['finish_reason'],'length')
    def test_explicit_transport_and_sse_errors_return_no_partial(self):
        for name in ('raised_read_error','sse_error_after_tool_json'):
            r=self.rows[name];self.assertFalse(r['returned']);self.assertEqual(r['mini_counted_calls'],0)
            self.assertEqual(r['error_type'],'MidStreamFallbackError')
    def test_nonstream_baseline_has_original_signed_thinking(self):
        message=self.response('nonstream_baseline')['choices'][0]['message']
        self.assertEqual(message['thinking_blocks'],[{'type':'thinking','thinking':'Synthetic reasoning.','signature':'fixture-signature'}])
    @unittest.expectedFailure
    def test_required_exact_signed_thinking_survives_stream_and_next_turn(self):
        expected=[{'type':'thinking','thinking':'Synthetic reasoning.','signature':'fixture-signature'}]
        message=self.response('complete_mini')['choices'][0]['message']
        self.assertEqual(message['thinking_blocks'],expected)
        next_wire=self.rows['mini_next_turn_wire']['requests'][0]['data']['messages'][1]['content']
        self.assertEqual([x for x in next_wire if x['type']=='thinking'],expected)
    @unittest.expectedFailure
    def test_required_silent_truncation_must_not_return_or_count_partial(self):
        r=self.rows['silent_eof_after_tool_json'];self.assertFalse(r['returned'])
        self.assertEqual(r['mini_counted_calls'],0)

if __name__=='__main__':unittest.main(verbosity=2)
