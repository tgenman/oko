from tests.support.reader import HEADERS
from tests.support.reader import wait_for
from tests.support.reader import setup_reader
from tests.support.models import model_transport

import json
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from tests.support.app import create_app
from oko.collection.demo import DemoSource


def test_local_theses_follow_inherited_prompt_and_delete_with_original(tmp_path: Path):
    calls = []
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=model_transport(calls)), headers=HEADERS) as client:
        item = setup_reader(client, theses=True)
        assert client.put('/api/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True}).status_code == 200
        assert client.post('/api/processors/llamacpp/check').json()['local'] is True
        assert client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True, 'prompt_theses': 'Один тезис о просмотре.'}).status_code == 200
        material = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('theses') else None))
        assert material['theses']['points'][0]['text'] == 'Просмотрено удаляет текст.'
        assert material['text'].startswith('Это тестовая публикация.')
        chat = next(r for r in calls if r.url.path == '/v1/chat/completions')
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
        item = setup_reader(client, theses=True)
        client.put('/api/processors/llamacpp', json={'endpoint': 'https://models.example.org', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        m = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('ai', {}).get('error') else None))
        assert 'Запрещена публичная' in m['ai']['error']
        assert not any(r.url.path == '/v1/chat/completions' for r in calls)
        client.patch('/api/feeds/demo', json={'public_ai': True})
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))
        assert any(r.url.path == '/v1/chat/completions' for r in calls)


def test_http_success_without_finished_attributed_result_is_not_theses(tmp_path: Path):
    calls = []
    good = model_transport(calls)
    def reply(request):
        if request.url.path != '/v1/chat/completions':
            return good.handler(request)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps({'points': [{'text': 'Выдуманный тезис', 'sources': [{'id': 'outsider', 'url': 'https://evil.org'}]}]})}}]})
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        item = setup_reader(client, theses=True)
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        m = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('ai', {}).get('error') else None))
        assert m['theses'] is None
        assert 'неизвестный источник' in m['ai']['error']
        assert not list(tmp_path.glob('content/*/1/theses.md'))


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
        if request.url.path != '/v1/chat/completions':
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
        item = setup_reader(client, theses=True)
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
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
        requests = [json.loads(r.content) for r in held.requests if r.url.path == '/v1/chat/completions']
        assert any('Новый промпт' in r['messages'][0]['content'] for r in requests)


def test_viewed_during_ai_never_reappears_and_stop_is_bounded(tmp_path: Path):
    held = HeldModel()
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=held), headers=HEADERS) as client:
        item = setup_reader(client, theses=True)
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
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


def test_model_error_remains_visible_without_leaking_response(tmp_path: Path):
    reply = {'error': 'secret prompt should never appear in UI'}
    calls = []
    good = model_transport(calls)
    def respond(request):
        return httpx.Response(200, json=reply) if request.url.path == '/v1/chat/completions' else good.handler(request)
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=httpx.MockTransport(respond)), headers=HEADERS) as client:
        item = setup_reader(client, theses=True)
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        m = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('ai', {}).get('error') else None))
        assert m['theses'] is None
        assert 'secret prompt' not in json.dumps(client.get('/api/status').json())
        assert m['ai']['error'] != 'Ошибка подготовки тезисов'


@pytest.mark.parametrize('case', ['unknown', 'unconfirmed', 'router', 'missing'])
def test_unverified_local_server_never_receives_forbidden_content(tmp_path: Path, case):
    calls = []
    good = model_transport(calls)
    def respond(request):
        if request.url.path == '/props' and case == 'unknown':
            return httpx.Response(200, json={'model_path': 'remote-model', 'build_info': 'proxy'})
        if request.url.path == '/v1/models' and case in ('router', 'missing'):
            return httpx.Response(200, json={'data': [
                {'id': 'other', 'owned_by': 'llamacpp', 'meta': {'size': 100}},
                *([{'id': 'local:latest', 'owned_by': 'llamacpp', 'meta': {'size': 100}}] if case == 'router' else [])]})
        return good.handler(request)
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=httpx.MockTransport(respond)), headers=HEADERS) as client:
        item = setup_reader(client, theses=True)
        config = {'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest'}
        if case != 'unconfirmed':
            config['local_execution'] = True
        assert client.put('/api/processors/llamacpp', json=config).status_code == 200
        check = client.post('/api/processors/llamacpp/check')
        if case == 'missing':
            assert check.status_code == 409
        else:
            assert check.json()['local'] is False
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('ai', {}).get('error'))
        assert not any(r.url.path == '/v1/chat/completions' for r in calls)


def test_reader_can_retry_theses_after_model_response_is_fixed(tmp_path: Path):
    calls = []
    fixed = False
    good = model_transport(calls)

    def respond(request):
        if request.url.path == '/v1/chat/completions' and not fixed:
            return httpx.Response(200, json={'choices': []})
        return good.handler(request)

    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=httpx.MockTransport(respond)), headers=HEADERS) as client:
        item = setup_reader(client, theses=True)
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('ai', {}).get('error'))
        fixed = True
        assert client.post('/api/materials/' + item + '/theses/retry').status_code == 200
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))
        client.post('/api/materials/' + item + '/viewed')
        assert client.post('/api/materials/' + item + '/theses/retry').status_code == 404


def test_local_router_checks_selected_model_before_sending_forbidden_public_input(tmp_path: Path):
    calls = []
    native = model_transport(calls)
    def reply(request):
        if request.url.path == '/props' and 'model' not in request.url.params:
            return httpx.Response(200, json={'role': 'router', 'model_path': 'none', 'build_info': 'b-router'})
        if request.url.path == '/props':
            assert request.url.params['model'] == 'local:latest'
        return native.handler(request)
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        item = setup_reader(client, theses=True)
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        assert client.post('/api/processors/llamacpp/check').json()['local'] is True
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))
        assert any(r.url.path == '/v1/chat/completions' for r in calls)


def test_theses_disable_reasoning_to_leave_budget_for_finished_json(tmp_path: Path):
    calls = []
    native = model_transport(calls)
    def reply(request):
        if request.url.path == '/v1/chat/completions' and json.loads(request.content).get('reasoning_effort') != 'none':
            return httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'role': 'assistant', 'content': '', 'reasoning_content': 'unfinished'}}]})
        return native.handler(request)
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', ai_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        item = setup_reader(client, theses=True)
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))
