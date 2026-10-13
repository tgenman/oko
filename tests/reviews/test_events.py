from tests.support.sources import EventSource
from tests.support.events import event_transport, configure
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from oko.reader.app import create_app
from oko.collection.contracts import Material, Publication
from tests.support.reader import HEADERS, wait_for
from tests.support.models import model_transport


def test_event_has_exact_sources_updates_conflicts_and_deletes_only_members(tmp_path):
    calls = []
    options = dict(source=EventSource(), token='test', ai_transport=event_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure(client)
        cards = wait_for(client, lambda: c if len(c := client.get('/api/cards').json()) == 2 and any(x.get('event') for x in c) else None)
        event = next(c for c in cards if c.get('event'))
        assert event['published'] == 1800000060
        material = client.get('/api/materials/' + event['id']).json()
        assert {s['url'] for s in material['sources']} == {'https://example.org/event/0', 'https://example.org/event/1'}
        assert material['event']['updates'][0]['text'] == 'Мэрия назвала вместимость 30 мест.'
        assert len(material['event']['conflicts'][0]['sources']) == 2
        # Two cards represent three publications: two in the event and one standalone.
        assert sum(f['count'] for f in client.get('/api/folders').json()) == 3
        assert client.post('/api/materials/' + event['id'] + '/viewed').status_code == 200
        assert client.get('/api/materials/' + event['id']).status_code == 404
        assert len(client.get('/api/cards').json()) == 1
        for source in material['sources']:
            assert client.get('/api/materials/' + source['id']).status_code == 404
            assert not (tmp_path / 'content' / source['id']).exists()
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert len(client.get('/api/cards').json()) == 1
        client.post('/api/tabs/reader/heartbeat')
        client.post('/api/collect')
        assert len(client.get('/api/cards').json()) == 1
        assert not list((tmp_path / 'events').glob('*.md'))


def test_window_is_configurable_and_restart_keeps_exact_event(tmp_path):
    class Spaced(EventSource):
        async def discover(self, cursor):
            return ([Publication(str(i), f'https://example.org/event/{i}', 1800000000+i*25*3600) for i in range(3)] if cursor is None else []), '3'
    calls = []
    options = dict(source=Spaced(), token='test', ai_transport=event_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure(client)
        assert client.get('/api/settings/oko/global').json()['effective']['event_hours']['value'] == 24
        assert len(client.get('/api/cards').json()) == 3
        assert calls == []
        client.patch('/api/settings/oko/global', json={'event_hours': 26})
        event = wait_for(client, lambda: next((c for c in client.get('/api/cards').json() if c.get('event')), None))
        assert event['published'] == 1800090000
        identity = event['id']
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/materials/' + identity).json()['event'] == event['event']
        client.post('/api/tabs/reader/heartbeat')
        client.patch('/api/settings/oko/global', json={'event_hours': 24})
        assert client.get('/api/materials/' + identity).status_code == 404
        assert len(client.get('/api/cards').json()) == 3
        wait_for(client, lambda: not list((tmp_path / 'events').glob('*.md')))


def test_cross_feed_permissions_folder_boundary_importance_and_sort(tmp_path):
    from tests.support.codex import CodexService, configure_codex
    from tests.support.rss import rss
    class EventCodex(CodexService):
        async def complete(self, prompt, schema, directory, choice):
            self.inputs.append(prompt)
            task = json.loads(prompt.split('\n', 1)[1])
            sources = task['sources']
            return json.dumps(dict(groups=[dict(members=[s['id'] for s in sources], title='Библиотека', points=[dict(text='Открылась библиотека', sources=[dict(id=s['id'], url=s['url']) for s in sources])], updates=[], conflicts=[])]))
    service = EventCodex()
    def reply(request):
        if request.url.path.endswith('/feed'):
            return httpx.Response(200, text=rss('https://' + request.url.host + '/article'))
        return httpx.Response(200, text='<article>Открылась библиотека</article>')
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply), codex_client=service), headers=HEADERS) as client:
        configure_codex(client)
        feeds = []
        for name, folder in [('a', 'Новости'), ('b', 'Новости'), ('c', 'Отдельно')]:
            feeds.append(client.post('/api/import', json={'opml': f'<opml><body><outline text="{folder}"><outline text="{name}" xmlUrl="https://{name}.test/feed"/></outline></body></opml>'}).json()['feeds'][-1])
        feeds = sorted(client.get('/api/subscriptions').json()['feeds'], key=lambda f: f['title'])
        client.patch('/api/feeds/' + feeds[0]['id'], json={'public_ai': True})
        client.patch('/api/feeds/' + feeds[1]['id'], json={'importance': 'high'})
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client, lambda: len(c := client.get('/api/cards').json()) == 3 and all(x['complete'] and not x.get('loading') for x in c))
        wait_for(client, lambda: any('всех фидов' in error for error in client.get('/api/status').json()['errors']))
        assert service.inputs == []
        client.patch('/api/feeds/' + feeds[1]['id'], json={'public_ai': True})
        cards = wait_for(client, lambda: c if len(c := client.get('/api/cards').json()) == 2 and any(x.get('event') for x in c) else None)
        event = next(c for c in cards if c.get('event'))
        assert event['importance'] == 'high'
        assert {s['url'] for s in event['sources']} == {'https://a.test/article', 'https://b.test/article'}
        assert len(json.loads(service.inputs[-1].split('\n', 1)[1])['sources']) == 2
        assert client.get('/api/cards?sort=importance').json()[0]['id'] == event['id']
        newest = client.get('/api/cards?sort=newest').json()
        oldest = client.get('/api/cards?sort=oldest').json()
        assert [c['published'] for c in newest] == sorted([c['published'] for c in newest], reverse=True)
        assert [c['published'] for c in oldest] == sorted([c['published'] for c in oldest])
        assert client.get('/api/cards?sort=newest').json() == newest
        assert len(client.get('/api/cards?folder=' + feeds[2]['folder_id']).json()) == 1


