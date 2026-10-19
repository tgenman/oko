from oko.collection.contracts import FetchResources
from tests.support.presence import presence, close_presence
from tests.support.sources import LateSource
from tests.support.reader import Clock, wait_for
import time
from pathlib import Path
from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.demo import DemoSource

HEADERS = {'Authorization': 'Bearer test-token'}


def test_viewed_during_fetch_never_publishes_late_result(tmp_path: Path):
    with TestClient(create_app(tmp_path, source=LateSource(), token='test-token'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        for _ in range(100):
            cards = client.get('/api/v3/cards').json()
            if cards:
                break
            time.sleep(.02)
        assert len(cards) == 3
        item = cards[0]['id']
        viewed_source = cards[0]['source_url'].rsplit('/', 1)[1]
        client.post(f'/api/v3/materials/{item}/viewed')
        time.sleep(.05)
        assert client.get(f'/api/v3/materials/{item}').status_code == 404
        assert not (tmp_path / 'content' / item).exists()
        assert all(b'Late secret' not in p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())


def test_delete_failure_is_visible_and_retried_without_content_in_errors(tmp_path: Path):
    clock = Clock()
    fail = True
    def fault(point):
        if point == 'before_delete' and fail:
            raise OSError('secret content in provider exception')
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test-token', fault=fault, clock=clock), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        for _ in range(100):
            cards = client.get('/api/v3/cards').json()
            if len(cards) == 3 and all((card['availability'] == 'available') for card in cards):
                break
            time.sleep(.02)
        item = cards[0]['id']
        viewed_source = cards[0]['source_url'].rsplit('/', 1)[1]
        client.post(f'/api/v3/materials/{item}/viewed')
        assert client.get(f'/api/v3/materials/{item}').status_code == 404
        assert any(p['code']=='feed_delete_failed' for p in client.get('/api/v3/status').json()['problems']['current'])
        assert (tmp_path / 'content' / item).exists()
        fail = False
        clock.now += 1.1
        wait_for(client, lambda: not any(p['code']=='feed_delete_failed' for p in client.get('/api/v3/status').json()['problems']['current']))
        assert not any(p['code']=='feed_delete_failed' for p in client.get('/api/v3/status').json()['problems']['current'])
        assert not (tmp_path / 'content' / item).exists()


def test_corrupt_database_is_reported_and_never_replaced(tmp_path: Path):
    original = b'not a sqlite database, keep this evidence'
    (tmp_path / 'state.sqlite').write_bytes(original)
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        assert client.get('/api/v3/cards').status_code == 503
        assert client.get('/api/v3/status').json()['runtime_state']=='unavailable'
    assert (tmp_path / 'state.sqlite').read_bytes() == original


def test_repeated_discovery_does_not_download_viewed_identity(tmp_path: Path):
    class RepeatingSource(DemoSource):
        def __init__(self):
            self.downloaded = []
        async def discover(self, cursor):
            return await super().discover(None)
        async def fetch(self, publication, resources=FetchResources()):
            self.downloaded.append(publication.id)
            return await super().fetch(publication)
    source = RepeatingSource()
    with TestClient(create_app(tmp_path, source=source, token='test-token'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        for _ in range(100):
            cards = client.get('/api/v3/cards').json()
            if len(cards) == 3 and all((c['availability'] == 'available') for c in cards):
                break
            time.sleep(.02)
        item = cards[0]['id']
        viewed_source = cards[0]['source_url'].rsplit('/', 1)[1]
        client.post(f'/api/v3/materials/{item}/viewed')
    source.downloaded.clear()
    with TestClient(create_app(tmp_path, source=source, token='test-token'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        time.sleep(.2)
        assert viewed_source not in source.downloaded
        assert set(source.downloaded) <= {'1', '2', '3'} - {viewed_source}
        assert len(client.get('/api/v3/cards').json()) == 2
        assert client.get(f'/api/v3/materials/{item}').status_code == 404


def test_restart_keeps_failed_deletion_retryable_and_blocks_collection(tmp_path: Path):
    class CountingSource(DemoSource):
        def __init__(self):
            self.calls = 0
        async def discover(self, cursor):
            self.calls += 1
            return await super().discover(cursor)
    clock = Clock()
    fail = True
    def fault(point):
        if point == 'before_delete' and fail:
            raise OSError('temporary disk error')
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test-token', fault=fault, clock=clock), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        for _ in range(100):
            cards = client.get('/api/v3/cards').json()
            if len(cards) == 3 and all((c['availability'] == 'available') for c in cards):
                break
            time.sleep(.02)
        item = cards[0]['id']
        viewed_source = cards[0]['source_url'].rsplit('/', 1)[1]
        client.post(f'/api/v3/materials/{item}/viewed')
    source = CountingSource()
    with TestClient(create_app(tmp_path, source=source, token='test-token', fault=fault, clock=clock), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        time.sleep(.1)
        status = client.get('/api/v3/status').json()
        assert status['runtime_state']=='running'
        assert any(p['code']=='feed_delete_failed' for p in status['problems']['current'])
        assert source.calls == 0
        assert (tmp_path / 'content' / item).exists()
        fail = False
        clock.now += 1.2
        wait_for(client, lambda: source.calls == 1 and not (tmp_path / 'content' / item).exists())
        assert not (tmp_path / 'content' / item).exists()
        assert source.calls == 1
        assert not any(p['code']=='feed_delete_failed' for p in client.get('/api/v3/status').json()['problems']['current'])
