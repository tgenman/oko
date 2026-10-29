from tests.support.cards import read_all_cards
from tests.support.presence import presence, close_presence
from tests.support.sources import LateSource
from tests.support.reader import Clock, wait_for
import time
from tests.support.storage import database
from pathlib import Path
from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.demo import DemoSource

HEADERS = {'Authorization': 'Bearer test-token'}

def retained(root, identity):
    with database(root) as db:
        return db.execute('SELECT 1 FROM items WHERE id = %s',(identity,)).fetchone() is not None



def test_viewed_during_fetch_never_publishes_late_result(tmp_path: Path):
    with TestClient(create_app(tmp_path, source=LateSource(), token='test-token'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        for _ in range(100):
            cards = read_all_cards(client, '/api/v3/cards')
            if cards:
                break
            time.sleep(.02)
        assert len(cards) == 3
        item = cards[0]['id']
        viewed_source = cards[0]['source_url'].rsplit('/', 1)[1]
        client.post(f'/api/v3/materials/{item}/viewed')
        time.sleep(.05)
        assert client.get(f'/api/v3/publication/{item}').status_code == 404
        assert not retained(tmp_path,item)
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
            cards = read_all_cards(client, '/api/v3/cards')
            if len(cards) == 3 and all((client.get('/api/v3/publication/' + card['id']).json()['availability'] == 'available') for card in cards):
                break
            time.sleep(.02)
        item = cards[0]['id']
        viewed_source = cards[0]['source_url'].rsplit('/', 1)[1]
        client.post(f'/api/v3/materials/{item}/viewed')
        assert client.get(f'/api/v3/publication/{item}').status_code == 404
        assert any(p['code']=='feed_delete_failed' for p in client.get('/api/v3/status/details').json()['problems']['current'])
        assert retained(tmp_path,item)
        fail = False
        clock.now += 1.1
        wait_for(client, lambda: not any(p['code']=='feed_delete_failed' for p in client.get('/api/v3/status/details').json()['problems']['current']))
        assert not any(p['code']=='feed_delete_failed' for p in client.get('/api/v3/status/details').json()['problems']['current'])
        assert not retained(tmp_path,item)


def test_corrupt_database_is_reported_and_never_replaced(tmp_path: Path, postgres_url):
    import psycopg
    with psycopg.connect(postgres_url, autocommit=True) as db:
        db.execute("UPDATE oko_schema_migrations SET checksum='preserve-corruption-evidence' WHERE version=1")
    with TestClient(create_app(tmp_path, token='test-token', database_url=postgres_url), headers=HEADERS) as client:
        assert client.get('/api/v3/cards').status_code == 503
        assert client.get('/api/v3/status').json()['runtime_state']=='unavailable'
    with psycopg.connect(postgres_url, autocommit=True) as db:
        assert db.execute('SELECT checksum FROM oko_schema_migrations WHERE version=1').fetchone()[0] == 'preserve-corruption-evidence'


def test_explicitly_repeated_discovery_can_recreate_viewed_identity(tmp_path: Path):
    class RepeatingSource(DemoSource):
        def __init__(self):
            self.downloaded = []
        async def discover(self, cursor):
            return await super().discover(None)
        async def fetch(self, publication):
            self.downloaded.append(publication.id)
            return await super().fetch(publication)
    source = RepeatingSource()
    with TestClient(create_app(tmp_path, source=source, token='test-token'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        for _ in range(100):
            cards = read_all_cards(client, '/api/v3/cards')
            if len(cards) == 3 and all((client.get('/api/v3/publication/' + c['id']).json()['availability'] == 'available') for c in cards):
                break
            time.sleep(.02)
        item = cards[0]['id']
        viewed_source = cards[0]['source_url'].rsplit('/', 1)[1]
        client.post(f'/api/v3/materials/{item}/viewed')
    source.downloaded.clear()
    with TestClient(create_app(tmp_path, source=source, token='test-token'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        wait_for(client,lambda: len(read_all_cards(client, '/api/v3/cards'))==3 and viewed_source in source.downloaded)
        assert set(source.downloaded) <= {'1','2','3'}
        assert client.get(f'/api/v3/publication/{item}').status_code == 404


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
            cards = read_all_cards(client, '/api/v3/cards')
            if len(cards) == 3 and all((client.get('/api/v3/publication/' + c['id']).json()['availability'] == 'available') for c in cards):
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
        status = client.get('/api/v3/status/details').json()
        assert status['runtime_state']=='running'
        assert any(p['code']=='feed_delete_failed' for p in status['problems']['current'])
        assert source.calls == 0
        assert retained(tmp_path,item)
        fail = False
        clock.now += 1.2
        wait_for(client, lambda: source.calls == 1 and not retained(tmp_path,item))
        assert not retained(tmp_path,item)
        assert source.calls == 1
        assert not any(p['code']=='feed_delete_failed' for p in client.get('/api/v3/status/details').json()['problems']['current'])
