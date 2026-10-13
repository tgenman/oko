"""Scenario models reject processing that the scenario did not enable."""
import httpx
import pytest

from tests.support.filters import transport as filter_transport
from tests.support.models import model_transport


@pytest.mark.parametrize(('factory', 'operation'), [
    (filter_transport, 'theses'), (filter_transport, 'events'),
    (model_transport, 'filter'), (model_transport, 'events'),
])
def test_model_rejects_unconfigured_operation(factory, operation):
    calls = []
    request = httpx.Request('POST', 'http://localhost/v1/chat/completions', json={
        'messages': [{'content': ''}, {'content': '{"operation":"' + operation + '","sources":[]}'}]})
    with pytest.raises(AssertionError, match='does not support operation: ' + operation):
        factory(calls).handler(request)
    assert calls == []
