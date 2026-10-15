from tests.support.subscriptions import saved_sources
from pathlib import Path
from fastapi.testclient import TestClient
from tests.support.app import create_app

HEADERS = {'Authorization': 'Bearer test-token'}




def test_effective_fields_reset_and_feed_permissions_survive_restart(tmp_path: Path):
    saved_sources(tmp_path, [('https://t.me/example_channel','Saved','3 Политика')])
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        feed = client.get('/api/subscriptions').json()['feeds'][0]
        folder = feed['folder_id']
        url = '/api/settings/feed/' + feed['id']
        assert client.patch('/api/settings/oko/global', json={'theses': True, 'processor': 'llamacpp:local'}).status_code == 200
        assert client.patch('/api/settings/folder/' + folder, json={'prompt_theses': 'Только темы', 'originals': False}).status_code == 200
        result = client.patch(url, json={'prompt_filters': 'Без рекламы'}).json()
        assert result['effective']['theses'] == {'value': True, 'scope': 'oko', 'owner': 'global'}
        assert result['effective']['prompt_theses']['value'] == 'Только темы'
        assert result['effective']['originals']['scope'] == 'folder'
        assert result['effective']['prompt_filters']['value'] == 'Без рекламы'
        assert client.patch(url, json={'prompt_theses': 'Коротко'}).json()['effective']['prompt_theses']['scope'] == 'feed'
        reset = client.patch(url, json={'prompt_theses': None}).json()
        assert reset['effective']['prompt_theses']['value'] == 'Только темы'
        assert reset['overrides'] == {'prompt_filters': 'Без рекламы'}
        patch = {'importance': 'high', 'initial_days': 7, 'auto_read': False, 'public_ai': True, 'read_history': False}
        assert client.patch('/api/feeds/' + feed['id'], json=patch).status_code == 200
        assert client.patch('/api/feeds/' + feed['id'], json={'transport':'telegram'}).status_code == 400
        assert client.patch(url, json={'importance': 'high'}).status_code == 422
        assert client.patch(url, json={'theses': 'yes'}).status_code == 422
        assert client.patch(url, json={'originals': 'wrong'}).status_code == 422
        assert client.patch('/api/feeds/' + feed['id'], json={'folder_id': 'missing'}).status_code == 404
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        restored = next(f for f in client.get('/api/subscriptions').json()['feeds'] if f['id'] == feed['id'])
        for field, value in patch.items():
            assert restored[field] == value
        assert client.get(url).json()['effective']['processor']['value'] == 'llamacpp:local'
        assert client.get(url).json()['effective']['prompt_filters']['value'] == 'Без рекламы'
        assert client.get('/api/settings/folder/' + folder).json()['effective']['prompt_filters']['value'] == ''


def test_folders_and_sorting_use_feed_importance_not_folder_prefix(tmp_path: Path):
    import time
    from oko.collection.contracts import Publication, Material

    class Sample:
        def __init__(self, item, published):
            self.item, self.published = item, published

        async def discover(self, cursor):
            return [Publication(self.item, 'https://example.org/' + self.item, self.published)], 'done'

        async def fetch(self, publication):
            return Material(self.item, 'Текст')

    saved_sources(tmp_path, [('https://example.org/a.xml', 'A', '9 Второй'), ('https://example.org/b.xml', 'B', '1 Первый')])
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        feeds = client.get('/api/subscriptions').json()['feeds']
        a, b = feeds
        client.patch('/api/feeds/' + a['id'], json={'importance': 'high'})
    for feed, source in ((a, Sample('a', 100)), (b, Sample('b', 200))):
        with TestClient(create_app(tmp_path, source=source, source_feed=feed['id'], token='test-token'), headers=HEADERS) as client:
            client.post('/api/tabs/test/heartbeat')
            for _ in range(100):
                cards = client.get('/api/cards').json()
                if any(c['title'] == source.item for c in cards):
                    break
                time.sleep(.02)
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        assert [c['title'] for c in client.get('/api/cards').json()] == ['b', 'a']
        assert [c['title'] for c in client.get('/api/cards?sort=oldest').json()] == ['a', 'b']
        assert [c['title'] for c in client.get('/api/cards?sort=importance').json()] == ['a', 'b']
        assert client.get('/api/cards?sort=invalid').status_code == 422
        assert [c['title'] for c in client.get('/api/cards?folder=' + a['folder_id']).json()] == ['a']
        folders = client.get('/api/folders').json()
        assert [f['title'] for f in folders] == ['1 Первый', '9 Второй']
        assert [f['count'] for f in folders] == [1, 1]
        client.patch('/api/feeds/' + a['id'], json={'folder_id': b['folder_id']})
        assert [f['count'] for f in client.get('/api/folders').json()] == [2, 0]








