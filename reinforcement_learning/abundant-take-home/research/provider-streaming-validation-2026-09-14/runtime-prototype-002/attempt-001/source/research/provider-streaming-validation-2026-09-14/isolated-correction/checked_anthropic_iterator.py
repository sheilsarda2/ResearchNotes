"""Isolated synchronous candidate for Mini's native Anthropic streaming path.

Reuses LiteLLM's HTTP/SSE/JSON decoder. It normalizes decoded thinking blocks
and rejects semantic truncation. No network implementation or deployment hook.
The explicit context manager is for single-process OFFLINE experiments only.
"""
import hashlib
import inspect
import copy
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import importlib.metadata
from litellm.llms.anthropic.chat import handler
from litellm.litellm_core_utils.streaming_chunk_builder_utils import ChunkProcessor

BASE_PROCESSOR_SHA256='467534a991264bcb054b91684cde0271116362210751379f898578d894df61d1'
BASE_HANDLER_SHA256='bc9fa84a5bef436b4bef7374c584884232b6456e339bf06a6494c9a2b2f49d1a'
OriginalIterator=handler.ModelResponseIterator
OriginalThinkingBuilder=ChunkProcessor.get_combined_thinking_content

class IncompleteAnthropicStream(RuntimeError):
    pass

class CheckedAnthropicIterator(OriginalIterator):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if not kwargs.get('sync_stream', args[1] if len(args)>1 else False):
            raise ValueError('Candidate covers synchronous Mini calls only')
        self._message_started=False
        self._message_stopped=False
        self._final_usage_received=False
        self._open_blocks={}
        self._seen_blocks=set()
        self._signed_thinking={}

    def chunk_parser(self,chunk):
        typ=chunk.get('type')
        if self._message_stopped and typ!='ping':
            raise IncompleteAnthropicStream('Event after message_stop')
        if typ=='message_start':
            if self._message_started:raise IncompleteAnthropicStream('Duplicate message_start')
            usage=chunk.get('message',{}).get('usage',{})
            if type(usage.get('input_tokens')) is not int or usage['input_tokens']<0:
                raise IncompleteAnthropicStream('Missing initial input usage')
            self._message_started=True
        elif typ not in ('ping','error') and not self._message_started:
            raise IncompleteAnthropicStream('Event before message_start')
        if typ=='content_block_start':
            index=chunk['index']
            if self._final_usage_received or index in self._seen_blocks or self._open_blocks:
                raise IncompleteAnthropicStream('Invalid content block ordering')
            self._seen_blocks.add(index)
            block=chunk['content_block'];self._open_blocks[index]=block['type']
            if block['type']=='thinking':
                self._signed_thinking[index]={'text':[block.get('thinking','')], 'signature':[block.get('signature','')]}
        elif typ=='content_block_delta':
            index=chunk['index']
            if index not in self._open_blocks:raise IncompleteAnthropicStream('Delta for closed content block')
            if index in self._signed_thinking:
                delta=chunk['delta'];state=self._signed_thinking[index]
                if delta['type']=='thinking_delta':state['text'].append(delta['thinking'])
                elif delta['type']=='signature_delta':state['signature'].append(delta['signature'])
        elif typ=='content_block_stop':
            if chunk['index'] not in self._open_blocks:raise IncompleteAnthropicStream('Stop for closed content block')
        elif typ=='message_delta':
            usage=chunk.get('usage',{})
            if self._open_blocks or self._final_usage_received or not chunk.get('delta',{}).get('stop_reason'):
                raise IncompleteAnthropicStream('Missing final stop reason or unfinished content')
            if type(usage.get('output_tokens')) is not int or usage['output_tokens']<0:
                raise IncompleteAnthropicStream('Missing final output usage')
            self._final_usage_received=True
        elif typ=='message_stop':
            if self._open_blocks or not self._final_usage_received:
                raise IncompleteAnthropicStream('message_stop before final usage or content completion')
            self._message_stopped=True

        result=super().chunk_parser(chunk)
        # A thinking block is emitted exactly once, only after its complete text
        # and signature are known. Do not forward the handler's repeated payload.
        index=chunk.get('index')
        if typ in ('content_block_start','content_block_delta') and index in self._signed_thinking:
            delta=result.choices[0].delta
            delta.thinking_blocks=None
            fields=dict(delta.provider_specific_fields or {})
            fields.pop('thinking_blocks',None)
            delta.provider_specific_fields=fields or None
        elif typ=='content_block_stop':
            self._open_blocks.pop(index)
            if index in self._signed_thinking:
                state=self._signed_thinking.pop(index)
                signature=''.join(state['signature'])
                if not signature:raise IncompleteAnthropicStream('Thinking block lacks signature')
                canonical=[{'type':'thinking','thinking':''.join(state['text']),'signature':signature}]
                delta=result.choices[0].delta
                delta.thinking_blocks=canonical
                fields=dict(delta.provider_specific_fields or {})
                fields['thinking_blocks']=canonical
                delta.provider_specific_fields=fields
        return result

    def __next__(self):
        try:
            result=super().__next__()
            # CustomStreamWrapper stops after finish_reason. Hold that chunk
            # until the existing parser has decoded message_stop, or raise.
            if getattr(result,'choices',None) and result.choices[0].finish_reason:
                while not self._message_stopped:super().__next__()
            return result
        except StopIteration:
            if not (self._message_started and self._final_usage_received and self._message_stopped) or self._open_blocks:
                raise IncompleteAnthropicStream('SSE ended before complete message and final provider usage') from None
            raise


def complete_thinking_blocks(processor,chunks):
    # This iterator emits complete signed blocks. Preserve even an empty signed
    # text block; the generic concatenator drops it through a truthiness check.
    blocks=[block for chunk in chunks for choice in chunk['choices']
            for block in (choice.get('delta',{}).get('thinking_blocks') or [])]
    if blocks and all(block.get('type')=='redacted_thinking' or
            ('thinking' in block and bool(block.get('signature'))) for block in blocks):
        return copy.deepcopy(blocks)
    return OriginalThinkingBuilder(processor,chunks)

@contextmanager
def offline_checked_iterator():
    if (importlib.metadata.version('litellm')!='1.100.1'
        or hashlib.sha256(Path(handler.__file__).read_bytes()).hexdigest()!=BASE_HANDLER_SHA256
        or hashlib.sha256(Path(inspect.getsourcefile(ChunkProcessor)).read_bytes()).hexdigest()!=BASE_PROCESSOR_SHA256):
        raise RuntimeError('Unreviewed LiteLLM source; candidate is version bound')
    with patch.object(handler,'ModelResponseIterator',CheckedAnthropicIterator), patch.object(ChunkProcessor,'get_combined_thinking_content',complete_thinking_blocks):
        yield
