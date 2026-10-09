from tests.support.filters import FilterSource, transport
import json
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from oko.reader.app import create_app
from oko.collection.contracts import Material
from tests.support.reader import HEADERS, wait_for


def configure(client):
    client.post('/api/tabs/reader/heartbeat')
    wait_for(client, lambda: len(c := client.get('/api/cards').json()) == 3 and all(x['complete'] for x in c))
    client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
    client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'prompt_filters': 'Убирай рекламу. Сохраняй законы.'})


def test_hidden_ads_and_clean_view_preserve_original_and_restore_same_version(tmp_path: Path):
    calls = []
    options = dict(source=FilterSource(), token='test', ai_transport=transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure(client)
        cards = wait_for(client, lambda: (c if len(c := client.get('/api/cards').json()) == 2 and all(x['filter']['status'] == 'done' for x in c) else None))
        all_cards = client.get('/api/cards?show_hidden=true').json()
        assert len(all_cards) == 3
        hidden = next(c for c in all_cards if c['filter']['hidden'])
        assert hidden['filter']['reason'] == 'Реклама'
        mixed = next(c for c in cards if c['url'].endswith('/1'))
        material = client.get('/api/materials/' + mixed['id']).json()
        assert material['display_text'] == 'Открылась библиотека. '
        assert material['text'] == 'Открылась библиотека. Реклама: купите курс.'
        assert sum(f['count'] for f in client.get('/api/folders').json()) == 2
        assert client.post('/api/materials/' + hidden['id'] + '/filter/restore').status_code == 200
        client.patch('/api/settings/oko/global', json={'prompt_filters': 'Ещё раз убирай рекламу.'})
        wait_for(client, lambda: all(c.get('filter', {}).get('status') == 'done' for c in client.get('/api/cards').json()))
        assert len(client.get('/api/cards').json()) == 3
        assert client.get('/api/materials/' + hidden['id']).json()['filter']['restored']
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert len(client.get('/api/cards').json()) == 3
        assert client.get('/api/materials/' + hidden['id']).json()['filter']['restored']
        client.post('/api/materials/' + hidden['id'] + '/viewed')
        assert client.get('/api/materials/' + hidden['id']).status_code == 404
        assert not (tmp_path / 'content' / hidden['id']).exists()


def test_theses_receive_clean_text_and_hidden_material_is_not_summarized(tmp_path):
    calls = []
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=transport(calls)), headers=HEADERS) as client:
        configure(client)
        client.patch('/api/settings/oko/global', json={'theses': True})
        wait_for(client, lambda: all(c.get('theses') for c in client.get('/api/cards').json()))
        inputs = [json.loads(json.loads(r.content)['messages'][1]['content']) for r in calls if r.url.path == '/v1/chat/completions']
        texts = {p['sources'][0]['text'] for p in inputs if p['operation'] == 'theses'}
        assert texts == {'Открылась библиотека. ', 'Изменён закон о выборах.'}


def test_unknown_filter_fragment_is_error_and_forbidden_ai_sends_nothing(tmp_path):
    calls = []
    native = transport(calls)
    def reply(request):
        if request.url.path == '/v1/chat/completions':
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(dict(hidden=False, reason='Реклама', removed=['несуществующий текст']))}}]})
        return native.handler(request)
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        configure(client)
        item = client.get('/api/cards').json()[0]['id']
        material = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json())['filter'].get('error') else None))
        assert 'отсутствующий фрагмент' in material['filter']['error']
        assert material['display_text'] == material['text']
        assert len(client.get('/api/cards').json()) == 3
        client.put('/api/processors/llamacpp', json={'endpoint': 'https://remote.example.org', 'model': 'local:latest', 'local_execution': True})
        material = wait_for(client, lambda: (m if 'Запрещена публичная' in ((m := client.get('/api/materials/' + item).json())['filter'].get('error') or '') else None))
        assert material['filter']['status'] == 'pending'
        assert not any(r.url.host == 'remote.example.org' and r.url.path == '/v1/chat/completions' for r in calls)


def test_filter_delete_and_stop_reject_cancel_resistant_late_reply(tmp_path):
    import asyncio
    import threading
    import time

    class HeldFilter(httpx.AsyncBaseTransport):
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.native = transport([])
        async def handle_async_request(self, request):
            if request.url.path == '/v1/chat/completions':
                self.started.set()
                while not self.release.is_set():
                    try:
                        await asyncio.sleep(.01)
                    except asyncio.CancelledError:
                        pass
            return self.native.handler(request)
    held = HeldFilter()
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=held), headers=HEADERS) as client:
        configure(client)
        assert held.started.wait(2)
        # First item is wholly advertising; the late answer would hide it.
        item = next(c['id'] for c in client.get('/api/cards').json() if c['url'].endswith('/0'))
        client.post('/api/materials/' + item + '/viewed')
        client.post('/api/stop')
        held.release.set()
        time.sleep(.15)
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=transport([])), headers=HEADERS) as client:
        assert client.get('/api/materials/' + item).status_code == 404
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client, lambda: all(c['filter']['status'] == 'done' for c in client.get('/api/cards').json()))
        assert len(client.get('/api/cards?show_hidden=true').json()) == 2


