"""Prepare/check or explicitly run ONE isolated, bounded two-request SSE probe.

No task runtime, tool execution, model retries, or live benchmark changes.
Never import offline_probe: it installs synthetic prices and network blockers.
"""
import argparse
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import signal
import socket
import sys
import time
import threading
from unittest.mock import patch

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
BASE=HERE.parent
ROOT=BASE.parents[1]
EXPECTED_BASE='https://take-home-automation.vercel.app'
MODEL='anthropic/claude-fable-5-1'
COMMAND="printf 'stream_probe_ok\\n'"
MAX_REQUESTS=2
TOTAL_WALL_SECONDS=240
MAX_EVENTS=20000
PROMPT=('This is a bounded streaming protocol probe. Make exactly one bash tool call with the command '
        +json.dumps(COMMAND)+'. No other tool calls or explanation are needed. The caller will not execute '
        'the command; it will return an explicitly synthetic observation. Do not inspect any environment.')
OBSERVATION=('SYNTHETIC OBSERVATION: no command was executed. The fabricated protocol-test output is '
             'stream_probe_ok. This is synthetic data, not execution evidence.')


def now():return datetime.now(timezone.utc).isoformat()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def hashed(value):return hashlib.sha256(canonical(value)).hexdigest()
def require(condition,message):
    if not condition:raise ValueError(message)
def dump(path,value):
    path=Path(path);temporary=path.with_name(path.name+'.tmp')
    with temporary.open('w') as output:json.dump(value,output,indent=2,sort_keys=True);output.write('\n')
    temporary.replace(path)


def signed_projection(blocks):
    """Only hashes and lengths leave memory; never return content/signatures."""
    signed=[{key:block[key] for key in ('type','thinking','signature')}
            for block in (blocks or []) if block.get('type')=='thinking' and block.get('signature')]
    ordered=[block for block in (blocks or []) if block.get('type') in ('thinking','redacted_thinking')]
    return dict(signed_count=len(signed),signed_blocks_sha256=hashed(signed),
        ordered_thinking_blocks_sha256=hashed(ordered),ordered_types=[b['type'] for b in ordered],
        signed_blocks=[dict(sha256=hashed(b),thinking_utf8_bytes=len(b['thinking'].encode()),
                           signature_utf8_bytes=len(b['signature'].encode())) for b in signed])


def numeric_usage(value):
    allowed={'input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens',
        'prompt_tokens','completion_tokens','total_tokens','cache_creation',
        'ephemeral_5m_input_tokens','ephemeral_1h_input_tokens','prompt_tokens_details',
        'completion_tokens_details','cached_tokens','cache_write_tokens','cache_creation_tokens',
        'reasoning_tokens','text_tokens'}
    return {key:(numeric_usage(v) if isinstance(v,dict) else v)
            for key,v in (value or {}).items() if key in allowed and
            (isinstance(v,dict) or type(v) in (int,float) or v is None)}


def preflight():
    """Read only. No credentials, network-capable imports, or output directory."""
    baseline=json.loads((BASE/'baseline-proof.json').read_text())
    for name,expected in baseline['artifact_file_sha256'].items():
        require(digest(BASE/name)==expected,'Frozen baseline artifact drift')
    correction=BASE/'isolated-correction'
    proof=json.loads((correction/'proof.json').read_text())
    require(proof['offline_regressions_passed'] is True and proof['regression_checks']==19,'Correction proof incomplete')
    for name,expected in proof['artifact_file_sha256'].items():
        require(digest(correction/name)==expected,'Reviewed correction artifact drift')
    info=json.loads((BASE/'execution-identity.json').read_text())
    require(importlib.metadata.version('litellm')=='1.100.1','Wrong LiteLLM version')
    spec=importlib.util.find_spec('litellm');require(spec and spec.origin,'LiteLLM not installed')
    package=Path(spec.origin).parent
    for name,expected in info['litellm_file_sha256'].items():
        require(digest(package/name)==expected,'Installed LiteLLM source drift')
    config=ROOT/'.devcontainer/devcontainer.json'
    values=re.findall(r'"ANTHROPIC_(?:API_BASE|BASE_URL)"\s*:\s*"([^"]+)"',config.read_text())
    require(len(values)==2 and set(values)=={EXPECTED_BASE},'Supplied route configuration changed')
    return dict(kind='real_route_streaming_probe_preflight',prepared_only=True,model_requests_sent=0,
        model=MODEL,route=EXPECTED_BASE+'/v1/messages',thinking={'type':'adaptive'},output_config={'effort':'max'},
        max_tokens=64000,max_model_requests=MAX_REQUESTS,total_wall_seconds=TOTAL_WALL_SECONDS,
        connect_timeout_seconds=15,read_idle_timeout_seconds=90,retries=0,tool_executions=0,
        credentials='Loaded only on explicit run, from ROOT/.env TAKE_HOME_TOKEN like run-candidate-screen.py; never persisted',
        source_sha256={str(p.relative_to(ROOT)):digest(p) for p in
            [Path(__file__),BASE/'source-identity.json',BASE/'execution-identity.json',BASE/'baseline-proof.json',
             correction/'proof.json',correction/'checked_anthropic_iterator.py',config]},
        exact_litellm_source_files=len(info['litellm_file_sha256']),execution_python=sys.version,
        pricing='Provider usage retained; no authoritative charge inference or synthetic tariff',
        scope='Short SSE and signed-block round-trip acceptance only; not gateway/deadline/concurrency proof')


