from tests.support.presence import presence, close_presence
from tests.support.subscriptions import configured_sources
import time
from fastapi.testclient import TestClient
from tests.support.app import create_app

HEADERS = {'Authorization': 'Bearer test-secret'}


def test_clear_debug_is_authorized_persistent_and_accepts_new_errors(tmp_path):
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        client.post('/api/v3/diagnostics', json={'source': 'browser', 'message': 'old error'})
        assert client.delete('/api/v3/diagnostics', headers={'Authorization': ''}).status_code == 403
        assert client.get('/api/v3/diagnostics').json()['groups']
        assert client.delete('/api/v3/diagnostics').json()['state'] == 'cleared'
        assert client.get('/api/v3/diagnostics').json()['groups'] == []
        assert client.get('/api/v3/status').json()['diagnostic_count'] == 0
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        assert client.get('/api/v3/diagnostics').json()['groups'] == []
        client.post('/api/v3/diagnostics', json={'source': 'browser', 'message': 'new error'})
        assert client.get('/api/v3/diagnostics').json()['groups'][0]['message'] == 'new error'


def test_failed_clear_keeps_debug_history(tmp_path):
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        client.post('/api/v3/diagnostics', json={'source': 'browser', 'message': 'keep error'})
        (tmp_path / 'debug.tmp').mkdir()
        result = client.delete('/api/v3/diagnostics').json()
        assert result['state'] == 'persistence_failure' and result['message']
        assert client.get('/api/v3/diagnostics').json()['groups'][0]['message'] == 'keep error'


def test_debug_captures_background_traceback_and_survives_restart(tmp_path):
    class Broken:
        changes = None
        async def discover(self, cursor):
            raise RuntimeError('broken collector test-secret')

        async def fetch(self, publication):
            raise AssertionError('not reached')

    with TestClient(create_app(tmp_path, source=Broken(), token='test-secret'), headers=HEADERS) as client:
        assert client.get('/api/v3/diagnostics', headers={'Authorization': ''}).status_code == 403
        presence(client, 'reader')
        for _ in range(100):
            response = client.get('/api/v3/diagnostics')
            assert response.status_code == 200
            errors = response.json()['groups']
            if errors:
                break
            time.sleep(.02)
        assert 'broken collector' in errors[-1]['message']
        assert 'RuntimeError' in errors[-1]['traceback']
        assert 'discover' in errors[-1]['traceback']
        assert 'test-secret' not in response.text
        client.post('/api/v3/stop')
        assert client.get('/api/v3/diagnostics').json()['groups'] == errors
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        assert client.get('/api/v3/diagnostics').json()['groups'] == errors


def test_browser_errors_are_bounded_and_persistent(tmp_path):
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        for i in range(103):
            assert client.post('/api/v3/diagnostics', json={'source': 'browser', 'message': f'error {i}', 'traceback': 'at click (app.js:42)'}).status_code == 200
        errors = client.get('/api/v3/diagnostics').json()['groups']
        assert len(errors) == 100
        assert errors[-1]['message'] == 'error 3'
        assert errors[-1]['source'] == 'browser'
        assert errors[-1]['traceback'] == 'at click (app.js:42)'


def test_unexpected_http_error_is_visible_and_shutdown_cancellation_is_not(tmp_path):
    import asyncio
    import threading

    class Waiting:
        changes = None
        started = threading.Event()
        async def discover(self, cursor):
            self.started.set()
            await asyncio.Event().wait()
        async def fetch(self, publication):
            raise AssertionError('not reached')

    source = Waiting()
    app = create_app(tmp_path, source=source, token='test-secret')

    @app.get('/api/v3/broken')
    async def broken():
        raise RuntimeError('HTTP exploded')

    with TestClient(app, headers=HEADERS) as client:
        assert client.get('/api/v3/broken').status_code == 500
        assert any('HTTP exploded' in error['traceback'] for error in client.get('/api/v3/diagnostics').json()['groups'])
        presence(client, 'reader')
        assert source.started.wait(2)
        before = client.get('/api/v3/diagnostics').json()['groups']
        client.post('/api/v3/stop')
        assert client.get('/api/v3/diagnostics').json()['groups'] == before


