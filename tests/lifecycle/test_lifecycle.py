from tests.support.presence import presence, close_presence, new_presence
from tests.support.reader import Clock
import time
from pathlib import Path
from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.demo import DemoSource

HEADERS = {'Authorization': 'Bearer test-token'}


def test_two_tabs_last_close_and_resume_require_live_ui(tmp_path: Path):
    clock = Clock()
    exits = []
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test-token', clock=clock,
                               lease=6, grace=15, shutdown=lambda: exits.append(True)), headers=HEADERS) as client:
        time.sleep(.05)
        assert client.get('/api/v3/cards').json() == []
        presence(client, 'a')
        presence(client, 'b')
        close_presence(client, 'a')
        time.sleep(.2)
        assert client.get('/api/v3/status').json()['active_client_sessions'] == 1
        assert len(client.get('/api/v3/cards').json()) == 3
        clock.now += 10  # suspended loop: previous lease must not start work
        time.sleep(.05)
        assert client.get('/api/v3/status').json()['active_client_sessions'] == 0
        assert presence(client, 'b').status_code == 410
        new_presence(client, 'b')
        assert client.get('/api/v3/status').json()['active_client_sessions'] == 1
        close_presence(client, 'b')
        time.sleep(.05)
        clock.now += 15
        time.sleep(.05)
        assert exits == [True]
        assert client.get('/api/v3/status').json()['runtime_state'] == 'stopped'


def test_stop_all_tabs_immediately(tmp_path: Path):
    exits = []
    with TestClient(create_app(tmp_path, token='test-token', shutdown=lambda: exits.append(True)), headers=HEADERS) as client:
        presence(client, 'a')
        presence(client, 'b')
        assert client.post('/api/v3/stop').status_code == 200
        time.sleep(.05)
        assert exits == [True]
        assert presence(client, 'a').status_code == 409
