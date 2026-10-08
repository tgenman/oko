import json
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from oko.app import create_app
from oko.sources import DemoSource

HEADERS = {'Authorization': 'Bearer test'}


def wait_for(client, predicate):
    for _ in range(150):
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('expected reader state did not arrive')


def model_transport(calls):
    def reply(request):
        calls.append(request)
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'local:latest', 'size': 100, 'digest': 'abc', 'details': {'format': 'gguf'}}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'details': {'format': 'gguf'}, 'model_info': {'general.architecture': 'llama'}})
        body = json.loads(request.content)
        source = json.loads(body['messages'][1]['content'])['sources'][0]
        return httpx.Response(200, json={'done': True, 'done_reason': 'stop', 'message': {'content': json.dumps({'points': [{'text': 'Просмотрено удаляет текст.', 'sources': [{'id': source['id'], 'url': source['url']}]}]})}})
    return httpx.MockTransport(reply)


def setup_reader(client):
    client.post('/api/tabs/reader/heartbeat')
    cards = wait_for(client, lambda: (cards if len(cards := client.get('/api/cards').json()) == 3 and all(c['complete'] for c in cards) else None))
    return cards[0]['id']


def test_local_theses_follow_inherited_prompt_and_delete_with_original(tmp_path: Path):
    calls = []
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=model_transport(calls)), headers=HEADERS) as client:
        item = setup_reader(client)
        assert client.put('/api/processors/ollama', json={'endpoint': 'http://127.0.0.1:11434', 'model': 'local:latest'}).status_code == 200
        assert client.post('/api/processors/ollama/check').json()['local'] is True
        assert client.patch('/api/settings/oko/global', json={'processor': 'ollama', 'theses': True, 'prompt_theses': 'Один тезис о просмотре.'}).status_code == 200
        material = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('theses') else None))
        assert material['theses']['points'][0]['text'] == 'Просмотрено удаляет текст.'
        assert material['text'].startswith('Это тестовая публикация.')
        chat = next(r for r in calls if r.url.path == '/api/chat')
        payload = json.loads(chat.content)
        assert payload['stream'] is False
        assert 'Один тезис о просмотре.' in payload['messages'][0]['content']
        assert 'tools' not in payload
        assert client.post('/api/materials/' + item + '/viewed').status_code == 200
        assert client.get('/api/materials/' + item).status_code == 404
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()


def test_unknown_remote_boundary_never_receives_forbidden_content(tmp_path: Path):
    calls = []
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=model_transport(calls)), headers=HEADERS) as client:
        item = setup_reader(client)
        client.put('/api/processors/ollama', json={'endpoint': 'https://models.example.org', 'model': 'local:latest'})
        client.patch('/api/settings/oko/global', json={'processor': 'ollama', 'theses': True})
        m = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('ai', {}).get('error') else None))
        assert 'Запрещена публичная' in m['ai']['error']
        assert not any(r.url.path == '/api/chat' for r in calls)
        client.patch('/api/feeds/demo', json={'public_ai': True})
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))
        assert any(r.url.path == '/api/chat' for r in calls)


def test_http_success_without_finished_attributed_result_is_not_theses(tmp_path: Path):
    calls = []
    good = model_transport(calls)
    def reply(request):
        if request.url.path != '/api/chat':
            return good.handler(request)
        return httpx.Response(200, json={'done': True, 'done_reason': 'stop', 'message': {'content': json.dumps({'points': [{'text': 'Выдуманный тезис', 'sources': [{'id': 'outsider', 'url': 'https://evil.org'}]}]})}})
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        item = setup_reader(client)
        client.put('/api/processors/ollama', json={'endpoint': 'http://127.0.0.1:11434', 'model': 'local:latest'})
        client.patch('/api/settings/oko/global', json={'processor': 'ollama', 'theses': True})
        m = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('ai', {}).get('error') else None))
        assert m['theses'] is None
        assert 'неизвестный источник' in m['ai']['error']
        assert not list(tmp_path.glob('content/*/1/theses.md'))


