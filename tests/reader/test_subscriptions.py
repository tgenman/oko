from tests.support.cards import read_all_cards
from oko.collection.contracts import DiscoveryResult, FetchResult
from tests.support.presence import presence, close_presence
from tests.support.subscriptions import configured_sources
from pathlib import Path
from fastapi.testclient import TestClient
from tests.support.app import create_app

HEADERS = {'Authorization': 'Bearer test-token'}




def test_effective_fields_reset_and_feed_permissions_survive_restart(tmp_path: Path):
    configured_sources(tmp_path, [('https://source.test/example-feed','Saved','3 Политика')])
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]
        folder = feed['folder_id']
        url = '/api/v3/settings/feed/' + feed['id']
        assert client.patch('/api/v3/settings/oko/global', json={'theses': True, 'processor': 'llamacpp'}).status_code == 200
        assert client.patch('/api/v3/settings/folder/' + folder, json={'prompt_theses': 'Только темы', 'originals': False}).status_code == 200
        result = client.patch(url, json={'prompt_filters': 'Без рекламы'}).json()
        assert result['effective']['theses'] == {'value': True, 'origin':{'scope':'oko','owner':'global'}}
        assert result['effective']['prompt_theses']['value'] == 'Только темы'
        assert result['effective']['originals']['origin']['scope'] == 'folder'
        assert result['effective']['prompt_filters']['value'] == 'Без рекламы'
        assert client.patch(url, json={'prompt_theses': 'Коротко'}).json()['effective']['prompt_theses']['origin']['scope'] == 'feed'
        reset = client.patch(url, json={'prompt_theses': None}).json()
        assert reset['effective']['prompt_theses']['value'] == 'Только темы'
        assert reset['overrides'] == {'prompt_filters': 'Без рекламы'}
        patch = {'importance':'high','initial_days':7,'public_ai':True}
        assert client.patch('/api/v3/feeds/' + feed['id'], json=patch).status_code == 200
        assert client.patch('/api/v3/feeds/' + feed['id'], json={'transport':'telegram'}).status_code == 400
        assert client.patch(url, json={'importance': 'high'}).status_code == 422
        assert client.patch(url, json={'theses': 'yes'}).status_code == 422
        assert client.patch(url, json={'originals': 'wrong'}).status_code == 422
        assert client.patch('/api/v3/feeds/' + feed['id'], json={'folder_id': 'missing'}).status_code == 400
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        restored = next(f for f in client.get('/api/v3/subscriptions').json()['feeds'] if f['id'] == feed['id'])
        for field, value in patch.items():
            assert restored[field] == value
        assert client.get(url).json()['effective']['processor']['value'] == 'llamacpp'
        assert client.get(url).json()['effective']['prompt_filters']['value'] == 'Без рекламы'
        assert client.get('/api/v3/settings/folder/' + folder).json()['effective']['prompt_filters']['value'] == ''


