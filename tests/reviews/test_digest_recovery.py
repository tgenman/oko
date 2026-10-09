from tests.support.digests import configure, digest_transport
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from oko.reader.app import create_app
from tests.support.sources import EventSource
from tests.support.reader import HEADERS, wait_for
from tests.support.models import model_transport

def test_partial_results_resume_after_stop_without_repeating_completed_part(tmp_path):
    calls = []
    blocked = [False]
    native = digest_transport(calls)
    def reply(request):
        if request.url.path == '/v1/chat/completions' and len(calls) == 1 and not blocked[0]:
            return httpx.Response(503)
        return native.handler(request)
    from oko.collection.contracts import Publication, Material
    class Big(EventSource):
        async def fetch(self, publication):
            return Material('Большой исходник', 'Текст '*10000)
    options = dict(source=Big(), token='test', ai_transport=httpx.MockTransport(reply))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure(client)
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        wait_for(client, lambda: any('занят' in e for e in client.get('/api/status').json()['errors']))
        assert client.get('/api/materials/'+identity).status_code == 404
        assert len(client.get('/api/cards').json()) == 3
    blocked[0] = True
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        material = wait_for(client, lambda: m if (m := client.get('/api/materials/'+identity).json()).get('digest') else None)
        assert len(material['sources']) == 3
        assert len(calls) == 7  # three 60k originals; six bounded parts, first reused
        assert [len(c['sources'][0]['text']) for c in calls[:-1]] == [40000, 20000]*3
        client.post('/api/materials/'+identity+'/viewed')
        assert not list((tmp_path / 'digests').glob('*.md'))


def test_corrupt_completed_digest_is_not_presented_as_empty_success(tmp_path):
    calls = []
    options = dict(source=EventSource(), token='test', ai_transport=digest_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure(client)
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        wait_for(client, lambda: client.get('/api/materials/'+identity).json().get('digest'))
    (tmp_path / 'digests' / (identity+'.md')).write_text('broken')
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/materials/'+identity).status_code == 404
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client, lambda: client.get('/api/materials/'+identity).json().get('digest'))
        assert len(calls) == 2


