"""Processing consistency through the reader and replaceable external AI."""
import asyncio
import json
import threading
import time

import httpx
from fastapi.testclient import TestClient

from oko.reader.app import create_app
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
        elif task['operation'] == 'events':
            result = dict(groups=[dict(members=[s['id'] for s in sources], title='Два сообщения',
                                      points=[dict(text='Сводка двух сообщений', sources=[dict(id=s['id'], url=s['url']) for s in sources])],
                                      updates=[], conflicts=[])])
        else:
            result = dict(points=[dict(text=sources[0]['text'], sources=[dict(id=sources[0]['id'], url=sources[0]['url'])])])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result)}}]})

    return httpx.MockTransport(respond)


def test_finished_views_do_not_repeat_processing_on_unchanged_passes(tmp_path):
    calls = []
    options = dict(source=FilterSource(), token='test', ai_transport=coordinated_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True, 'events': True, 'prompt_filters': 'Убери рекламу.'})
        client.post('/api/tabs/reader/heartbeat')
        events = wait_for(client, lambda: cards if (cards := client.get('/api/cards').json()) and all(c.get('event') for c in cards) else None)
        original = events[0]['sources'][0]['id']
        wait_for(client, lambda: client.get('/api/materials/' + original).json().get('theses'))
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        result = wait_for(client, lambda: m if (m := client.get('/api/materials/' + identity).json()).get('digest') else None)
        wait_for(client, lambda: client.get('/api/status').json()['stage'] == 'Готово')
        assert {'filter', 'theses', 'events', 'digest'} <= {c['operation'] for c in calls}
        assert all('Реклама:' not in s['display_text'] for s in result['sources'] if not s['filter']['hidden'])
        count = len(calls)
        time.sleep(1.2)  # More than two periodic reconciliation passes.
        assert len(calls) == count
        assert client.get('/api/materials/' + identity).json()['digest'] == result['digest']
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client, lambda: client.get('/api/status').json()['stage'] == 'Готово')
        assert client.get('/api/materials/' + identity).json()['digest'] == result['digest']
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
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True, 'events': False})
        client.post('/api/tabs/reader/heartbeat')
        try:
            assert transport.started.wait(2)
            assert client.patch('/api/feeds/demo', json={'public_ai': False}).status_code == 200
        finally:
            transport.release.set()
        wait_for(client, lambda: client.get('/api/status').json()['errors'])
        assert not any(r.url.path == '/v1/chat/completions' for r in transport.calls)
        assert all(c.get('theses') is None for c in client.get('/api/cards').json())


def test_restore_hidden_original_rebuilds_review_and_theses_with_same_members(tmp_path):
    calls = []
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=coordinated_transport(calls)), headers=HEADERS) as client:
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True, 'events': True, 'prompt_filters': 'Убери рекламу.'})
        client.post('/api/tabs/reader/heartbeat')
        hidden = wait_for(client, lambda: next((c for c in client.get('/api/cards?show_hidden=true').json() if c.get('filter', {}).get('hidden')), None))
        # A filtered first publication does not mean the entire source has loaded.
        def all_loaded():
            cards = client.get('/api/cards?show_hidden=true').json()
            originals = {source['id'] for card in cards for source in card.get('sources', [card])}
            return len(originals) == 3 and all(card['complete'] and not card.get('loading') for card in cards)
        wait_for(client, all_loaded)
        created = client.post('/api/digests/folder/demo', json={})
        assert created.status_code == 200, created.json()
        identity = created.json()['id']
        before = wait_for(client, lambda: m if (m := client.get('/api/materials/' + identity).json()).get('digest') else None)
        count = len(calls)
        assert client.post('/api/materials/' + hidden['id'] + '/filter/restore').status_code == 200
        after = wait_for(client, lambda: m if (m := client.get('/api/materials/' + identity).json()).get('digest') and any(s['id'] == hidden['id'] and s['filter']['restored'] for s in m['sources']) else None)
        assert {s['id'] for s in before['sources']} == {s['id'] for s in after['sources']}
        wait_for(client, lambda: client.get('/api/materials/' + hidden['id']).json().get('theses'))
        assert any(c['operation'].startswith('digest') and any(s['id'] == hidden['id'] and s['text'] == 'Реклама: купите курс.' for s in c['sources']) for c in calls[count:])
        assert any(c['operation'] == 'theses' and c['sources'][0]['id'] == hidden['id'] for c in calls[count:])
