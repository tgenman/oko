from oko.collection.contracts import FetchResult, FetchResources
from tests.support.presence import presence, close_presence
from tests.support.filters import FilterSource, transport, setup_filters
import json
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.contracts import Material
from tests.support.reader import HEADERS, wait_for



def test_hidden_ads_and_clean_view_preserve_original_and_restore_same_version(tmp_path: Path):
    calls = []
    options = dict(source=FilterSource(), token='test', ai_transport=transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        setup_filters(client)
        cards = wait_for(client, lambda: (c if len(c := client.get('/api/v3/cards').json()) == 2 and all(x['filter']['attempt']['state'] == 'ready' for x in c) else None))
        all_cards = client.get('/api/v3/cards?show_hidden=true').json()
        assert len(all_cards) == 3
        hidden = next(c for c in all_cards if c['filter']['result']['hidden'])
        assert hidden['filter']['result']['reason'] == 'Реклама'
        mixed = next(c for c in cards if c['source_url'].endswith('/1'))
        material = client.get('/api/v3/materials/' + mixed['id']).json()
        assert material['content']['display_text'] == 'Открылась библиотека. '
        assert material['content']['text'] == 'Открылась библиотека. Реклама: купите курс.'
        assert sum(f['count'] for f in client.get('/api/v3/folders').json()) == 2
        assert client.post('/api/v3/materials/' + hidden['id'] + '/filter/restore').status_code == 200
        client.patch('/api/v3/settings/oko/global', json={'prompt_filters': 'Ещё раз убирай рекламу.'})
        wait_for(client, lambda: all(c['filter']['attempt']['state'] == 'ready' for c in client.get('/api/v3/cards').json()))
        assert len(client.get('/api/v3/cards').json()) == 3
        assert client.get('/api/v3/materials/' + hidden['id']).json()['filter']['result']['restored']
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert len(client.get('/api/v3/cards').json()) == 3
        assert client.get('/api/v3/materials/' + hidden['id']).json()['filter']['result']['restored']
        client.post('/api/v3/materials/' + hidden['id'] + '/viewed')
        assert client.get('/api/v3/materials/' + hidden['id']).status_code == 404
        assert not (tmp_path / 'content' / hidden['id']).exists()


def test_theses_receive_clean_text_and_hidden_material_is_not_summarized(tmp_path):
    calls = []
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=transport(calls, theses=True)), headers=HEADERS) as client:
        setup_filters(client)
        client.patch('/api/v3/settings/oko/global', json={'theses': True})
        wait_for(client, lambda: all(c['theses']['result'] for c in client.get('/api/v3/cards').json()))
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
        setup_filters(client)
        item = client.get('/api/v3/cards').json()[0]['id']
        material = wait_for(client, lambda: (m if (m := client.get('/api/v3/materials/' + item).json())['filter']['diagnostic_message'] else None))
        assert 'отсутствующий фрагмент' in material['filter']['diagnostic_message']
        assert material['content']['display_text'] == material['content']['text']
        assert len(client.get('/api/v3/cards').json()) == 3
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'https://remote.example.org', 'model': 'local:latest', 'local_execution': True})
        material = wait_for(client, lambda: (m if 'Запрещена публичная' in ((m := client.get('/api/v3/materials/' + item).json())['filter']['diagnostic_message'] or '') else None))
        assert material['filter']['attempt']['state'] == 'waiting'
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
        setup_filters(client)
        assert held.started.wait(2)
        # First item is wholly advertising; the late answer would hide it.
        item = next(c['id'] for c in client.get('/api/v3/cards').json() if c['source_url'].endswith('/0'))
        client.post('/api/v3/materials/' + item + '/viewed')
        client.post('/api/v3/stop')
        held.release.set()
        time.sleep(.15)
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=transport([])), headers=HEADERS) as client:
        assert client.get('/api/v3/materials/' + item).status_code == 404
        presence(client, 'reader')
        wait_for(client, lambda: all(c['filter']['attempt']['state'] == 'ready' for c in client.get('/api/v3/cards').json()))
        assert len(client.get('/api/v3/cards?show_hidden=true').json()) == 2