def test_new_publication_rebuilds_event_without_reusing_deleted_semantic_history(tmp_path):
    class Growing(EventSource):
        added = False
        async def discover(self, cursor):
            items, _ = await super().discover(cursor)
            if self.added:
                items.append(Publication('3', 'https://example.org/event/3', 1800000300))
            return items, '4' if self.added else '3'
        async def fetch(self, publication):
            if publication.url.endswith('/3'):
                return Material('Новый факт', 'В библиотеке появились 30 новых книг.')
            return await super().fetch(publication)
    source = Growing()
    with TestClient(create_app(tmp_path, source=source, token='test', ai_transport=event_transport([])), headers=HEADERS) as client:
        configure(client)
        first = wait_for(client, lambda: next((c for c in client.get('/api/cards').json() if c.get('event')), None))
        source.added = True
        client.post('/api/collect')
        updated = wait_for(client, lambda: next((c for c in client.get('/api/cards').json() if c.get('event') and len(c['sources']) == 3), None))
        assert updated['published'] == 1800000300
        assert client.get('/api/materials/' + first['id']).status_code == 404
        assert any(s['url'].endswith('/3') for update in updated['event']['updates'] for s in update['sources'])
        client.post('/api/materials/' + updated['id'] + '/viewed')
        assert len(client.get('/api/cards').json()) == 1
        # A fresh identity can reappear; no semantic tombstone suppresses it.
        source.added = False
        original_discover = source.discover
        async def new_identity(cursor):
            items, marker = await original_discover(cursor)
            return items + [Publication('4', 'https://example.org/event/4', 1800000600)], marker
        source.discover = new_identity
        original_fetch = source.fetch
        async def new_fetch(publication):
            return Material('Пересказ', 'Открылась библиотека на 24 места.') if publication.url.endswith('/4') else await original_fetch(publication)
        source.fetch = new_fetch
        client.post('/api/collect')
        wait_for(client, lambda: len(c := client.get('/api/cards').json()) == 2 and all(x['complete'] for x in c))
        assert client.get('/api/materials/' + updated['id']).status_code == 404


