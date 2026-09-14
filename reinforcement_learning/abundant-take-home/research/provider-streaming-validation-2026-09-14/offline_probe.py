"""Offline experiment against exact local LiteLLM + captured Mini source.
No real HTTP request or tool execution is permitted.
"""
import os, sys, json, socket, tempfile, pathlib, hashlib, importlib.metadata as md, datetime, copy
from unittest.mock import patch
BASE=pathlib.Path(__file__).resolve().parent
sys.dont_write_bytecode=True
identity=json.loads((BASE/'source-identity.json').read_text())
for name,expected in identity['file_sha256'].items():
    assert hashlib.sha256((BASE/'source-snapshot'/name).read_bytes()).hexdigest()==expected, name
session=tempfile.TemporaryDirectory(prefix='streaming-offline-')
os.environ['MSWEA_GLOBAL_CONFIG_DIR']=session.name
os.environ['MSWEA_SILENT_STARTUP']='1'
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
os.environ['MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT']='1'
blocked=[]
def deny(*args,**kwargs):
    blocked.append('blocked_network_attempt')
    raise RuntimeError('Offline harness denies network')
socket.socket.connect=deny
socket.create_connection=deny
socket.getaddrinfo=deny
sys.path.insert(0,str(BASE/'source-snapshot'))
import httpx
httpx.Client.send=deny
httpx.AsyncClient.send=deny
import litellm
from litellm.llms.custom_httpx.http_handler import HTTPHandler
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.models import GLOBAL_MODEL_STATS
litellm.telemetry=False
litellm.suppress_debug_info=True
MODEL='anthropic/claude-fable-5-1'
# The offline bundled map lacks the routed 5-1 alias. Preserve stock capability
# metadata and use an explicit synthetic tariff only for accounting assertions.
metadata=dict(litellm.model_cost['claude-fable-5'])
metadata.update(input_cost_per_token=0.000001,output_cost_per_token=0.000002,
    cache_read_input_token_cost=0.0000001,cache_creation_input_token_cost=0.00000125)
litellm.register_model({MODEL:metadata,'claude-fable-5-1':metadata})

def events():
    def event(typ,**kw):return dict(type=typ,**kw)
    return [
 event('message_start',message=dict(id='msg_offline_fixture',type='message',role='assistant',model='claude-fable-5-1',content=[],stop_reason=None,stop_sequence=None,usage=dict(input_tokens=100,output_tokens=1,cache_creation_input_tokens=20,cache_read_input_tokens=30))),
 event('ping'),
 event('content_block_start',index=0,content_block=dict(type='thinking',thinking='')),
 event('content_block_delta',index=0,delta=dict(type='thinking_delta',thinking='Synthetic ')),
 event('content_block_delta',index=0,delta=dict(type='thinking_delta',thinking='reasoning.')),
 event('content_block_delta',index=0,delta=dict(type='signature_delta',signature='fixture-signature')),
 event('content_block_stop',index=0),
 event('content_block_start',index=1,content_block=dict(type='text',text='')),
 event('content_block_delta',index=1,delta=dict(type='text_delta',text='Synthetic tool request.')),
 event('content_block_stop',index=1),
 event('content_block_start',index=2,content_block=dict(type='tool_use',id='toolu_fixture',name='bash',input={})),
 event('content_block_delta',index=2,delta=dict(type='input_json_delta',partial_json='{"command": "printf ')),
 event('content_block_delta',index=2,delta=dict(type='input_json_delta',partial_json='fixture"}')),
 event('content_block_stop',index=2),
 event('message_delta',delta=dict(stop_reason='tool_use',stop_sequence=None),usage=dict(output_tokens=25)),
 event('message_stop')]

class SyntheticBytes(httpx.SyncByteStream):
    def __init__(self, rows, broken=None):self.rows=rows;self.broken=broken
    def __iter__(self):
        for row in self.rows:
            encoded=('event: '+row['type']+'\ndata: '+json.dumps(row)+'\n\n').encode()
            # Fragment even SSE framing itself, exercising real httpx iter_lines.
            for offset in range(0,len(encoded),17):yield encoded[offset:offset+17]
        if self.broken:raise self.broken

