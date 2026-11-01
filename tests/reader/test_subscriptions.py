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
