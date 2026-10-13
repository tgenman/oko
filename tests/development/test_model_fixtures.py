"""Synthetic model contracts reject unrelated processing operations immediately."""
import httpx
import pytest

from tests.support.events import event_transport
from tests.support.digests import digest_transport


@pytest.mark.parametrize('factory', [event_transport, digest_transport])
@pytest.mark.parametrize('operation', ['theses', 'filter'])
def test_scenario_model_rejects_unrelated_operation(factory, operation):
    calls = []
    request = httpx.Request('POST', 'http://localhost/v1/chat/completions', json={
        'messages': [{'content': ''}, {'content': '{"operation":"' + operation + '","sources":[]}'}]})
    with pytest.raises(AssertionError, match='does not support operation: ' + operation):
        factory(calls).handler(request)
    assert calls == []


def test_combined_digest_and_theses_model_explicitly_supports_theses():
    import json
    calls = []
    request = httpx.Request('POST', 'http://localhost/v1/chat/completions', json={
        'messages': [{'content': ''}, {'content': json.dumps({'operation': 'theses', 'sources': [{'id': 'one', 'url': 'https://source.test/one'}]})}]})
    response = digest_transport(calls, theses=True).handler(request)
    assert response.status_code == 200
    assert calls[0]['operation'] == 'theses'
    assert json.loads(response.json()['choices'][0]['message']['content'])['points'][0]['sources'][0]['id'] == 'one'
