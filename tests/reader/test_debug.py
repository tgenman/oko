from tests.support.presence import presence
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


def test_complete_credentials_are_masked_in_persistent_group(tmp_path):
    message = 'password="correct horse battery staple" Authorization: Basic dXNlcjpwYXNz password=\'secret with spaces\''
    with TestClient(create_app(tmp_path, token='test-secret'), headers=HEADERS) as client:
        client.post('/api/v3/diagnostics', json={'source': 'browser', 'message': message, 'traceback': message})
        shown = client.get('/api/v3/diagnostics').text
        disk = (tmp_path / 'debug.json').read_text()
        for leaked in ['horse', 'battery', 'staple', 'dXNlcjpwYXNz', 'with spaces']:
            assert leaked not in shown and leaked not in disk
