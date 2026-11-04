import time
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import Clock, wait_for
from tests.support.cards import read_all_cards
from tests.support.demo_source import DemoSource

HEADERS = {'Authorization': 'Bearer test-token'}


def test_collection_and_process_continue_without_any_browser(tmp_path):
    clock = Clock()
    exits = []
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test-token', **clock.options(),
                               lease=6, grace=15, shutdown=lambda: exits.append(True)), headers=HEADERS) as client:
        wait_for(client, lambda: len(read_all_cards(client, '/api/v3/cards')) == 3)
        clock.now += 3600
        time.sleep(.1)
        for path, method in [('/api/v3/client-sessions','post'),('/api/v3/client-sessions/old','delete')]:
            response = getattr(client, method)(path)
            assert response.status_code == 409
            assert response.json()['code'] == 'lifetime_policy_incompatible'
        state = client.get('/api/v3/status').json()
        assert state['lifetime'] == {'kind': 'explicit'}
        assert state['active_client_sessions'] == 0
        assert state['work_allowed'] is True
        assert state['runtime_state'] == 'running'
        assert exits == []
        assert client.post('/api/v3/stop').status_code == 200
        wait_for(client, lambda: exits)
        assert client.get('/api/v3/status').json()['runtime_state'] == 'stopped'


def test_power_remains_stopped_without_browser_and_after_restart(tmp_path):
    clock = Clock()
    with TestClient(create_app(tmp_path, token='test-token', **clock.options()), headers=HEADERS) as client:
        assert client.put('/api/v3/power', json={'enabled':False}).status_code == 200
        clock.now += 86400
        assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        assert client.get('/api/v3/status').json()['work_allowed'] is False
        assert client.put('/api/v3/power', json={'enabled':True}).status_code == 200
        assert client.get('/api/v3/status').json()['work_allowed'] is True


def test_processing_completes_without_browser_after_restart(tmp_path):
    from tests.support.models import model_transport
    calls = []
    with TestClient(create_app(tmp_path, token='test-token', ai_transport=model_transport(calls)), headers=HEADERS) as client:
        assert client.put('/api/v3/processors/llamacpp', json={'endpoint':'http://127.0.0.1:8080','model':'local:latest','local_execution':True}).status_code == 200
        assert client.patch('/api/v3/settings/oko/global', json={'processor':'llamacpp','theses':True,'prompt_filters':''}).status_code == 200
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test-token', ai_transport=model_transport(calls)), headers=HEADERS) as client:
        cards = wait_for(client, lambda: read_all_cards(client, '/api/v3/cards'))
        wait_for(client, lambda: client.get('/api/v3/publication/'+cards[0]['id']).json().get('theses',{}).get('result'))
        assert calls
        assert client.get('/api/v3/status').json()['active_client_sessions'] == 0
