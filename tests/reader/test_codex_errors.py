"""Codex failures and stopped responses satisfy the strict reader HTTP contract."""
import asyncio

import pytest

from oko.processing.contracts import ProcessingError
from tests.support.app import create_app
from tests.support.codex import CodexService
from tests.support.v3 import TestClient

HEADERS = {'Authorization': 'Bearer test'}
CONFIGURATION = {name: {'model': 'synthetic', 'reasoning_effort': 'low', 'fast': False} for name in ('filter', 'theses')}
OPERATIONS = [
    ('GET', '/api/v3/processors/codex', None),
    ('GET', '/api/v3/processors/codex/models', None),
    ('PUT', '/api/v3/processors/codex', CONFIGURATION),
    ('POST', '/api/v3/processors/codex/check', None),
]


@pytest.mark.parametrize('method,path,body', [OPERATIONS[1], OPERATIONS[2]])
def test_catalog_failure_has_processor_error_and_preserves_configuration(tmp_path, method, path, body):
    class Unavailable(CodexService):
        async def catalog(self):
            raise ProcessingError('Synthetic catalog unavailable')

    with TestClient(create_app(tmp_path, token='test', api_only=True, codex_client=Unavailable()), headers=HEADERS) as client:
        before = client.get('/api/v3/processors/codex').json()
        response = client.request(method, path, headers={'X-Oko-Action': 'catalog-failure'}, **({'json': body} if body else {}))
        assert response.status_code == 503
        assert response.json()['code'] == 'processor_unavailable'
        assert client.get('/api/v3/processors/codex').json() == before
        if method == 'PUT':
            receipt = client.get('/api/v3/commands/catalog-failure').json()
            assert receipt['outcome'] == 'failed'
            assert receipt['error'] == response.json()


def test_interrupted_codex_check_has_processor_error_and_failed_receipt(tmp_path):
    class Interrupted(CodexService):
        async def check(self):
            raise asyncio.CancelledError()

    with TestClient(create_app(tmp_path, token='test', api_only=True, codex_client=Interrupted()), headers=HEADERS) as client:
        before = client.get('/api/v3/processors/codex').json()
        response = client.post('/api/v3/processors/codex/check', headers={'X-Oko-Action': 'interrupted-check'})
        assert response.status_code == 503
        assert response.json()['code'] == 'processor_unavailable'
        assert client.get('/api/v3/processors/codex').json() == before
        receipt = client.get('/api/v3/commands/interrupted-check').json()
        assert receipt['outcome'] == 'failed'
        assert receipt['error'] == response.json()