def test_filter_missing_file_retries_without_losing_user_correction(tmp_path):
    calls = []
    options = dict(source=FilterSource(), token='test', ai_transport=transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        setup_filters(client)
        cards = wait_for(client, lambda: (c if len(c := client.get('/api/v3/cards?show_hidden=true').json()) == 3 and all(x['filter']['attempt']['state'] == 'ready' for x in c) else None))
        item = next(c['id'] for c in cards if c['filter']['result']['hidden'])
        client.post('/api/v3/materials/' + item + '/filter/restore')
    (tmp_path / 'content' / item / '1' / 'filter.md').unlink()
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        presence(client, 'reader')
        result = wait_for(client, lambda: (m if (m := client.get('/api/v3/materials/' + item).json())['filter']['attempt']['state'] == 'ready' else None))
        assert result['filter']['result']['restored']
        assert not result['filter']['result']['hidden']
        assert len(client.get('/api/v3/cards').json()) == 3


def test_restore_applies_only_to_original_version_and_media_survive_filter(tmp_path):
    from oko.collection.contracts import Attachment
    class ChangingSource(FilterSource):
        revision = False
        async def fetch(self, publication, resources=FetchResources()):
            result = (await super().fetch(publication, resources)).material
            return FetchResult(Material(result.title, result.text + (' Новая версия.' if self.revision else ''), attachments=(Attachment('media-0.txt', b'attachment', 'file', 'text/plain'),)))
    source = ChangingSource()
    with TestClient(create_app(tmp_path, source=source, token='test', ai_transport=transport([])), headers=HEADERS) as client:
        setup_filters(client)
        cards = wait_for(client, lambda: (c if len(c := client.get('/api/v3/cards?show_hidden=true').json()) == 3 and all(x['filter']['attempt']['state'] == 'ready' for x in c) else None))
        hidden = next(c for c in cards if c['filter']['result']['hidden'])
        item = hidden['id']
        assert client.get(hidden['attachments'][0]['url']).content == b'attachment'
        client.post('/api/v3/materials/' + item + '/filter/restore')
        source.revision = True
        client.post('/api/v3/materials/' + item + '/retry')
        result = wait_for(client, lambda: (m if (m := client.get('/api/v3/materials/' + item).json())['content']['version'] != hidden['version'] and m['filter']['attempt']['state'] == 'ready' else None))
        assert result['filter']['result']['hidden']
        assert not result['filter']['result']['restored']
        client.post('/api/v3/materials/' + item + '/viewed')
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
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    process = launch(tmp_path, 'after_filter_move', model_origin=f'http://127.0.0.1:{server.server_port}')
    try:
        with connect(tmp_path, process) as client:
            presence(client, 'a')
            item = wait_cards(client, 3)[0]['id']
            client.put('/api/v3/processors/llamacpp', json={'endpoint': f'http://127.0.0.1:{server.server_port}', 'model': 'local:latest', 'local_execution': True})
            client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp', 'prompt_filters': 'Скрыть тестовые публикации.'})
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path, model_origin=f'http://127.0.0.1:{server.server_port}')
        with connect(tmp_path, process) as client:
            assert len(client.get('/api/v3/cards').json()) == 3
            assert client.get('/api/v3/materials/' + item).json()['filter']['attempt']['state'] == 'queued'
            presence(client, 'a')
            wait_for(client, lambda: len(client.get('/api/v3/cards').json()) == 0)
            assert len(client.get('/api/v3/cards?show_hidden=true').json()) == 3
            client.post('/api/v3/materials/' + item + '/viewed')
            assert not (tmp_path / 'content' / item).exists()
    finally:
        cleanup(process)
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_codex_filters_require_permission_and_use_filter_schema(tmp_path):
    from tests.support.codex import CodexService, configure_codex
    class FilterCodex(CodexService):
        async def complete(self, prompt, schema, directory, choice):
            self.inputs.append(prompt)
            task = json.loads(prompt.split('\n', 1)[1])
            assert task['operation'] == 'filter'
            assert set(schema['properties']) == {'hidden', 'reason', 'removed'}
            return json.dumps(dict(hidden=True, reason='Реклама', removed=[]))
    service = FilterCodex()
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', codex_client=service), headers=HEADERS) as client:
        configure_codex(client)
        setup_filters(client)
        client.patch('/api/v3/settings/oko/global', json={'processor': 'codex'})
        item = client.get('/api/v3/cards').json()[0]['id']
        wait_for(client, lambda: 'Запрещена публичная' in (client.get('/api/v3/materials/' + item).json()['filter']['diagnostic_message'] or ''))
        assert service.inputs == []
        client.patch('/api/v3/feeds/demo', json={'public_ai': True})
        wait_for(client, lambda: len(client.get('/api/v3/cards').json()) == 0)
        assert len(service.inputs) == 3