class Evidence:
    def __init__(self,output,inputs):
        self.output=output;self.started=time.monotonic();self.active_turn=0
        self.allow_connect=False;self.network_thread=threading.get_ident();self.responses=[];self.client=None
        self.raw_provider_blocks={1:[],2:[]}
        self.state=dict(schema_version=1,kind='bounded_real_route_streaming_probe',status='starting',started_at=now(),
            passed=False,model_requests_sent=0,tool_executions=0,task_launches=0,retries=0,
            inputs=inputs,turns={},event_count=0,blocked_network_attempts=0,
            signed_roundtrip_status='not_attempted',provider_charge_usd=None,
            pricing_note='Mini local cost, if present, is an unverified estimate; provider charge unknown.')
        self.save()
    def save(self):dump(self.output/'result.json',self.state)
    def event(self,typ,details=None):
        self.state['event_count']+=1
        require(self.state['event_count']<=MAX_EVENTS,'Event bound exceeded')
        row=dict(at=now(),elapsed_seconds=round(time.monotonic()-self.started,6),turn=self.active_turn,event=typ)
        row.update(details or {})
        with (self.output/'events.jsonl').open('a') as out:out.write(json.dumps(row,sort_keys=True)+'\n')
    def network_guard(self,request):
        require(str(request.url)==EXPECTED_BASE+'/v1/messages' and request.method=='POST','Non-model route blocked')
        require(self.state['model_requests_sent']<MAX_REQUESTS,'Two-request limit reached')
        body=json.loads(request.content)
        require(body.get('model')=='claude-fable-5-1' and body.get('stream') is True
            and body.get('thinking')=={'type':'adaptive'} and body.get('output_config')=={'effort':'max'}
            and body.get('max_tokens')==64000,'Wire model settings differ')
        require(len(body.get('tools',[]))==1 and body['tools'][0]['name']=='bash','Unexpected tool schema')
        number=self.state['model_requests_sent']+1
        require(number==self.active_turn,'Unexpected retry or extra model request')
        turns=self.state['turns'];row=turns.setdefault(str(number),{})
        row.update(request_started_at=now(),request_body_sha256=hashlib.sha256(request.content).hexdigest(),
            request_bytes=len(request.content),wire_settings_verified=True)
        if number==2:
            assistants=[m for m in body['messages'] if m.get('role')=='assistant']
            require(len(assistants)==1,'Second request must contain exactly the first assistant response')
            content=assistants[0].get('content',[]);require(isinstance(content,list),'Native assistant blocks missing')
            replay=signed_projection(content);row['replayed_blocks']=replay
            first=turns['1']['provider_blocks']
            require(replay['signed_blocks_sha256']==first['signed_blocks_sha256']
                and replay['ordered_thinking_blocks_sha256']==first['ordered_thinking_blocks_sha256'],
                'Thinking blocks changed before second request')
        self.state['model_requests_sent']=number;self.state['status']='request_in_flight';self.save()
        self.event('http_send',dict(request_number=number))


