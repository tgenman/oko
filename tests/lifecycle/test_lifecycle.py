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
        assert client.get('/api/cards').json() == []
        client.post('/api/tabs/a/heartbeat')
        client.post('/api/tabs/b/heartbeat')
        client.post('/api/tabs/a/close')
        time.sleep(.2)
        assert client.get('/api/status').json()['live_tabs'] == 1
        assert len(client.get('/api/cards').json()) == 3
        clock.now += 10  # suspended loop: previous lease must not start work
        time.sleep(.05)
        assert client.get('/api/status').json()['live_tabs'] == 0
        client.post('/api/tabs/b/heartbeat')
        assert client.get('/api/status').json()['live_tabs'] == 1
        client.post('/api/tabs/b/close')
        time.sleep(.05)
        clock.now += 15
        time.sleep(.05)
        assert exits == [True]
        assert client.get('/api/status').json()['stopped']


def test_stop_all_tabs_immediately(tmp_path: Path):
    exits = []
    with TestClient(create_app(tmp_path, token='test-token', shutdown=lambda: exits.append(True)), headers=HEADERS) as client:
        client.post('/api/tabs/a/heartbeat')
        client.post('/api/tabs/b/heartbeat')
        assert client.post('/api/stop').status_code == 200
        time.sleep(.05)
        assert exits == [True]
        assert client.post('/api/tabs/a/heartbeat').status_code == 409
