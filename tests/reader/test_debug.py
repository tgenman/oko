import time
from fastapi.testclient import TestClient
from oko.reader.app import create_app

HEADERS = {'Authorization': 'Bearer test-secret'}


def test_debug_captures_background_traceback_and_survives_restart(tmp_path):
    class Broken:
        async def discover(self, cursor):
            raise RuntimeError('broken collector test-secret')

        async def fetch(self, publication):
            raise AssertionError('not reached')

    with TestClient(create_app(tmp_path, source=Broken(), token='test-secret'), headers=HEADERS) as client:
        assert client.get('/api/debug', headers={'Authorization': ''}).status_code == 403
        client.post('/api/tabs/reader/heartbeat')
        for _ in range(100):
            response = client.get('/api/debug')
            assert response.status_code == 200
            errors = response.json()['groups']
            if errors:
                break
            time.sleep(.02)
        assert 'broken collector' in errors[-1]['message']
        assert 'RuntimeError' in errors[-1]['traceback']
        assert 'discover' in errors[-1]['traceback']
        assert 'test-secret' not in response.text
        client.post('/api/stop')
        assert client.get('/api/debug').json()['groups'] == errors
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        assert client.get('/api/debug').json()['groups'] == errors


def test_browser_errors_are_bounded_and_persistent(tmp_path):
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        for i in range(103):
            assert client.post('/api/debug', json={'source': 'browser', 'message': f'error {i}', 'traceback': 'at click (app.js:42)'}).status_code == 200
        errors = client.get('/api/debug').json()['groups']
        assert len(errors) == 100
        assert errors[-1]['message'] == 'error 3'
        assert errors[-1]['source'] == 'browser'
        assert errors[-1]['traceback'] == 'at click (app.js:42)'


def test_unexpected_http_error_is_visible_and_shutdown_cancellation_is_not(tmp_path):
    import asyncio
    import threading

    class Waiting:
        started = threading.Event()
        async def discover(self, cursor):
            self.started.set()
            await asyncio.Event().wait()
        async def fetch(self, publication):
            raise AssertionError('not reached')

    source = Waiting()
    app = create_app(tmp_path, source=source, token='test-secret')

    @app.get('/api/broken')
    async def broken():
        raise RuntimeError('HTTP exploded')

    with TestClient(app, headers=HEADERS) as client:
        assert client.get('/api/broken').status_code == 500
        assert any('HTTP exploded' in error['traceback'] for error in client.get('/api/debug').json()['groups'])
        client.post('/api/tabs/reader/heartbeat')
        assert source.started.wait(2)
        before = client.get('/api/debug').json()['groups']
        client.post('/api/stop')
        assert client.get('/api/debug').json()['groups'] == before


def test_groups_repeat_without_counting_delivery_twice_and_hide_credentials(tmp_path):
    payload = {'source': 'browser:feed-a', 'message': 'Oops password=private api_key=hiddenkey',
               'traceback': 'Error at action app.js:42', 'error_type': 'Error', 'location': 'app.js:42', 'event_id': 'one'}
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        client.post('/api/debug', json=payload)
        client.post('/api/debug', json=payload)
        first = client.get('/api/debug').json()['groups'][0]
        assert first['count'] == 1
        client.post('/api/debug', json={**payload, 'event_id': 'two'})
        groups = client.get('/api/debug').json()['groups']
        assert len(groups) == 1 and groups[0]['count'] == 2
        assert groups[0]['first_time'] == first['first_time']
        assert groups[0]['last_time'] >= first['last_time']
        client.post('/api/debug', json={**payload, 'source': 'browser:feed-b', 'event_id': 'three'})
        groups = client.get('/api/debug').json()['groups']
        assert len(groups) == 2
        raw = (tmp_path / 'debug.json').read_text()
        assert 'private' not in raw and 'hiddenkey' not in raw and 'test-secret' not in raw
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        assert client.get('/api/debug').json()['groups'] == groups


