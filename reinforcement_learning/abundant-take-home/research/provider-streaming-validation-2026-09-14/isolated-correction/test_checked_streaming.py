"""Offline regressions for the isolated candidate; no baseline artifact writes."""
import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import offline_probe as p
from checked_anthropic_iterator import offline_checked_iterator,CheckedAnthropicIterator,IncompleteAnthropicStream

class CheckedStreaming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with offline_checked_iterator():cls.proof=p.probe()
        cls.rows={r['name']:r for r in cls.proof['outcomes']}
        (HERE/'results.json').write_text(json.dumps(cls.proof,indent=2,default=str)+'\n')
    def response(self,name):
        r=self.rows[name];self.assertTrue(r['returned'],r.get('error'))
        return r['response']['extra']['response'] if r['mini'] else r['response']
    def case(self,name,rows):
        with offline_checked_iterator():r=p.run_case(name,rows,mini=True)
        return r
    def assert_rejected(self,r):
        self.assertFalse(r['returned']);self.assertEqual(r['mini_counted_calls'],0)
        self.assertEqual(r['error_type'],'MidStreamFallbackError')
    def test_exact_signed_thinking_and_next_turn(self):
        expected=[{'type':'thinking','thinking':'Synthetic reasoning.','signature':'fixture-signature'}]
        self.assertEqual(self.response('complete_mini')['choices'][0]['message']['thinking_blocks'],expected)
        content=self.rows['mini_next_turn_wire']['requests'][0]['data']['messages'][1]['content']
        self.assertEqual([x for x in content if x['type']=='thinking'],expected)
    def test_tool_fragments_and_step(self):
        r=self.rows['complete_mini'];self.assertEqual(r['mini_counted_calls'],1)
        self.assertEqual(r['response']['extra']['actions'],[{'command':'printf fixture','tool_call_id':'toolu_fixture'}])
    def test_usage_and_synthetic_cost_match_nonstream(self):
        for name in ('complete_mini','nonstream_baseline'):
            u=self.response(name)['usage']
            self.assertEqual((u['prompt_tokens'],u['completion_tokens'],u['total_tokens']),(150,25,175))
            self.assertEqual(u['cache_creation_input_tokens'],20);self.assertEqual(u['cache_read_input_tokens'],30)
            self.assertAlmostEqual(self.rows[name]['response']['extra']['cost'],0.000178)
    def test_wire_model_effort_tokens_unchanged(self):
        for r in self.rows.values():
            self.assertEqual(len(r['requests']),1)
            req=r['requests'][0];d=req['data']
            self.assertEqual(req['url'],'https://offline.invalid/v1/messages')
            self.assertEqual(d['model'],'claude-fable-5-1');self.assertEqual(d['max_tokens'],64000)
            self.assertEqual(d['thinking'],{'type':'adaptive'});self.assertEqual(d['output_config'],{'effort':'max'})
            self.assertNotIn('complete_response',d)
    def test_finish_reasons(self):
        self.assertEqual(self.response('complete')['choices'][0]['finish_reason'],'tool_calls')
        self.assertEqual(self.response('max_tokens_finish')['choices'][0]['finish_reason'],'length')
    def test_explicit_error_and_silent_eof_rejected(self):
        for name in ('raised_read_error','sse_error_after_tool_json','silent_eof_after_tool_json'):
            with self.subTest(name=name):self.assert_rejected(self.rows[name])
    def test_no_network_or_tools(self):
        self.assertEqual(self.proof['network_attempts_blocked'],[])
        self.assertEqual(self.proof['model_calls'],0);self.assertEqual(self.proof['tool_executions'],0)
    def test_missing_message_stop_rejected(self):
        self.assert_rejected(self.case('missing_stop',p.events()[:-1]))
    def test_missing_final_usage_rejected(self):
        rows=p.events();rows[-2]['usage']={}
        self.assert_rejected(self.case('missing_usage',rows))
    def test_unclosed_tool_block_rejected(self):
        rows=p.events();del rows[13]
        self.assert_rejected(self.case('open_tool',rows))
    def test_duplicate_start_rejected(self):
        rows=p.events();rows.insert(1,copy.deepcopy(rows[0]))
        self.assert_rejected(self.case('duplicate_start',rows))
    def test_iterator_rejects_content_after_stop(self):
        iterator=CheckedAnthropicIterator(iter([]),sync_stream=True)
        for row in p.events():iterator.chunk_parser(row)
        with self.assertRaises(IncompleteAnthropicStream):iterator.chunk_parser(p.events()[8])
    def test_fragmented_signature_preserved(self):
        rows=p.events();rows[5]['delta']['signature']='fixture-';extra=copy.deepcopy(rows[5]);extra['delta']['signature']='signature';rows.insert(6,extra)
        r=self.case('signature_fragments',rows);self.assertTrue(r['returned'],r.get('error'))
        self.assertEqual(r['response']['thinking_blocks'],[{'type':'thinking','thinking':'Synthetic reasoning.','signature':'fixture-signature'}])
    def test_empty_and_multiple_thinking_blocks(self):
        rows=p.events();rows[3]['delta']['thinking']='';rows[4]['delta']['thinking']=''
        extra=[{'type':'content_block_start','index':3,'content_block':{'type':'thinking','thinking':''}},
            {'type':'content_block_delta','index':3,'delta':{'type':'thinking_delta','thinking':'Second.'}},
            {'type':'content_block_delta','index':3,'delta':{'type':'signature_delta','signature':'sig2'}},
            {'type':'content_block_stop','index':3}]
        rows[-2:-2]=extra;r=self.case('multiple_blocks',rows)
        self.assertTrue(r['returned'],r.get('error'));self.assertEqual(r['response']['thinking_blocks'],[
            {'type':'thinking','thinking':'','signature':'fixture-signature'},
            {'type':'thinking','thinking':'Second.','signature':'sig2'}])
    def test_missing_thinking_signature_rejected(self):
        rows=p.events();del rows[5]
        self.assert_rejected(self.case('unsigned_thinking',rows))

    def test_every_incomplete_event_prefix_rejected(self):
        rows=p.events()
        for length in range(len(rows)):
            with self.subTest(events=length):
                r=self.case('prefix_'+str(length),rows[:length])
                self.assertFalse(r['returned']);self.assertEqual(r['mini_counted_calls'],0)
    def test_redacted_thinking_block_preserved(self):
        rows=p.events()
        rows[2:7]=[{'type':'content_block_start','index':0,'content_block':{'type':'redacted_thinking','data':'fixture-redacted'}},
            {'type':'content_block_stop','index':0}]
        r=self.case('redacted',rows);self.assertTrue(r['returned'],r.get('error'))
        self.assertEqual(r['response']['thinking_blocks'],[{'type':'redacted_thinking','data':'fixture-redacted'}])
    def test_async_not_silently_covered(self):
        with self.assertRaisesRegex(ValueError,'synchronous'):
            CheckedAnthropicIterator(iter([]),sync_stream=False)

    def test_multiple_text_thinking_redacted_order_matches_nonstream_next_turn(self):
        content=[{'type':'thinking','thinking':'First thought.','signature':'sigA'},
            {'type':'text','text':'Alpha '},{'type':'redacted_thinking','data':'fixture-redacted'},
            {'type':'thinking','thinking':'Second thought.','signature':'sigB'},
            {'type':'text','text':'Beta.'},{'type':'tool_use','id':'toolu_fixture','name':'bash','input':{'command':'printf fixture'}}]
        events=[copy.deepcopy(p.events()[0])]
        for index,block in enumerate(content):
            typ=block['type'];start=copy.deepcopy(block)
            if typ=='thinking':start={'type':typ,'thinking':''}
            elif typ=='text':start={'type':typ,'text':''}
            elif typ=='tool_use':start['input']={}
            events.append({'type':'content_block_start','index':index,'content_block':start})
            def delta(kind,**value):events.append({'type':'content_block_delta','index':index,'delta':dict(type=kind,**value)})
            if typ=='thinking':
                delta('thinking_delta',thinking=block['thinking'][:4]);delta('thinking_delta',thinking=block['thinking'][4:])
                delta('signature_delta',signature=block['signature'])
            elif typ=='text':delta('text_delta',text=block['text'])
            elif typ=='tool_use':
                value=json.dumps(block['input']);delta('input_json_delta',partial_json=value[:8]);delta('input_json_delta',partial_json=value[8:])
            events.append({'type':'content_block_stop','index':index})
        events.extend(copy.deepcopy(p.events()[-2:]))
        full=dict(p.events()[0]['message']);full.update(content=content,stop_reason='tool_use',
            usage={'input_tokens':100,'output_tokens':25,'cache_creation_input_tokens':20,'cache_read_input_tokens':30})
        records={}
        def query(stream,messages,label):
            requests=[]
            def post(self,url,*args,**kwargs):
                requests.append(json.loads(kwargs['data']));request=p.httpx.Request('POST',url)
                if stream:return p.httpx.Response(200,stream=p.SyntheticBytes(events),request=request)
                return p.httpx.Response(200,json=full,request=request)
            model=p.LitellmModel(model_name=p.MODEL,cost_tracking='ignore_errors',model_kwargs=dict(
                api_base='https://offline.invalid',api_key='offline-fixture-key',drop_params=True,
                thinking={'type':'adaptive'},output_config={'effort':'max'},max_tokens=64000,
                stream=stream,complete_response=stream,num_retries=0))
            with offline_checked_iterator(),patch.object(p.HTTPHandler,'post',post):result=model.query(messages)
            records[label]={'requests':requests,'response':result}
            return result
        first=[{'role':'user','content':'Synthetic multiple block fixture.'}]
        streamed=query(True,first,'streamed');baseline=query(False,first,'nonstream')
        expected=[block for block in content if block['type'] in ('thinking','redacted_thinking')]
        self.assertEqual(streamed['thinking_blocks'],expected);self.assertEqual(baseline['thinking_blocks'],expected)
        self.assertEqual(streamed['content'],baseline['content']);self.assertEqual(streamed['content'],'Alpha Beta.')
        self.assertEqual(streamed['extra']['actions'],baseline['extra']['actions'])
        self.assertEqual([{k:v for k,v in tool.items() if k!='index'} for tool in streamed['tool_calls']],
            [{k:v for k,v in tool.items() if k!='index'} for tool in baseline['tool_calls']])
        observation={'role':'tool','tool_call_id':'toolu_fixture','content':'Synthetic result, no execution.'}
        query(True,first+[streamed,observation],'streamed_next')
        query(False,first+[baseline,observation],'nonstream_next')
        a=records['streamed_next']['requests'][0]['messages'];b=records['nonstream_next']['requests'][0]['messages']
        self.assertEqual(a,b)
        self.assertEqual([block for block in a[1]['content'] if block['type'] in ('thinking','redacted_thinking')],expected)
        (HERE/'multiple-block-roundtrip.json').write_text(json.dumps(records,indent=2,default=str)+'\n')

if __name__=='__main__':unittest.main(verbosity=2)