def test_folders_and_time_sorting_are_independent_of_feed_importance(tmp_path: Path):
    import time
    from oko.collection.contracts import Publication, Material

    class Sample:
        changes = None
        def __init__(self, item, published):
            self.item, self.published = item, published

        async def discover(self, cursor):
            return DiscoveryResult(tuple([Publication(self.item, 'https://example.org/' + self.item, self.published)]), 'done')

        async def fetch(self, publication):
            return FetchResult(Material(self.item, 'Текст'))

    configured_sources(tmp_path, [('https://example.org/a.xml', 'A', '9 Второй'), ('https://example.org/b.xml', 'B', '1 Первый')])
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        feeds = client.get('/api/v3/subscriptions').json()['feeds']
        a, b = feeds
        client.patch('/api/v3/feeds/' + a['id'], json={'importance': 'high'})
    for feed, source in ((a, Sample('a', 100)), (b, Sample('b', 200))):
        with TestClient(create_app(tmp_path, source=source, source_feed=feed['id'], token='test-token'), headers=HEADERS) as client:
            presence(client, 'test')
            for _ in range(100):
                cards = read_all_cards(client, '/api/v3/cards')
                if any(c['title'] == source.item for c in cards):
                    break
                time.sleep(.02)
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        assert [c['title'] for c in read_all_cards(client, '/api/v3/cards')] == ['b', 'a']
        assert [c['title'] for c in read_all_cards(client, '/api/v3/cards?sort=oldest')] == ['a', 'b']
        assert client.get('/api/v3/cards?sort=importance').status_code == 422
        assert client.get('/api/v3/cards?sort=invalid').status_code == 422
        assert [c['title'] for c in read_all_cards(client, '/api/v3/cards?folder=' + a['folder_id'])] == ['a']
        folders = client.get('/api/v3/folders').json()
        assert [f['title'] for f in folders] == ['1 Первый', '9 Второй']
        assert [f['count'] for f in folders] == [1, 1]
        client.patch('/api/v3/feeds/' + a['id'], json={'folder_id': b['folder_id']})
        assert [f['count'] for f in client.get('/api/v3/folders').json()] == [2, 0]








def test_same_url_in_two_feeds_remains_in_each_folder_after_viewing_one(tmp_path: Path):
    import time
    from oko.collection.contracts import Publication, Material

    class SharedArticle:
        changes = None
        async def discover(self, cursor):
            return DiscoveryResult(tuple([Publication('same', 'https://example.org/shared', 100)]), 'done')

        async def fetch(self, publication):
            return FetchResult(Material('Общая статья', 'Текст статьи'))

    configured_sources(tmp_path, [('https://example.org/a', 'Источник', 'A'), ('https://example.org/b', 'Источник', 'B')])
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        feeds = client.get('/api/v3/subscriptions').json()['feeds']
    for feed in feeds:
        with TestClient(create_app(tmp_path, source=SharedArticle(), source_feed=feed['id'], token='test-token'), headers=HEADERS) as client:
            presence(client, 'test')
            for _ in range(100):
                cards = read_all_cards(client, '/api/v3/cards?folder=' + feed['folder_id'])
                if len(cards) == 1 and (client.get('/api/v3/materials/'+cards[0]['id']).json()['availability'] == 'available'):
                    break
                time.sleep(.02)
            assert len(cards) == 1 and (client.get('/api/v3/materials/'+cards[0]['id']).json()['availability'] == 'available')
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        cards = read_all_cards(client, '/api/v3/cards')
        assert len(cards) == 1
        first = cards[0]
        client.post('/api/v3/materials/' + first['id'] + '/viewed')
        remaining = read_all_cards(client, '/api/v3/cards')
        assert remaining == []
    with TestClient(create_app(tmp_path, source=SharedArticle(), source_feed=feeds[0]['id'], token='test-token'), headers=HEADERS) as client:
        presence(client, 'test')
        time.sleep(.1)
        assert len(read_all_cards(client, '/api/v3/cards')) == 1






def test_theses_default_is_youtube_only_and_explicit_override_can_be_reset(tmp_path: Path):
    configured_sources(tmp_path, [('https://source.test/feed', 'RSS', 'Папка'), ('https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa', 'YouTube', 'Папка'), ('https://source.test/example-feed', 'Telegram', 'Папка')])
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        result = client.get('/api/v3/subscriptions')
        assert result.status_code == 200
        feeds = result.json()['feeds']
        for feed in feeds:
            url = '/api/v3/settings/feed/' + feed['id']
            assert client.get(url).json()['effective']['theses'] == {'value': feed['kind']=='youtube', 'origin':{'scope':'default'}}
        url = '/api/v3/settings/feed/' + feeds[0]['id']
        assert client.patch(url, json={'theses': True}).json()['effective']['theses']['value'] is True
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        assert client.get(url).json()['effective']['theses']['value'] is True
        assert client.patch(url, json={'theses': None}).json()['effective']['theses']['value'] is False