def test_same_url_in_two_feeds_remains_in_each_folder_after_viewing_one(tmp_path: Path):
    import time
    from oko.collection.contracts import Publication, Material

    class SharedArticle:
        async def discover(self, cursor):
            return [Publication('same', 'https://example.org/shared', 100)], 'done'

        async def fetch(self, publication):
            return Material('Общая статья', 'Текст статьи')

    saved_sources(tmp_path, [('https://example.org/a', 'Источник', 'A'), ('https://example.org/b', 'Источник', 'B')])
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        feeds = client.get('/api/subscriptions').json()['feeds']
    for feed in feeds:
        with TestClient(create_app(tmp_path, source=SharedArticle(), source_feed=feed['id'], token='test-token'), headers=HEADERS) as client:
            client.post('/api/tabs/test/heartbeat')
            for _ in range(100):
                cards = client.get('/api/cards?folder=' + feed['folder_id']).json()
                if len(cards) == 1 and cards[0]['complete']:
                    break
                time.sleep(.02)
            assert len(cards) == 1 and cards[0]['complete']
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        cards = client.get('/api/cards').json()
        assert len(cards) == 2
        first = next(c for c in cards if c['feed_id'] == feeds[0]['id'])
        client.post('/api/materials/' + first['id'] + '/viewed')
        remaining = client.get('/api/cards').json()
        assert len(remaining) == 1 and remaining[0]['feed_id'] == feeds[1]['id']
    with TestClient(create_app(tmp_path, source=SharedArticle(), source_feed=feeds[0]['id'], token='test-token'), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        time.sleep(.1)
        assert len(client.get('/api/cards').json()) == 1


def test_upgrade_preserves_old_reader_and_pending_deletion(tmp_path: Path):
    import hashlib
    import json
    import sqlite3
    import time
    from oko.collection.demo import DemoSource

    first = hashlib.sha256(b'demo:1').hexdigest()[:32]
    second = hashlib.sha256(b'demo:2').hexdigest()[:32]
    # Storage is an agreed system boundary: this is an input from release 01,
    # not a query into the implementation under test.
    with sqlite3.connect(tmp_path / 'state.sqlite') as db:
        db.executescript('''CREATE TABLE items (id TEXT PRIMARY KEY, source_id TEXT UNIQUE,
        url TEXT UNIQUE,published REAL,viewed INTEGER DEFAULT 0,complete INTEGER DEFAULT 0);
        CREATE TABLE jobs (id INTEGER PRIMARY KEY,kind TEXT,item TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',attempt INTEGER DEFAULT 0,next REAL DEFAULT 0,
        generation INTEGER DEFAULT 0,error TEXT,UNIQUE(kind,item));
        CREATE TABLE state (key TEXT PRIMARY KEY,value TEXT);''')
        db.execute('INSERT INTO items VALUES (?,?,?,100,0,1)', (first, '1', 'https://example.org/oko/1'))
        db.execute('INSERT INTO items VALUES (?,?,?,NULL,1,0)', (second, '2', 'https://example.org/oko/2'))
        db.execute("INSERT INTO jobs(kind,item) VALUES ('delete',?)", (second,))
        db.execute("INSERT INTO state VALUES ('cursor','3')")
    for item in (first, second):
        path = tmp_path / 'content' / item / '1'
        path.mkdir(parents=True)
        (path / 'original.md').write_text(json.dumps({'title': 'Сохранённый материал'}) + '\n\nИсходный текст')
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test-token'), headers=HEADERS) as client:
        cards = client.get('/api/cards').json()
        assert len(cards) == 1 and cards[0]['id'] == first
        assert client.get('/api/materials/' + first).json()['text'] == 'Исходный текст'
        assert client.get('/api/materials/' + second).status_code == 404
        assert client.get('/api/status').json()['deleting'] is True
        client.post('/api/tabs/test/heartbeat')
        for _ in range(100):
            if not client.get('/api/status').json()['deleting']:
                break
            time.sleep(.02)
        assert not (tmp_path / 'content' / second).exists()
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        assert client.get('/api/materials/' + first).json()['text'] == 'Исходный текст'
        assert client.get('/api/materials/' + second).status_code == 404




def test_theses_are_enabled_by_default_for_all_feeds_and_can_be_disabled(tmp_path: Path):
    saved_sources(tmp_path, [('https://source.test/feed', 'RSS', 'Папка'), ('https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa', 'YouTube', 'Папка'), ('https://t.me/example_channel', 'Telegram', 'Папка')])
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        result = client.get('/api/subscriptions')
        assert result.status_code == 200
        feeds = result.json()['feeds']
        for feed in feeds:
            url = '/api/settings/feed/' + feed['id']
            assert client.get(url).json()['effective']['theses'] == {'value': True, 'scope': 'default', 'owner': None}
        url = '/api/settings/feed/' + feeds[0]['id']
        assert client.patch(url, json={'theses': False}).json()['effective']['theses']['value'] is False
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        assert client.get(url).json()['effective']['theses']['value'] is False
        assert client.patch(url, json={'theses': None}).json()['effective']['theses']['value'] is True
