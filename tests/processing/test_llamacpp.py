"""Malformed model responses at the adapter's HTTP boundary, without a reader."""
import asyncio
import json

import httpx
import pytest

from oko.processing.contracts import InputSource, ProcessingError, ProcessingRequest
from oko.processing.llamacpp import LlamaCpp, ProcessorConfig
from tests.support.models import model_transport


@pytest.mark.parametrize('reply,label', [
    ({'choices': []}, 'llama.cpp не завершил результат'),
    ({'choices': [{'finish_reason': 'length', 'message': {'role': 'assistant', 'content': '{}'}}]}, 'llama.cpp не завершил результат'),
    ({'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{broken'}}]}, 'Результат не соответствует схеме обработки'),
    ({'error': 'secret prompt should never appear in UI'}, 'llama.cpp вернул ошибку'),
    ({'choices': [{'finish_reason': 'stop', 'message': []}]}, 'Результат не соответствует схеме обработки'),
    ({'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'tool_calls': [{'function': {'name': 'read_file'}}]}}]}, 'llama.cpp не завершил результат'),
])
def test_invalid_model_response_is_a_safe_processing_error(reply, label):
    calls = []
    def respond(request):
        assert request.url.path == '/v1/chat/completions'
        calls.append(request)
        return httpx.Response(200, json=reply)
    processor = LlamaCpp(ProcessorConfig(endpoint='http://127.0.0.1:8080', model='local:latest'), httpx.MockTransport(respond))
    request = ProcessingRequest(sources=(InputSource(id='post', version='1', url='https://source.test/post', title='Post', text='Source text'),),
                                settings_version=1, prompt='Тезисы', processor='llamacpp', generation=1)
    with pytest.raises(ProcessingError) as failure:
        asyncio.run(processor.generate(request))
    assert str(failure.value) == label
    assert 'secret prompt' not in str(failure.value)
    assert len(calls) == 1
    assert json.loads(calls[0].content)['stream'] is False


def test_valid_model_response_returns_attributed_theses():
    calls = []
    processor = LlamaCpp(ProcessorConfig(endpoint='http://127.0.0.1:8080', model='local:latest'), model_transport(calls))
    request = ProcessingRequest(sources=(InputSource(id='post', version='1', url='https://source.test/post', title='Post', text='Source text'),),
                                settings_version=1, prompt='Тезисы', processor='llamacpp', generation=1)
    result = asyncio.run(processor.generate(request))
    assert result.points[0].sources[0].id == 'post'
    assert result.points[0].sources[0].url == 'https://source.test/post'
    assert len(calls) == 1