def test_groups_repeat_without_counting_delivery_twice_and_hide_credentials(tmp_path):
    payload = {'source': 'browser:feed-a', 'message': 'Oops password=private api_key=hiddenkey',
               'traceback': 'Error at action app.js:42', 'error_type': 'Error', 'location': 'app.js:42', 'event_id': 'one'}
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        client.post('/api/v3/diagnostics', json=payload)
        client.post('/api/v3/diagnostics', json=payload)
        first = client.get('/api/v3/diagnostics').json()['groups'][0]
        assert first['count'] == 1
        client.post('/api/v3/diagnostics', json={**payload, 'event_id': 'two'})
        groups = client.get('/api/v3/diagnostics').json()['groups']
        assert len(groups) == 1 and groups[0]['count'] == 2
        assert groups[0]['first_at'] == first['first_at']
        assert groups[0]['last_at'] >= first['last_at']
        client.post('/api/v3/diagnostics', json={**payload, 'source': 'browser:feed-b', 'event_id': 'three'})
        groups = client.get('/api/v3/diagnostics').json()['groups']
        assert len(groups) == 2
        raw = (tmp_path / 'debug.json').read_text()
        assert 'private' not in raw and 'hiddenkey' not in raw and 'test-secret' not in raw
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        assert client.get('/api/v3/diagnostics').json()['groups'] == groups


def test_handled_api_errors_record_once_and_disk_failure_does_not_break_reader(tmp_path):
    app = create_app(tmp_path, token='test-secret')
    @app.get('/api/v3/wrapped')
    async def wrapped():
        from oko.diagnostics import record_caught
        try:
            raise ValueError('original failure')
        except ValueError as error:
            record_caught('inner')
            raise RuntimeError('outer failure') from error
    with TestClient(app, headers=HEADERS) as client:
        client.get('/api/v3/wrapped')
        groups = client.get('/api/v3/diagnostics').json()['groups']
        assert len(groups) == 1 and groups[0]['count'] == 1
        assert groups[0]['message'] == 'original failure'
        assert client.post('/api/v3/feeds/verify', json={'url': ''}).status_code == 422
        assert client.get('/api/v3/diagnostics').json()['groups'] == groups  # validation reports sanitized typed errors, not raw input
        temporary = tmp_path / 'debug.tmp'
        temporary.mkdir()
        saved = client.post('/api/v3/diagnostics', json={'source': 'browser', 'message': 'disk failed'}).json()
        assert saved['state'] == 'persistence_failure' and saved['message']
        assert client.get('/api/v3/cards').status_code == 200
        assert client.get('/api/v3/diagnostics').json()['persistence_error']
        temporary.rmdir()
        assert not client.get('/api/v3/diagnostics').json()['persistence_error']