def test_completed_theses_survive_restart_without_second_model_call(tmp_path: Path):
    calls = []
    options = dict(source=DemoSource(), token='test', ai_transport=model_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        item = setup_reader(client)
        client.put('/api/processors/ollama', json={'endpoint': 'http://127.0.0.1:11434', 'model': 'local:latest'})
        client.patch('/api/settings/oko/global', json={'processor': 'ollama', 'theses': True})
        wait_for(client, lambda: all(m.get('theses') for m in client.get('/api/cards').json()))
    before = len([r for r in calls if r.url.path == '/api/chat'])
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        assert client.get('/api/materials/' + item).json()['theses']
        time.sleep(.1)
    assert len([r for r in calls if r.url.path == '/api/chat']) == before


def test_missing_theses_file_is_regenerated_after_restart(tmp_path: Path):
    calls = []
    options = dict(source=DemoSource(), token='test', ai_transport=model_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        item = setup_reader(client)
        client.put('/api/processors/ollama', json={'endpoint': 'http://127.0.0.1:11434', 'model': 'local:latest'})
        client.patch('/api/settings/oko/global', json={'processor': 'ollama', 'theses': True})
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))
    (tmp_path / 'content' / item / '1' / 'theses.md').unlink()
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))


class HeldModel(httpx.AsyncBaseTransport):
    def __init__(self):
        import threading
        self.started = threading.Event()
        self.release = threading.Event()
        self.requests = []
        self.good = model_transport(self.requests)
        self.active = 0
        self.maximum = 0

    async def handle_async_request(self, request):
        import asyncio
        if request.url.path != '/api/chat':
            return self.good.handler(request)
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        self.started.set()
        try:
            while not self.release.is_set():
                try:
                    await asyncio.sleep(.01)
                except asyncio.CancelledError:
                    pass  # Simulate an external provider which already accepted the input.
            return self.good.handler(request)
        finally:
            self.active -= 1


def test_permission_change_invalidates_late_output_and_keeps_one_ai_request(tmp_path: Path):
    held = HeldModel()
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=held), headers=HEADERS) as client:
        item = setup_reader(client)
        client.put('/api/processors/ollama', json={'endpoint': 'http://127.0.0.1:11434', 'model': 'local:latest'})
        client.patch('/api/settings/oko/global', json={'processor': 'ollama', 'theses': True})
        assert held.started.wait(2)
        client.patch('/api/settings/oko/global', json={'prompt_theses': 'Новый промпт'})
        time.sleep(.15)
        maximum = held.maximum
        late = client.get('/api/materials/' + item).json()['theses']
        held.release.set()
        assert maximum == 1
        assert late is None
        result = wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))
        assert result
        requests = [json.loads(r.content) for r in held.requests if r.url.path == '/api/chat']
        assert any('Новый промпт' in r['messages'][0]['content'] for r in requests)


def test_viewed_during_ai_never_reappears_and_stop_is_bounded(tmp_path: Path):
    held = HeldModel()
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=held), headers=HEADERS) as client:
        item = setup_reader(client)
        client.put('/api/processors/ollama', json={'endpoint': 'http://127.0.0.1:11434', 'model': 'local:latest'})
        client.patch('/api/settings/oko/global', json={'processor': 'ollama', 'theses': True})
        assert held.started.wait(2)
        start = time.monotonic()
        assert client.post('/api/materials/' + item + '/viewed').status_code == 200
        assert time.monotonic() - start < .8
        start = time.monotonic()
        assert client.post('/api/stop').status_code == 200
        assert time.monotonic() - start < .8
        held.release.set()
        time.sleep(.1)
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()


import pytest