def test_process_death_after_partial_move_and_view_commit_recovers(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from tests.support.processes import launch, connect, wait_cards, cleanup
    native = digest_transport([])
    class ModelServer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def answer(self):
            request = httpx.Request(self.command, 'http://model'+self.path, content=self.rfile.read(int(self.headers.get('Content-Length', 0))))
            response = native.handler(request)
            self.send_response(response.status_code)
            self.send_header('Content-Length', str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)
        do_GET = answer
        do_POST = answer
    server = ThreadingHTTPServer(('127.0.0.1', 0), ModelServer)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    process = launch(tmp_path, 'after_digest_move')
    try:
        with connect(tmp_path, process) as client:
            client.patch('/api/settings/oko/global', json={'events':False, 'processor':'llamacpp'})
            client.post('/api/tabs/a/heartbeat')
            wait_cards(client, 3)
            client.put('/api/processors/llamacpp', json={'endpoint':f'http://127.0.0.1:{server.server_port}', 'model':'local:latest', 'local_execution':True})
            identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path, 'after_viewed_commit')
        with connect(tmp_path, process) as client:
            assert client.get('/api/materials/'+identity).status_code == 404
            client.post('/api/tabs/a/heartbeat')
            wait_for(client, lambda: client.get('/api/materials/'+identity).json().get('digest'))
            members = client.get('/api/materials/'+identity).json()['sources']
            try:
                client.post('/api/materials/'+identity+'/viewed')
            except httpx.TransportError:
                pass
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            assert client.get('/api/cards').json() == []
            client.post('/api/tabs/a/heartbeat')
            wait_for(client, lambda: not list((tmp_path / 'digests').glob('*.md')))
            assert all(client.get('/api/materials/'+m['id']).status_code == 404 for m in members)
    finally:
        cleanup(process)
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize('damage', [
    {'parts': 999999}, {'reduce_cursor': -1}, {'rounds': -1},
    {'complete': False}, {'points': []}, {'reduce_cursor': 1}, {'rounds': 1},
    {'parts': 0}, 'wrong_url', 'foreign_text_url',
    {'points': [{'text': 'Чужой тезис', 'sources': [{'id': 'alien', 'url': 'https://alien.test/'}]}]},
    {'reduced': [{'text': 'Несогласованный результат', 'sources': [{'id': 'alien', 'url': 'https://alien.test/'}]}]},
])
def test_impossible_saved_progress_rebuilds_the_same_digest_without_false_ready(tmp_path, damage):
    calls = []
    options = dict(source=EventSource(), token='test', ai_transport=digest_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure(client)
        originals = client.get('/api/cards').json()
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        wait_for(client, lambda: client.get('/api/materials/'+identity).json().get('digest'))
    path = tmp_path / 'digests' / (identity+'.md')
    header = json.loads(path.read_text().split('\n\n', 1)[0])
    if damage == 'wrong_url':
        header['result']['points'][0]['sources'][0]['url'] = 'https://alien.test/'
    elif damage == 'foreign_text_url':
        header['result']['points'][0]['text'] = 'Чужая ссылка https://alien.test/'
    else:
        header['result'].update(damage)
    path.write_text(json.dumps(header)+'\n\nПовреждённый прогресс')
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/materials/'+identity).status_code == 404
        assert {c['id'] for c in client.get('/api/cards').json()} == {c['id'] for c in originals}
        client.post('/api/tabs/reader/heartbeat')
        digest = wait_for(client, lambda: m if (m := client.get('/api/materials/'+identity).json()).get('digest') else None)
        assert {s['id'] for s in digest['sources']} == {c['id'] for c in originals}
        assert len(calls) == 2


def test_changed_progress_during_response_discards_old_stage_without_restarting_valid_parts(tmp_path):
    import asyncio
    import threading
    from oko.collection.contracts import Material
    class Big(EventSource):
        async def fetch(self, publication):
            return Material('Тема', 'Текст '*10000)
    class Held(httpx.AsyncBaseTransport):
        started = threading.Event()
        release = threading.Event()
        calls = []
        native = model_transport([])
        async def handle_async_request(self, request):
            if request.url.path != '/v1/chat/completions':
                return self.native.handler(request)
            task = json.loads(json.loads(request.content)['messages'][1]['content'])
            self.calls.append(task)
            old = len(self.calls) == 2
            if old:
                self.started.set()
                while not self.release.is_set():
                    await asyncio.sleep(.01)
            source = task['sources'][0]
            text = 'Ответ устаревшего этапа' if old else 'Тема периода'
            result = dict(points=[dict(text=text, sources=[dict(id=source['id'], url=source['url'])])])
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result)}}]})
    held = Held()
    with TestClient(create_app(tmp_path, source=Big(), token='test', ai_transport=held), headers=HEADERS) as client:
        configure(client)
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        assert held.started.wait(3)
        path = tmp_path / 'digests' / (identity+'.md')
        header = json.loads(path.read_text().split('\n\n', 1)[0])
        assert header['result']['parts'] == 1
        header['result']['points'][0]['text'] = 'Исправленный промежуточный тезис'
        path.write_text(json.dumps(header)+'\n\nИсправленный промежуточный тезис')
        held.release.set()
        wait_for(client, lambda: client.get('/api/materials/'+identity).json().get('digest'))
        assert len(held.calls) == 8  # six parts + final + discarded old answer
        assert held.calls[2]['sources'] == held.calls[1]['sources']
        assert 'Исправленный промежуточный тезис' in held.calls[-1]['sources'][0]['text']
        assert 'Ответ устаревшего этапа' not in held.calls[-1]['sources'][0]['text']


def test_reduce_round_resumes_legacy_progress_without_repeating_accepted_groups(tmp_path):
    from oko.collection.contracts import Material
    class Big(EventSource):
        async def fetch(self, publication):
            return Material('Тема', 'Текст '*10000)
    calls = []
    fail = [True]
    native = model_transport([])
    def reply(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        if task['operation'] == 'digest_reduce' and any(c['operation'] == 'digest_reduce' for c in calls) and fail[0]:
            return httpx.Response(503)
        calls.append(task)
        source = task['sources'][0]
        texts = [f'Часть {len(calls)}, тезис {i}: '+('а'*3800) for i in range(30)] if task['operation'] == 'digest' else ['Общая тема']
        result = dict(points=[dict(text=text, sources=[dict(id=source['id'], url=source['url'])]) for text in texts])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result)}}]})
    options = dict(source=Big(), token='test', ai_transport=httpx.MockTransport(reply))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure(client)
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        wait_for(client, lambda: any('занят' in e for e in client.get('/api/status').json()['errors']))
        assert client.get('/api/materials/'+identity).status_code == 404
        assert len(calls) == 7
    path = tmp_path / 'digests' / (identity+'.md')
    header = json.loads(path.read_text().split('\n\n', 1)[0])
    assert set(header['result']) == {'parts', 'points', 'complete', 'reduce_cursor', 'reduced', 'rounds'}
    assert header['result']['parts'] == 6 and header['result']['reduce_cursor'] == 1
    # Literal pre-refactor fields, without any volatile plan or new journal.
    path.write_text(json.dumps({'signature': header['signature'], 'result': {
        'parts': 6, 'points': header['result']['points'], 'complete': False,
        'reduce_cursor': 1, 'reduced': header['result']['reduced'], 'rounds': 0,
    }})+'\n\nПромежуточные тезисы')
    accepted = json.dumps(calls[-1]['sources'], sort_keys=True)
    fail[0] = False
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        digest = wait_for(client, lambda: m if (m := client.get('/api/materials/'+identity).json()).get('digest') else None)
        assert len(digest['sources']) == 3
        assert all(c['operation'] != 'digest' for c in calls[7:])
        assert all(json.dumps(c['sources'], sort_keys=True) != accepted for c in calls[7:])
        assert calls[-1]['operation'] == 'digest_final'
        client.post('/api/materials/'+identity+'/viewed')
        assert client.get('/api/cards').json() == []