requests=[]
def run_case(name,rows,broken=None,mini=False,stream=True,messages=None):
    request_rows=[]
    def post(self,url,*args,**kwargs):
        data=json.loads(kwargs['data']);request_rows.append(dict(url=url,data=data,stream=kwargs.get('stream')))
        request=httpx.Request('POST',url)
        if stream:return httpx.Response(200,headers={'content-type':'text/event-stream','request-id':'offline-fixture'},
            stream=SyntheticBytes(rows,broken),request=request)
        body=dict(id='msg_offline_fixture',type='message',role='assistant',model='claude-fable-5-1',
            content=[dict(type='thinking',thinking='Synthetic reasoning.',signature='fixture-signature'),
                dict(type='text',text='Synthetic tool request.'),
                dict(type='tool_use',id='toolu_fixture',name='bash',input={'command':'printf fixture'})],
            stop_reason='tool_use',stop_sequence=None,
            usage=dict(input_tokens=100,output_tokens=25,cache_creation_input_tokens=20,cache_read_input_tokens=30))
        return httpx.Response(200,json=body,request=request)
    kwargs=dict(api_base='https://offline.invalid',api_key='offline-fixture-key',stream=stream,complete_response=stream,
                thinking={'type':'adaptive'},output_config={'effort':'max'},max_tokens=64000,num_retries=0,drop_params=True)
    result={'name':name,'mini':mini,'completed_synthetic_sse':any(e['type']=='message_stop' for e in rows)}
    before=GLOBAL_MODEL_STATS.n_calls
    try:
        with patch.object(HTTPHandler,'post',post):
            if mini:
                model=LitellmModel(model_name=MODEL,model_kwargs=kwargs,cost_tracking='ignore_errors')
                response=model.query(messages or [{'role':'user','content':'Synthetic offline fixture. Do not execute tools.'}])
                result['response']=response
            else:
                response=litellm.completion(model=MODEL,messages=[{'role':'user','content':'Synthetic offline fixture.'}],
                    tools=[{'type':'function','function':{'name':'bash','parameters':{'type':'object','properties':{'command':{'type':'string'}},'required':['command']}}}],**kwargs)
                result['response']=response.model_dump()
        result['returned']=True
    except Exception as e:
        result.update(returned=False,error_type=type(e).__name__,error=str(e)[:400])
    result.update(requests=request_rows,mini_counted_calls=GLOBAL_MODEL_STATS.n_calls-before)
    return result

def probe():
    rows=events()
    outcomes=[run_case('complete',rows),run_case('complete_mini',rows,mini=True),
        run_case('nonstream_baseline',rows,mini=True,stream=False),
        run_case('raised_read_error',rows[:13],httpx.ReadError('synthetic interrupted stream',request=httpx.Request('POST','https://offline.invalid/v1/messages')),mini=True),
        run_case('silent_eof_after_tool_json',rows[:14],mini=True),
        run_case('sse_error_after_tool_json',rows[:14]+[dict(type='error',error=dict(type='api_error',message='synthetic upstream failure'))],mini=True)]
    complete=outcomes[1]
    if complete['returned']:
        messages=[{'role':'user','content':'Synthetic offline fixture. Do not execute tools.'},complete['response'],
            {'role':'tool','tool_call_id':'toolu_fixture','content':'Synthetic tool result; no command executed.'}]
        outcomes.append(run_case('mini_next_turn_wire',rows,mini=True,messages=messages))
    limited=copy.deepcopy(rows);limited[-2]['delta']['stop_reason']='max_tokens'
    outcomes.append(run_case('max_tokens_finish',limited))
    return dict(kind='offline_synthetic_streaming_probe',model_calls=0,tool_executions=0,network_attempts_blocked=blocked,
        execution_python=sys.version,litellm_version=md.version('litellm'),outcomes=outcomes,
        completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())

if __name__=='__main__':
    result=probe()
    (BASE/'fixtures.json').write_text(json.dumps(events(),indent=2)+'\n')
    (BASE/'probe-results.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
    print(json.dumps({**{k:v for k,v in result.items() if k!='outcomes'},'outcomes':[{k:v for k,v in r.items() if k not in ('requests','response')} for r in result['outcomes']]},indent=2))