@pytest.mark.parametrize('reply', [
    {'done': False, 'message': {'content': '{}'}},
    {'done': True, 'done_reason': 'length', 'message': {'content': '{}'}},
    {'done': True, 'done_reason': 'stop', 'message': {'content': '{broken'}},
    {'error': 'secret prompt should never appear in UI'},
])
def test_incomplete_and_invalid_responses_remain_visible_errors(tmp_path: Path, reply):
    calls = []
    good = model_transport(calls)
    def respond(request):
        return httpx.Response(200, json=reply) if request.url.path == '/api/chat' else good.handler(request)
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=httpx.MockTransport(respond)), headers=HEADERS) as client:
        item = setup_reader(client)
        client.put('/api/processors/ollama', json={'endpoint': 'http://127.0.0.1:11434', 'model': 'local:latest'})
        client.patch('/api/settings/oko/global', json={'processor': 'ollama', 'theses': True})
        m = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('ai', {}).get('error') else None))
        assert m['theses'] is None
        assert 'secret prompt' not in json.dumps(client.get('/api/status').json())


def test_ollama_cloud_metadata_at_local_endpoint_is_not_local(tmp_path: Path):
    calls = []
    good = model_transport(calls)
    def respond(request):
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'details': {'format': 'gguf'}, 'model_info': {'general.architecture': 'llama'}, 'remote_host': 'https://ollama.com', 'remote_model': 'remote'})
        return good.handler(request)
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=httpx.MockTransport(respond)), headers=HEADERS) as client:
        item = setup_reader(client)
        client.put('/api/processors/ollama', json={'endpoint': 'http://127.0.0.1:11434', 'model': 'local:latest'})
        assert client.post('/api/processors/ollama/check').json()['local'] is False
        client.patch('/api/settings/oko/global', json={'processor': 'ollama', 'theses': True})
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('ai', {}).get('error'))
        assert not any(r.url.path == '/api/chat' for r in calls)


def test_process_death_after_theses_move_retries_without_publishing_orphan(tmp_path: Path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from tests.test_process import launch, connect, wait_cards, cleanup

    class ModelServer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.answer({'models': [{'name': 'local:latest', 'size': 100, 'digest': 'abc', 'details': {'format': 'gguf'}}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/api/show':
                self.answer({'details': {'format': 'gguf'}, 'model_info': {'general.architecture': 'llama'}})
            else:
                source = json.loads(body['messages'][1]['content'])['sources'][0]
                self.answer({'done': True, 'done_reason': 'stop', 'message': {'content': json.dumps({'points': [{'text': 'Контент удаляется при просмотре.', 'sources': [{'id': source['id'], 'url': source['url']}]}]})}})

    server = ThreadingHTTPServer(('127.0.0.1', 0), ModelServer)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    process = launch(tmp_path, 'after_theses_move')
    try:
        with connect(tmp_path, process) as client:
            client.post('/api/tabs/a/heartbeat')
            cards = wait_cards(client, 3)
            item = cards[0]['id']
            client.put('/api/processors/ollama', json={'endpoint': f'http://127.0.0.1:{server.server_port}', 'model': 'local:latest'})
            client.patch('/api/settings/oko/global', json={'processor': 'ollama', 'theses': True})
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            assert client.get('/api/materials/' + item).json()['theses'] is None
            client.post('/api/tabs/a/heartbeat')
            result = wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))
            assert result['points'][0]['text'] == 'Контент удаляется при просмотре.'
            client.post('/api/materials/' + item + '/viewed')
            assert not (tmp_path / 'content' / item).exists()
            assert not (tmp_path / 'staging' / item).exists()
    finally:
        cleanup(process)
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_reader_can_retry_theses_after_model_response_is_fixed(tmp_path: Path):
    calls = []
    fixed = False
    good = model_transport(calls)

    def respond(request):
        if request.url.path == '/api/chat' and not fixed:
            return httpx.Response(200, json={'done': False})
        return good.handler(request)

    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=httpx.MockTransport(respond)), headers=HEADERS) as client:
        item = setup_reader(client)
        client.put('/api/processors/ollama', json={'endpoint': 'http://127.0.0.1:11434', 'model': 'local:latest'})
        client.patch('/api/settings/oko/global', json={'processor': 'ollama', 'theses': True})
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('ai', {}).get('error'))
        fixed = True
        assert client.post('/api/materials/' + item + '/theses/retry').status_code == 200
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))
        client.post('/api/materials/' + item + '/viewed')
        assert client.post('/api/materials/' + item + '/theses/retry').status_code == 404