def test_same_source_error_in_two_feeds_is_not_merged(tmp_path):
    configured_sources(tmp_path, [('https://source.test/one', 'A', 'Без папки'), ('https://source.test/two', 'B', 'Без папки')])
    import httpx
    def reply(request):
        return httpx.Response(500)
    with TestClient(create_app(tmp_path, token='test-secret', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        client.get('/api/v3/subscriptions')
        presence(client, 'reader')
        for _ in range(100):
            groups = client.get('/api/v3/diagnostics').json()['groups']
            if len(groups) >= 2:
                break
            time.sleep(.02)
        assert len(groups) == 2
        assert groups[0]['source'] != groups[1]['source']
        assert all('500' in group['message'] for group in groups)
        assert {url for url in ('https://source.test/one','https://source.test/two') if any(url in group['message'] for group in groups)}=={'https://source.test/one','https://source.test/two'}
        assert all(g['count'] == 1 for g in groups)


def test_reusing_exception_instance_in_new_request_counts_a_new_occurrence(tmp_path):
    app = create_app(tmp_path, token='test-secret')
    failure = ValueError('same reusable error')
    @app.get('/api/v3/reused')
    async def reused():
        raise failure
    with TestClient(app, headers=HEADERS) as client:
        client.get('/api/v3/reused')
        client.get('/api/v3/reused')
        groups = client.get('/api/v3/diagnostics').json()['groups']
        assert len(groups) == 1 and groups[0]['count'] == 2


def test_http_error_type_is_not_framework_stream_context(tmp_path):
    app = create_app(tmp_path, token='test-secret')
    @app.get('/api/v3/fails')
    async def fails():
        raise ValueError('original root')
    with TestClient(app, headers=HEADERS) as client:
        client.get('/api/v3/fails')
        group = client.get('/api/v3/diagnostics').json()['groups'][0]
        assert group['error_type'] == 'ValueError'
        assert group['message'] == 'original root'


def test_complete_credentials_are_masked_in_persistent_group(tmp_path):
    message = 'password="correct horse battery staple" Authorization: Basic dXNlcjpwYXNz password=\'secret with spaces\''
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        client.post('/api/v3/diagnostics', json={'source': 'browser', 'message': message, 'traceback': message})
        shown = client.get('/api/v3/diagnostics').text
        disk = (tmp_path / 'debug.json').read_text()
        for leaked in ['horse', 'battery', 'staple', 'dXNlcjpwYXNz', 'with spaces']:
            assert leaked not in shown and leaked not in disk


def test_lost_delivery_stays_idempotent_after_many_other_tab_occurrences(tmp_path):
    payload = {'source': 'browser', 'message': 'same error', 'location': 'app.js:42', 'event_id': 'original', 'stream_id': 'tab-a', 'sequence': 1}
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        client.post('/api/v3/diagnostics', json=payload)
        for i in range(1, 301):
            client.post('/api/v3/diagnostics', json={**payload, 'stream_id': 'tab-b', 'sequence': i, 'event_id': f'other-{i}'})
        assert client.get('/api/v3/diagnostics').json()['groups'][0]['count'] == 301
        client.post('/api/v3/diagnostics', json=payload)
        assert client.get('/api/v3/diagnostics').json()['groups'][0]['count'] == 301
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        client.post('/api/v3/diagnostics', json=payload)
        assert client.get('/api/v3/diagnostics').json()['groups'][0]['count'] == 301


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
        presence(client, 'reader')
        with ThreadPoolExecutor(1) as pool:
            response = pool.submit(client.post, '/api/v3/processors/codex/check')
            assert service.started.wait(2)
            client.post('/api/v3/stop')
            stopped = response.result(timeout=2)
            assert stopped.status_code == 409
            assert stopped.json()['code'] == 'runtime_stopped'
        assert client.get('/api/v3/diagnostics').json()['groups'] == []


def test_browser_iso_timestamps_survive_http_delivery_and_retry(tmp_path):
    payload = dict(source='browser', message='dated error', event_id='dated',
                   first_at='2026-10-13T10:00:00.000Z', occurred_at='2026-10-13T10:00:01.250Z')
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        response = client.post('/api/v3/diagnostics', json=payload)
        assert response.status_code == 200, response.text
        entry = response.json()['entry']
        assert entry['first_at'] == '2026-10-13T10:00:00Z'
        assert entry['last_at'] == '2026-10-13T10:00:01.250000Z'
        assert client.post('/api/v3/diagnostics', json=payload).json()['entry']['count'] == 1
        for invalid in (123, True, 'not-a-date', '2026-10-14T12:00:00'):
            assert client.post('/api/v3/diagnostics', json={**payload, 'occurred_at': invalid}).status_code == 422