def execute(output,inputs):
    require(output.parent==HERE and output.name.startswith('attempt-'),'Use a fresh attempt-* directory under this probe')
    require(not output.exists(),'Never overwrite a probe attempt')
    output.mkdir(mode=0o700)
    os.umask(0o077)
    evidence=Evidence(output,inputs)
    def terminate(signum,frame):
        evidence.state.update(status='wall_timeout' if signum==signal.SIGALRM else 'terminated',
            finished_at=now(),passed=False,received_signal=signum)
        evidence.save()
        os._exit(124 if signum==signal.SIGALRM else 128+signum)
    signal.signal(signal.SIGALRM,terminate);signal.signal(signal.SIGTERM,terminate)
    signal.setitimer(signal.ITIMER_REAL,TOTAL_WALL_SECONDS)
    # All output from libraries is discarded: exceptions may contain headers or keys.
    with open(os.devnull,'w') as quiet,redirect_stdout(quiet),redirect_stderr(quiet):
        try:
            logging.disable(logging.CRITICAL)
            from dotenv import load_dotenv
            load_dotenv(ROOT/'.env')
            token=os.environ.get('TAKE_HOME_TOKEN');require(bool(token),'Missing configured credential')
            for key in ('ANTHROPIC_API_BASE','ANTHROPIC_BASE_URL'):
                require(os.environ.get(key,EXPECTED_BASE).rstrip('/')==EXPECTED_BASE,'Configured route mismatch')
            os.environ['ANTHROPIC_API_KEY']=token
            os.environ['MSWEA_GLOBAL_CONFIG_DIR']=str(output/'isolated-mini-config')
            os.environ['MSWEA_SILENT_STARTUP']='1'
            os.environ['MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT']='1'
            os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
            # Block import-time/background networking. Only the guarded sync send
            # temporarily authorizes sockets; the explicit transport has retries=0.
            original_connect=socket.socket.connect;original_connect_ex=socket.socket.connect_ex
            def connect(sock,address):
                if not evidence.allow_connect or threading.get_ident()!=evidence.network_thread:
                    evidence.state['blocked_network_attempts']+=1;raise RuntimeError('Unrelated network blocked')
                return original_connect(sock,address)
            def connect_ex(sock,address):
                if not evidence.allow_connect or threading.get_ident()!=evidence.network_thread:
                    evidence.state['blocked_network_attempts']+=1;raise RuntimeError('Unrelated network blocked')
                return original_connect_ex(sock,address)
            with patch.object(socket.socket,'connect',connect),patch.object(socket.socket,'connect_ex',connect_ex):
                import httpx
                import litellm
                litellm.telemetry=False;litellm.cache=None
                litellm.callbacks=[];litellm.success_callback=[];litellm.failure_callback=[]
                litellm.suppress_debug_info=True;litellm.num_retries=0
                sys.path.insert(0,str(BASE/'source-snapshot'))
                sys.path.insert(0,str(BASE/'isolated-correction'))
                from minisweagent.models.litellm_model import LitellmModel
                from litellm.llms.custom_httpx.http_handler import HTTPHandler
                from litellm.llms.anthropic.chat import handler
                from checked_anthropic_iterator import CheckedAnthropicIterator,offline_checked_iterator
                class TimedIterator(CheckedAnthropicIterator):
                    def __init__(self,*args,**kwargs):
                        super().__init__(*args,**kwargs);self.original_blocks={}
                    def chunk_parser(self,chunk):
                        typ=chunk.get('type');index=chunk.get('index')
                        details=dict(payload_sha256=hashed(chunk))
                        if type(index)is int:details['block_index']=index
                        if typ=='message_start':details['usage']=numeric_usage(chunk.get('message',{}).get('usage'))
                        elif typ=='message_delta':details['usage']=numeric_usage(chunk.get('usage'))
                        evidence.event(typ if typ in {'message_start','message_delta','message_stop','content_block_start','content_block_delta','content_block_stop','ping','error'} else 'unknown_provider_event',details)
                        if typ=='content_block_start':
                            block=chunk['content_block']
                            if block['type']=='thinking':self.original_blocks[index]=dict(type='thinking',thinking=block.get('thinking',''),signature=block.get('signature',''))
                            elif block['type']=='redacted_thinking':self.original_blocks[index]=dict(type='redacted_thinking',data=block['data'])
                        elif typ=='content_block_delta' and index in self.original_blocks:
                            delta=chunk['delta'];block=self.original_blocks[index]
                            if delta['type']=='thinking_delta':block['thinking']+=delta['thinking']
                            elif delta['type']=='signature_delta':block['signature']+=delta['signature']
                        elif typ=='content_block_stop' and index in self.original_blocks:
                            evidence.raw_provider_blocks[evidence.active_turn].append(self.original_blocks.pop(index))
                        result=super().chunk_parser(chunk)
                        if typ=='message_stop':
                            row=evidence.state['turns'][str(evidence.active_turn)]
                            row['provider_blocks']=signed_projection(evidence.raw_provider_blocks[evidence.active_turn])
                            row['provider_message_stop_at']=now();evidence.save()
                        return result
                client=httpx.Client(transport=httpx.HTTPTransport(retries=0),follow_redirects=False,
                    timeout=httpx.Timeout(90,connect=15,write=30,pool=15))
                evidence.client=client
                http=HTTPHandler(client=client)
                original_send=httpx.Client._send_single_request
                def send(client_self,request):
                    require(client_self is client,'Unexpected HTTP client blocked')
                    evidence.network_guard(request)
                    evidence.allow_connect=True
                    try:response=original_send(client_self,request)
                    finally:evidence.allow_connect=False
                    evidence.responses.append(response)
                    evidence.event('http_status',dict(status_code=response.status_code))
                    evidence.state['turns'][str(evidence.active_turn)]['http_status']=response.status_code
                    evidence.save();return response
                with offline_checked_iterator(),patch.object(handler,'ModelResponseIterator',TimedIterator),patch.object(httpx.Client,'_send_single_request',send):
                    model=LitellmModel(model_name=MODEL,cost_tracking='ignore_errors',model_kwargs=dict(
                        api_base=EXPECTED_BASE,api_key=token,client=http,drop_params=True,
                        stream=True,complete_response=True,thinking={'type':'adaptive'},output_config={'effort':'max'},
                        max_tokens=64000,num_retries=0,timeout=httpx.Timeout(90,connect=15,write=30,pool=15)))
                    messages=[{'role':'user','content':PROMPT}]
                    for turn in (1,2):
                        evidence.active_turn=turn
                        message=model.query(messages)
                        raw=message['extra']['response'];choice=raw['choices'][0]
                        row=evidence.state['turns'][str(turn)]
                        require(row.get('provider_message_stop_at'),'Missing explicit stream completion')
                        projection=signed_projection(message.get('thinking_blocks'))
                        require(projection==row['provider_blocks'],'Provider thinking blocks changed during aggregation')
                        row.update(completed_at=now(),response_sha256=hashed(raw),mini_blocks=projection,
                            finish_reason=choice['finish_reason'],usage=numeric_usage(raw.get('usage')),
                            mini_recorded_cost=message['extra'].get('cost'),content_sha256=hashed(message.get('content')),
                            tool_actions_sha256=hashed(message['extra']['actions']))
                        actions=message['extra']['actions']
                        require(len(actions)==1 and actions[0]['command']==COMMAND,'Expected fixed harmless tool call absent')
                        row['fixed_tool_call_matches']=True
                        require(choice['finish_reason']=='tool_calls','Probe response did not finish with a tool call')
                        evidence.event('mini_response_accepted',dict(request_number=turn));evidence.save()
                        if turn==1:
                            messages.extend([message,{'role':'tool','tool_call_id':actions[0]['tool_call_id'],'content':OBSERVATION},
                                {'role':'user','content':'The observation was synthetic. Repeat exactly the same fixed bash tool call once; no command will be executed.'}])
                    first=evidence.state['turns']['1']['provider_blocks']
                    evidence.state['signed_roundtrip_status']='accepted' if first['signed_count'] else 'inconclusive_no_signed_thinking'
                    evidence.state.update(status='completed',short_sse_completed=True,passed=first['signed_count']>0)
        except BaseException as error:
            # Do not serialize exception text, traceback, response, request, or headers.
            status=getattr(error,'status_code',None)
            evidence.state.update(status='failed',error_type=type(error).__name__,
                error_http_status=status if type(status)is int else None,passed=False)
        finally:
            cleanup=[]
            for response in evidence.responses:
                try:response.close()
                except BaseException as error:cleanup.append(type(error).__name__)
            if evidence.client is not None:
                try:evidence.client.close()
                except BaseException as error:cleanup.append(type(error).__name__)
            evidence.state.update(finished_at=now(),elapsed_seconds=round(time.monotonic()-evidence.started,3),
                http_cleanup_errors=cleanup,http_responses_closed=all(r.is_closed for r in evidence.responses),
                provider_charge_usd=None)
            if cleanup:evidence.state['passed']=False
            evidence.save()
    signal.setitimer(signal.ITIMER_REAL,0)
    files={str(p.relative_to(output)):digest(p) for p in output.rglob('*') if p.is_file()}
    dump(output/'artifact-manifest.json',dict(kind='sanitized_real_route_probe_artifacts',file_sha256=files,
        raw_thinking_stored=False,signatures_stored=False,headers_stored=False,model_requests_sent=evidence.state['model_requests_sent']))
    return 0 if evidence.state['status']=='completed' else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--check-only',action='store_true');group.add_argument('--run',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();inputs=preflight()
    if args.check_only:
        require(args.output is None,'check-only does not create execution output');print(json.dumps(inputs,indent=2),flush=True);return 0
    require(args.output is not None,'Explicit fresh output path required')
    code=execute(args.output.resolve(),inputs)
    print(json.dumps(dict(result=str(args.output/'result.json'),exit_code=code)),flush=True)
    return code

if __name__=='__main__':
    try:code=main()
    except BaseException as error:
        print(json.dumps(dict(status='preflight_or_setup_failed',error_type=type(error).__name__)),flush=True);code=1
    # All result files and HTTP clients are handled before terminating our own
    # process. Do not let third-party background logging extend the wall bound.
    os._exit(code)
