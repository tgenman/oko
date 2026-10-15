"""Processing consistency through the reader and replaceable external AI."""
import asyncio
import json
import threading
import time

import httpx
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.filters import FilterSource
from tests.support.models import model_transport
from tests.support.reader import HEADERS, wait_for


def coordinated_transport(calls):
    native = model_transport([])

    def respond(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        sources = task['sources']
        if task['operation'] == 'filter':
            text = sources[0]['text']
            result = dict(hidden=text.startswith('Реклама:'), reason='Реклама' if 'Реклама:' in text else '',
                          removed=['Реклама: купите курс.'] if text.startswith('Открылась') else [])
        else:
            result = dict(points=[dict(text=sources[0]['text'], sources=[dict(id=sources[0]['id'], url=sources[0]['url'])])])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result)}}]})

    return httpx.MockTransport(respond)


def test_finished_views_do_not_repeat_processing_on_unchanged_passes(tmp_path):
    calls = []
    options = dict(source=FilterSource(), token='test', ai_transport=coordinated_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True, 'prompt_filters': 'Убери рекламу.'})
        client.post('/api/tabs/reader/heartbeat')
        result = wait_for(client, lambda: cards if len(cards := client.get('/api/cards').json()) == 2 and all(c.get('theses') for c in cards) else None)
        wait_for(client, lambda: client.get('/api/status').json()['stage'] == 'Готово')
        assert {'filter', 'theses'} <= {c['operation'] for c in calls}
        assert all('Реклама:' not in c['snippet'] for c in result)
        count = len(calls)
        time.sleep(1.2)  # More than two periodic reconciliation passes.
        assert len(calls) == count
        assert [c['theses'] for c in client.get('/api/cards').json()] == [c['theses'] for c in result]
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client, lambda: client.get('/api/status').json()['stage'] == 'Готово')
        assert [c['theses'] for c in client.get('/api/cards').json()] == [c['theses'] for c in result]
        assert len(calls) == count


def test_permission_revoked_during_processor_check_prevents_source_transfer(tmp_path):
    class Checking(httpx.AsyncBaseTransport):
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.calls = []
            self.native = model_transport(self.calls)

        async def handle_async_request(self, request):
            if request.url.path == '/props':
                self.started.set()
                while not self.release.is_set():
                    try:
                        await asyncio.sleep(.01)
                    except asyncio.CancelledError:
                        pass
            return self.native.handler(request)

    transport = Checking()
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=transport), headers=HEADERS) as client:
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': False})
        client.patch('/api/feeds/demo', json={'public_ai': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        client.post('/api/tabs/reader/heartbeat')
        try:
            assert transport.started.wait(2)
            assert client.patch('/api/feeds/demo', json={'public_ai': False}).status_code == 200
        finally:
            transport.release.set()
        wait_for(client, lambda: client.get('/api/status').json()['errors'])
        assert not any(r.url.path == '/v1/chat/completions' for r in transport.calls)
        assert all(c.get('theses') is None for c in client.get('/api/cards').json())


def test_restore_hidden_original_creates_its_theses(tmp_path):
    calls = []
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=coordinated_transport(calls)), headers=HEADERS) as client:
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True, 'prompt_filters': 'Убери рекламу.'})
        client.post('/api/tabs/reader/heartbeat')
        hidden = wait_for(client, lambda: next((c for c in client.get('/api/cards?show_hidden=true').json() if c.get('filter', {}).get('hidden')), None))
        assert hidden.get('theses') is None
        assert client.post('/api/materials/' + hidden['id'] + '/filter/restore').status_code == 200
        material = wait_for(client, lambda: m if (m := client.get('/api/materials/' + hidden['id']).json()).get('theses') else None)
        assert material['filter']['restored']
        assert any(c['operation'] == 'theses' and c['sources'][0]['id'] == hidden['id'] for c in calls)


def test_original_presentation_inherits_independently_of_theses(tmp_path):
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=coordinated_transport([])), headers=HEADERS) as client:
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'originals': False, 'theses': True})
        client.post('/api/tabs/reader/heartbeat')
        material = wait_for(client, lambda: next((c for c in client.get('/api/cards').json() if c.get('theses')), None))
        current = client.get('/api/materials/' + material['id']).json()
        assert current['originals'] is False and current['text']
        client.patch('/api/settings/feed/demo', json={'originals': True})
        assert client.get('/api/materials/' + material['id']).json()['originals'] is True
        assert client.get('/api/settings/feed/demo').json()['effective']['theses']['value'] is True


def test_folder_completion_includes_hidden_publications(tmp_path):
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=coordinated_transport([])), headers=HEADERS) as client:
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': False, 'prompt_filters': 'Убери рекламу.'})
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client, lambda: len(c := client.get('/api/cards?show_hidden=true').json()) == 3 and all(x.get('filter', {}).get('status') == 'done' for x in c))
        assert len(client.get('/api/cards').json()) == 2
        preview = client.post('/api/folders/demo/completion/preview').json()
        assert preview['cards'] == preview['originals'] == 3
        assert client.post('/api/folders/demo/completion', json={'token': preview['token']}).status_code == 200
        assert client.get('/api/cards?show_hidden=true').json() == []
