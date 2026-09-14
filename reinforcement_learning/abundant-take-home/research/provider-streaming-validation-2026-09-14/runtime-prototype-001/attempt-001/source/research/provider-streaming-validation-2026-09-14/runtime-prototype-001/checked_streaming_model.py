"""Opt-in synchronous transport prototype; all other Mini behavior is inherited."""
import copy
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch

from minisweagent.models.litellm_model import LitellmModel
from litellm.llms.custom_httpx.http_handler import HTTPHandler
from checked_anthropic_iterator import offline_checked_iterator

EVENT_PATH = None
_QUERY_LOCK = threading.Lock()


def event(kind, **values):
    if EVENT_PATH is not None:
        with Path(EVENT_PATH).open('a') as output:
            output.write(json.dumps(dict(event=kind, at_epoch=time.time(), **values), sort_keys=True) + '\n')


class CheckedStreamingModel(LitellmModel):
    """Keep Mini query/retry/cost/trajectory/action semantics; aggregate checked SSE."""

    def __init__(self, **kwargs):
        kwargs = dict(kwargs)
        model_kwargs = dict(kwargs.get('model_kwargs') or {})
        kwargs['model_kwargs'] = model_kwargs
        if kwargs.get('model_name') != 'anthropic/claude-fable-5-1':
            raise ValueError('This prototype is limited to the reviewed Fable route')
        if (model_kwargs.get('thinking') != {'type': 'adaptive'} or
                model_kwargs.get('output_config') != {'effort': 'max'} or
                model_kwargs.get('max_tokens') != 64000):
            raise ValueError('Prototype requires the unchanged reviewed model settings')
        for key in ('stream', 'complete_response'):
            if key in model_kwargs and model_kwargs[key] is not True:
                raise ValueError('Conflicting explicit streaming configuration')
            model_kwargs[key] = True
        super().__init__(**kwargs)

    def _query(self, messages, **kwargs):
        for key in ('stream', 'complete_response'):
            if key in kwargs and kwargs[key] is not True:
                raise ValueError('Per-query streaming override is unsupported')
        if not _QUERY_LOCK.acquire(blocking=False):
            raise RuntimeError('Streaming prototype requires one synchronous query at a time')
        responses = []
        original_post = HTTPHandler.post

        def capture_post(handler, *args, **options):
            response = original_post(handler, *args, **options)
            responses.append(response)
            event('http_response_opened', response_number=len(responses))
            return response

        try:
            event('query_entered')
            # The reviewed context only changes the decoded-event iterator and
            # thinking-block aggregation; it adds no socket/client/retry policy.
            with offline_checked_iterator(), patch.object(HTTPHandler, 'post', capture_post):
                response = super()._query(messages, **kwargs)
            event('complete_response_returned')
            return response
        finally:
            errors = []
            for response in responses:
                try:
                    response.close()
                except BaseException as error:
                    errors.append(type(error).__name__)
            _QUERY_LOCK.release()
            event('query_resources_closed', response_count=len(responses),
                  all_closed=all(response.is_closed for response in responses), error_types=errors)
            if errors:
                raise RuntimeError('Streaming response cleanup failed')