def test_filter_missing_file_retries_without_losing_user_correction(tmp_path):
    calls = []
    options = dict(source=FilterSource(), token='test', ai_transport=transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure(client)
        cards = wait_for(client, lambda: (c if len(c := client.get('/api/cards?show_hidden=true').json()) == 3 and all(x['filter']['status'] == 'done' for x in c) else None))
        item = next(c['id'] for c in cards if c['filter']['hidden'])
        client.post('/api/materials/' + item + '/filter/restore')
    (tmp_path / 'content' / item / '1' / 'filter.md').unlink()
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        result = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json())['filter']['status'] == 'done' else None))
        assert result['filter']['restored']
        assert not result['filter']['hidden']
        assert len(client.get('/api/cards').json()) == 3


def test_restore_applies_only_to_original_version_and_media_survive_filter(tmp_path):
    from oko.collection.contracts import Attachment
    class ChangingSource(FilterSource):
        revision = False
        async def fetch(self, publication):
            result = await super().fetch(publication)
            return Material(result.title, result.text + (' Новая версия.' if self.revision else ''), attachments=(Attachment('media-0.txt', b'attachment', 'file', 'text/plain'),))
    source = ChangingSource()
    with TestClient(create_app(tmp_path, source=source, token='test', ai_transport=transport([])), headers=HEADERS) as client:
        configure(client)
        cards = wait_for(client, lambda: (c if len(c := client.get('/api/cards?show_hidden=true').json()) == 3 and all(x['filter']['status'] == 'done' for x in c) else None))
        hidden = next(c for c in cards if c['filter']['hidden'])
        item = hidden['id']
        assert client.get(hidden['attachments'][0]['url']).content == b'attachment'
        client.post('/api/materials/' + item + '/filter/restore')
        source.revision = True
        client.post('/api/materials/' + item + '/retry')
        result = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('version') != hidden['version'] and m['filter']['status'] == 'done' else None))
        assert result['filter']['hidden']
        assert not result['filter']['restored']
        client.post('/api/materials/' + item + '/viewed')
        assert client.get(hidden['attachments'][0]['url']).status_code == 404


def test_process_death_after_filter_move_does_not_publish_orphan(tmp_path):
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
            self.rfile.read(int(self.headers['Content-Length']))
            self.answer({'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(dict(hidden=True, reason='Скрыто по правилу пользователя', removed=[]))}}]})
    server = ThreadingHTTPServer(('127.0.0.1', 0), ModelServer)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    process = launch(tmp_path, 'after_filter_move')
    try:
        with connect(tmp_path, process) as client:
            client.post('/api/tabs/a/heartbeat')
            item = wait_cards(client, 3)[0]['id']
            client.put('/api/processors/llamacpp', json={'endpoint': f'http://127.0.0.1:{server.server_port}', 'model': 'local:latest', 'local_execution': True})
            client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'prompt_filters': 'Скрыть тестовые публикации.'})
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            assert len(client.get('/api/cards').json()) == 3
            assert client.get('/api/materials/' + item).json()['filter']['status'] == 'pending'
            client.post('/api/tabs/a/heartbeat')
            wait_for(client, lambda: len(client.get('/api/cards').json()) == 0)
            assert len(client.get('/api/cards?show_hidden=true').json()) == 3
            client.post('/api/materials/' + item + '/viewed')
            assert not (tmp_path / 'content' / item).exists()
    finally:
        cleanup(process)
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_codex_filters_require_permission_and_use_filter_schema(tmp_path):
    from tests.support.codex import CodexService
    class FilterCodex(CodexService):
        async def complete(self, prompt, schema, directory):
            self.inputs.append(prompt)
            task = json.loads(prompt.split('\n', 1)[1])
            assert task['operation'] == 'filter'
            assert set(schema['properties']) == {'hidden', 'reason', 'removed'}
            return json.dumps(dict(hidden=True, reason='Реклама', removed=[]))
    service = FilterCodex()
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', codex_client=service), headers=HEADERS) as client:
        configure(client)
        client.patch('/api/settings/oko/global', json={'processor': 'codex'})
        item = client.get('/api/cards').json()[0]['id']
        wait_for(client, lambda: 'Запрещена публичная' in (client.get('/api/materials/' + item).json()['filter'].get('error') or ''))
        assert service.inputs == []
        client.patch('/api/feeds/demo', json={'public_ai': True})
        wait_for(client, lambda: len(client.get('/api/cards').json()) == 0)
        assert len(service.inputs) == 3