def test_handled_api_errors_record_once_and_disk_failure_does_not_break_reader(tmp_path):
    app = create_app(tmp_path, token='test-secret')
    @app.get('/api/wrapped')
    async def wrapped():
        from oko.diagnostics import record_caught
        try:
            raise ValueError('original failure')
        except ValueError as error:
            record_caught('inner')
            raise RuntimeError('outer failure') from error
    with TestClient(app, headers=HEADERS) as client:
        client.get('/api/wrapped')
        groups = client.get('/api/debug').json()['groups']
        assert len(groups) == 1 and groups[0]['count'] == 1
        assert groups[0]['message'] == 'original failure'
        assert client.post('/api/feeds/verify', json={'url': ''}).status_code == 422
        assert any('RequestValidationError' == group['error_type'] for group in client.get('/api/debug').json()['groups'])
        temporary = tmp_path / 'debug.tmp'
        temporary.mkdir()
        saved = client.post('/api/debug', json={'source': 'browser', 'message': 'disk failed'}).json()
        assert not saved['ok'] and saved['persistence_error']
        assert client.get('/api/cards').status_code == 200
        assert client.get('/api/debug').json()['persistence_error']
        temporary.rmdir()
        assert not client.get('/api/debug').json()['persistence_error']


def test_same_source_error_in_two_feeds_is_not_merged(tmp_path):
    import httpx
    def reply(request):
        return httpx.Response(500)
    with TestClient(create_app(tmp_path, token='test-secret', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        client.post('/api/import', json={'opml': '<opml><body><outline text="A" xmlUrl="https://source.test/one"/><outline text="B" xmlUrl="https://source.test/two"/></body></opml>'})
        client.post('/api/tabs/reader/heartbeat')
        for _ in range(100):
            groups = client.get('/api/debug').json()['groups']
            if len(groups) >= 2:
                break
            time.sleep(.02)
        assert len(groups) == 2
        assert groups[0]['source'] != groups[1]['source']
        assert groups[0]['message'] == groups[1]['message']
        assert all(g['count'] == 1 for g in groups)


def test_reusing_exception_instance_in_new_request_counts_a_new_occurrence(tmp_path):
    app = create_app(tmp_path, token='test-secret')
    failure = ValueError('same reusable error')
    @app.get('/api/reused')
    async def reused():
        raise failure
    with TestClient(app, headers=HEADERS) as client:
        client.get('/api/reused')
        client.get('/api/reused')
        groups = client.get('/api/debug').json()['groups']
        assert len(groups) == 1 and groups[0]['count'] == 2


def test_http_error_type_is_not_framework_stream_context(tmp_path):
    app = create_app(tmp_path, token='test-secret')
    @app.get('/api/fails')
    async def fails():
        raise ValueError('original root')
    with TestClient(app, headers=HEADERS) as client:
        client.get('/api/fails')
        group = client.get('/api/debug').json()['groups'][0]
        assert group['error_type'] == 'ValueError'
        assert group['message'] == 'original root'


def test_complete_credentials_are_masked_in_persistent_group(tmp_path):
    message = 'password="correct horse battery staple" Authorization: Basic dXNlcjpwYXNz password=\'secret with spaces\''
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        client.post('/api/debug', json={'source': 'browser', 'message': message, 'traceback': message})
        shown = client.get('/api/debug').text
        disk = (tmp_path / 'debug.json').read_text()
        for leaked in ['horse', 'battery', 'staple', 'dXNlcjpwYXNz', 'with spaces']:
            assert leaked not in shown and leaked not in disk


def test_lost_delivery_stays_idempotent_after_many_other_tab_occurrences(tmp_path):
    payload = {'source': 'browser', 'message': 'same error', 'location': 'app.js:42', 'event_id': 'original', 'stream_id': 'tab-a', 'sequence': 1}
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        client.post('/api/debug', json=payload)
        for i in range(1, 301):
            client.post('/api/debug', json={**payload, 'stream_id': 'tab-b', 'sequence': i, 'event_id': f'other-{i}'})
        assert client.get('/api/debug').json()['groups'][0]['count'] == 301
        client.post('/api/debug', json=payload)
        assert client.get('/api/debug').json()['groups'][0]['count'] == 301
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        client.post('/api/debug', json=payload)
        assert client.get('/api/debug').json()['groups'][0]['count'] == 301


def test_cancelled_codex_check_is_expected_and_not_recorded(tmp_path):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from tests.support.codex import CodexService
    class Pending(CodexService):
        started = threading.Event()
        async def check(self):
            self.started.set()
            await asyncio.Event().wait()
    service = Pending()
    with TestClient(create_app(tmp_path, token='test-secret', codex_client=service), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        with ThreadPoolExecutor(1) as pool:
            response = pool.submit(client.post, '/api/processors/codex/check')
            assert service.started.wait(2)
            client.post('/api/stop')
            stopped = response.result(timeout=2)
            assert stopped.status_code == 409
            assert stopped.headers['X-Oko-Expected'] == '1'
        assert client.get('/api/debug').json()['groups'] == []