def test_late_result_after_member_view_and_stop_cannot_resurrect_event(tmp_path):
    import asyncio
    import threading
    import time
    class Held(httpx.AsyncBaseTransport):
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.native = event_transport([])
        async def handle_async_request(self, request):
            if request.url.path == '/v1/chat/completions':
                self.started.set()
                while not self.release.is_set():
                    try:
                        await asyncio.sleep(.01)
                    except asyncio.CancelledError:
                        pass
            return self.native.handler(request)
    held = Held()
    with TestClient(create_app(tmp_path, source=EventSource(), token='test', ai_transport=held), headers=HEADERS) as client:
        configure(client)
        assert held.started.wait(2)
        member = next(c['id'] for c in client.get('/api/cards').json() if c['url'].endswith('/0'))
        client.post('/api/materials/' + member + '/viewed')
        client.post('/api/stop')
        held.release.set()
        time.sleep(.1)
        assert not list((tmp_path / 'events').glob('*.md'))
        assert not (tmp_path / 'content' / member).exists()
    with TestClient(create_app(tmp_path, source=EventSource(), token='test', ai_transport=event_transport([])), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        assert client.get('/api/materials/' + member).status_code == 404
        assert len(client.get('/api/cards').json()) == 2


@pytest.mark.parametrize('invalid', ['foreign', 'missing', 'duplicate'])
def test_invalid_members_and_foreign_attribution_fail_without_hiding_originals(tmp_path, invalid):
    native = event_transport([])
    bad = True
    def reply(request):
        response = native.handler(request)
        if request.url.path == '/v1/chat/completions' and bad:
            data = response.json()
            result = json.loads(data['choices'][0]['message']['content'])
            if invalid == 'foreign':
                result['groups'][0]['points'][0]['sources'][0]['url'] = 'https://foreign.example/'
            elif invalid == 'missing':
                result['groups'].pop()
            else:
                result['groups'][0]['members'].append(result['groups'][0]['members'][0])
            data['choices'][0]['message']['content'] = json.dumps(result)
            return httpx.Response(200, json=data)
        return response
    with TestClient(create_app(tmp_path, source=EventSource(), token='test', ai_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        configure(client)
        wait_for(client, lambda: any(('неизвестный источник' if invalid == 'foreign' else 'ровно один раз') in error for error in client.get('/api/status').json()['errors']))
        assert len(client.get('/api/cards').json()) == 3
        assert not list((tmp_path / 'events').glob('*.md'))
        bad = False
        client.patch('/api/settings/oko/global', json={'event_hours': 25})
        wait_for(client, lambda: len(client.get('/api/cards').json()) == 2)


def test_sliding_window_does_not_split_close_event_at_unrelated_boundary(tmp_path):
    class Boundary(EventSource):
        async def discover(self, cursor):
            return ([Publication(str(i), f'https://example.org/event/{i}', 1800000000+offset*3600) for i, offset in [(2, 0), (0, 23), (1, 25)]] if cursor is None else []), '3'
    with TestClient(create_app(tmp_path, source=Boundary(), token='test', ai_transport=event_transport([])), headers=HEADERS) as client:
        configure(client)
        event = wait_for(client, lambda: next((c for c in client.get('/api/cards').json() if c.get('event')), None))
        assert len(event['sources']) == 2
        assert event['published'] == 1800090000


def test_process_death_after_event_move_and_after_view_recovers_without_content_history(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from tests.support.processes import launch, connect, wait_cards, cleanup
    class ModelServer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def answer(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            self.answer({'data': [{'id': 'local:latest', 'owned_by': 'llamacpp', 'meta': {'size': 100}}]} if self.path == '/v1/models' else {'model_path': '/models/local.gguf', 'build_info': 'b-test'})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            sources = json.loads(body['messages'][1]['content'])['sources']
            result = dict(groups=[dict(members=[s['id'] for s in sources], title='Событие', points=[dict(text='Тестовое событие', sources=[dict(id=s['id'], url=s['url']) for s in sources])], updates=[], conflicts=[])])
            self.answer({'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result)}}]})
    server = ThreadingHTTPServer(('127.0.0.1', 0), ModelServer)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    process = launch(tmp_path, 'after_events_move')
    try:
        with connect(tmp_path, process) as client:
            client.post('/api/tabs/a/heartbeat')
            wait_cards(client, 3)
            client.put('/api/processors/llamacpp', json={'endpoint': f'http://127.0.0.1:{server.server_port}', 'model': 'local:latest', 'local_execution': True})
            client.patch('/api/settings/oko/global', json={'processor': 'llamacpp'})
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path, 'after_viewed_commit')
        with connect(tmp_path, process) as client:
            assert len(client.get('/api/cards').json()) == 3
            client.post('/api/tabs/a/heartbeat')
            event = wait_cards(client, 1)[0]
            members = event['event']['members']
            try:
                client.post('/api/materials/' + event['id'] + '/viewed')
            except httpx.TransportError:
                pass
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            assert client.get('/api/cards').json() == []
            client.post('/api/tabs/a/heartbeat')
            wait_for(client, lambda: not list((tmp_path / 'events').glob('*.md')))
            wait_for(client, lambda: all(not (tmp_path / 'content' / member).exists() for member in members))
            assert client.get('/api/cards').json() == []
    finally:
        cleanup(process)
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_view_invalidates_other_event_but_preserves_unrelated_members_and_retries_deletion(tmp_path):
    from oko.collection.contracts import Attachment
    import time
    fail = False
    class TwoEvents(EventSource):
        async def discover(self, cursor):
            return ([Publication(str(i), f'https://example.org/event/{i}', 1800000000+i*60) for i in range(4)] if cursor is None else []), '4'
        async def fetch(self, publication):
            return Material('Библиотека' if int(publication.id) < 2 else 'Спутник', publication.id, attachments=(Attachment('media-0.txt', b'local attachment', 'file', 'text/plain'),))
    def fault(point):
        if point == 'before_delete' and fail:
            raise OSError('secret original in error')
    native = model_transport([])
    def reply(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        groups = []
        for title in ('Библиотека', 'Спутник'):
            sources = [s for s in task['sources'] if s['title'] == title]
            if sources:
                groups.append(dict(members=[s['id'] for s in sources], title=title, points=[dict(text=title, sources=[dict(id=s['id'], url=s['url']) for s in sources])], updates=[], conflicts=[]))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(dict(groups=groups))}}]})
    options = dict(source=TwoEvents(), token='test', ai_transport=httpx.MockTransport(reply), fault=fault)
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client, lambda: len(c := client.get('/api/cards').json()) == 4 and all(x['complete'] for x in c))
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp'})
        cards = wait_for(client, lambda: c if len(c := client.get('/api/cards').json()) == 2 and all(x.get('event') for x in c) else None)
        event = next(c for c in cards if c['title'] == 'Библиотека')
        old_other = next(c for c in cards if c['title'] == 'Спутник')
        material = client.get('/api/materials/' + event['id']).json()
        media = material['sources'][0]['attachments'][0]['url']
        assert client.get(media).content == b'local attachment'
        fail = True
        client.post('/api/materials/' + event['id'] + '/viewed')
        assert client.get('/api/materials/' + event['id']).status_code == 404
        assert client.get(media).status_code == 404
        assert client.get('/api/materials/' + old_other['id']).status_code == 404
        assert len(client.get('/api/cards').json()) == 2
        assert client.get('/api/status').json()['errors'] == ['Ошибка удаления']
        assert all(client.get('/api/materials/' + s['id']).status_code == 200 for s in old_other['sources'])
    fail = False
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        remaining = wait_for(client, lambda: c if len(c := client.get('/api/cards').json()) == 1 and c[0].get('event') else None)
        assert remaining[0]['id'] == old_other['id']
        assert all(not (tmp_path / 'content' / s['id']).exists() for s in material['sources'])
        assert client.get('/api/status').json()['errors'] == []


@pytest.mark.parametrize('captions_state', ['not_requested', 'unavailable', 'error'])
def test_two_youtube_titles_without_captions_never_enter_event_ai(tmp_path, captions_state):
    from tests.support.youtube import subscribe, feed_xml, VIDEO, PNG
    import time
    from oko.collection.contracts import SourceError
    class MissingCaptions:
        async def fetch(self, video):
            if captions_state == 'error':
                raise SourceError('Ограничение частоты')
            return None
    ai = []
    entry = feed_xml().split('<entry>', 1)[1].split('</entry>', 1)[0]
    xml = feed_xml().replace('</feed>', '<entry>' + entry.replace(VIDEO, 'mnopqrstuvw') + '</entry></feed>')
    def reply(request):
        if request.url.path == '/feeds/videos.xml':
            return httpx.Response(200, text=xml)
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply), youtube_captions=MissingCaptions(), ai_transport=model_transport(ai)), headers=HEADERS) as client:
        subscribe(client)
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': captions_state != 'not_requested'})
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client, lambda: len(c := client.get('/api/cards').json()) == 2 and all(x.get('captions') == captions_state and not x.get('loading') for x in c))
        time.sleep(.7)
        assert ai == []
        assert len(client.get('/api/cards').json()) == 2