def test_empty_ai_set_finishes_and_restarts_without_model_calls(tmp_path):
    from tests.support.filters import FilterSource
    calls = []
    native = model_transport([])
    def reply(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        assert task['operation'] == 'filter'  # hidden originals never enter digest AI
        result = dict(hidden=True, reason='Исключено правилом', removed=[])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result)}}]})
    options = dict(source=FilterSource(), token='test', ai_transport=httpx.MockTransport(reply))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure(client)
        client.patch('/api/settings/oko/global', json={'prompt_filters': 'Исключи всё'})
        originals = wait_for(client, lambda: c if len(c := client.get('/api/cards?show_hidden=true').json()) == 3 and all(x['filter']['hidden'] for x in c) else None)
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        digest = wait_for(client, lambda: m if (m := client.get('/api/materials/'+identity).json()).get('digest') else None)
        assert digest['digest']['points'] == []
        assert len(digest['sources']) == 3 and len(calls) == 3
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/materials/'+identity).json()['digest']['points'] == []
        client.post('/api/tabs/reader/heartbeat')
        client.post('/api/materials/'+identity+'/viewed')
        assert all(client.get('/api/materials/'+c['id']).status_code == 404 for c in originals)
        assert len(calls) == 3


def test_legacy_single_part_resumes_after_file_move_without_another_ai_call(tmp_path):
    calls = []
    failed = [False]
    def fault(point):
        if point == 'after_digest_move' and not failed[0]:
            failed[0] = True
            raise OSError('Сбой после записи части')
    options = dict(source=EventSource(), token='test', ai_transport=digest_transport(calls))
    with TestClient(create_app(tmp_path, fault=fault, **options), headers=HEADERS) as client:
        configure(client)
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        wait_for(client, lambda: bool(client.get('/api/status').json()['errors']))
        assert client.get('/api/materials/'+identity).status_code == 404
        assert len(calls) == 1
    path = tmp_path / 'digests' / (identity+'.md')
    header = json.loads(path.read_text().split('\n\n', 1)[0])
    assert header['result']['parts'] == 1 and header['result']['complete'] is False
    header['result'] = {'parts': 1, 'points': header['result']['points']}
    path.write_text(json.dumps(header)+'\n\nТезисы одной части')
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        digest = wait_for(client, lambda: m if (m := client.get('/api/materials/'+identity).json()).get('digest') else None)
        assert len(digest['sources']) == 3
        assert len(calls) == 1


def test_corrupt_progress_rebuild_never_expands_frozen_set_to_new_arrivals(tmp_path):
    from oko.collection.contracts import Publication, Material
    class Growing(EventSource):
        added = False
        async def discover(self, cursor):
            items, _ = await super().discover(cursor)
            if self.added:
                items.append(Publication('3', 'https://example.org/event/3', 1800000300))
            return items, '4' if self.added else '3'
        async def fetch(self, publication):
            return Material('Новое поступление', 'Новая тема') if publication.id == '3' else await super().fetch(publication)
    source = Growing()
    calls = []
    options = dict(source=source, token='test', ai_transport=digest_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure(client)
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        digest = wait_for(client, lambda: m if (m := client.get('/api/materials/'+identity).json()).get('digest') else None)
        old_ids = {s['id'] for s in digest['sources']}
        source.added = True
        client.post('/api/collect')
        wait_for(client, lambda: len(c := client.get('/api/cards').json()) == 2 and all(x['complete'] for x in c))
    path = tmp_path / 'digests' / (identity+'.md')
    header = json.loads(path.read_text().split('\n\n', 1)[0])
    header['result']['reduce_cursor'] = -1
    path.write_text(json.dumps(header)+'\n\nПовреждённый указатель')
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        digest = wait_for(client, lambda: m if (m := client.get('/api/materials/'+identity).json()).get('digest') else None)
        assert {s['id'] for s in digest['sources']} == old_ids
        assert len(client.get('/api/cards').json()) == 2
        assert all(not any(s['url'].endswith('/3') for s in c['sources']) for c in calls)
