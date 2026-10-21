"""Collection progress and original request diagnostics through the reader HTTP interface."""
import threading
import asyncio
from fastapi.testclient import TestClient
from oko.collection.demo import DemoSource
from tests.support.app import create_app
from tests.support.presence import presence
from tests.support.reader import wait_for, HEADERS


def test_collection_attempt_is_shared_and_refresh_does_not_restart_it(tmp_path):
    started, release = threading.Event(), threading.Event()

    class SlowSource(DemoSource):
        async def discover(self, cursor):
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return await super().discover(cursor)

    with TestClient(create_app(tmp_path, token='test', source=SlowSource(), api_only=True), headers=HEADERS) as client:
        presence(client, 'reader')
        assert started.wait(3)
        response = client.get('/api/v3/collection-status')
        assert response.status_code == 200, response.text
        state = response.json()
        source = next(s for s in state['sources'] if s['feed_id'] == 'demo')
        assert source['state'] == 'collecting'
        assert source['operation']['kind'] == 'discover'
        attempt = source['operation']['attempt_id']
        assert attempt
        assert source['operation']['elapsed_seconds'] >= 0
        assert client.post('/api/v3/collect').status_code == 200
        again = client.get('/api/v3/collection-status').json()
        assert again['sources'][0]['operation']['attempt_id'] == attempt
        details = client.get('/api/v3/collection-status/demo').json()
        assert details['operations'][0]['attempt_id'] == attempt
        assert details['instance_id'] == state['instance_id']
        assert details['revision'] >= state['revision']
        release.set()
        wait_for(client, lambda: all(s['state'] == 'complete' for s in client.get('/api/v3/collection-status').json()['sources']))
        assert client.get('/api/v3/collection-status/missing').status_code == 404


def test_collection_wait_keeps_error_across_power_restart_and_resolves_on_success(tmp_path):
    from oko.collection.contracts import SourceError
    from tests.support.reader import Clock
    clock = Clock()
    failed = threading.Event()

    class RetryingSource(DemoSource):
        async def discover(self, cursor):
            if not failed.is_set():
                failed.set()
                raise SourceError('temporary source failure', delay=60)
            return await super().discover(cursor)

    options = dict(token='test', source=RetryingSource(), clock=clock, wall_clock=lambda:1800000000+clock.now, api_only=True)
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        source = wait_for(client, lambda: next((s for s in client.get('/api/v3/collection-status').json()['sources'] if s['state']=='waiting'), None))
        attempt = source['operation']['attempt_id']
        details = client.get('/api/v3/collection-status/demo').json()
        assert details['operations'][0]['last_error']['message'] == 'temporary source failure'
        assert details['operations'][0]['last_error']['requests'] == []
        assert client.put('/api/v3/power', json={'enabled':False}).status_code == 200
        paused = client.get('/api/v3/collection-status').json()['sources'][0]
        assert paused['state'] == 'paused'
        assert client.get('/api/v3/collection-status/demo').json()['last_error']['message'] == 'temporary source failure'
    clock.now += 100
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/collection-status').json()['sources'][0]['state'] == 'paused'
        assert client.get('/api/v3/collection-status/demo').json()['last_error']['message'] == 'temporary source failure'
        assert client.put('/api/v3/power', json={'enabled':True}).status_code == 200
        wait_for(client, lambda: all(s['state']=='complete' for s in client.get('/api/v3/collection-status').json()['sources']))
        details = client.get('/api/v3/collection-status/demo').json()
        assert details['last_error']['resolved'] is True
        assert details['last_error']['attempt_id'] == attempt
        assert details['source']['last_success_at']


def test_collection_cancelled_download_freezes_attempt_and_start_has_new_identity(tmp_path):
    from tests.support.reader import Clock
    clock = Clock()
    started = threading.Event()

    class SlowSource(DemoSource):
        async def discover(self, cursor):
            started.set()
            await asyncio.Event().wait()

    with TestClient(create_app(tmp_path, token='test', source=SlowSource(),clock=clock,api_only=True), headers=HEADERS) as client:
        assert started.wait(3)
        first = client.get('/api/v3/collection-status').json()['sources'][0]['operation']
        clock.now += 5
        assert client.put('/api/v3/power',json={'enabled':False}).status_code == 200
        paused = client.get('/api/v3/collection-status').json()['sources'][0]
        assert paused['state'] == 'paused'
        assert paused['operation']['elapsed_seconds'] == 5
        clock.now += 100
        assert client.get('/api/v3/collection-status').json()['sources'][0]['operation']['elapsed_seconds'] == 5
        started.clear()
        assert client.put('/api/v3/power',json={'enabled':True}).status_code == 200
        assert started.wait(3)
        second = client.get('/api/v3/collection-status').json()['sources'][0]['operation']
        assert second['attempt_id'] != first['attempt_id']
        assert second['elapsed_seconds'] == 0
