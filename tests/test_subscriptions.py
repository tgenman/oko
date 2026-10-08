from pathlib import Path

from fastapi.testclient import TestClient
from oko.app import create_app

HEADERS = {'Authorization': 'Bearer test-token'}
OPML = (Path(__file__).parent / 'fixtures' / 'subscriptions.opml').read_text()


def test_opml_requires_folder_choice_and_reimport_preserves_configuration(tmp_path: Path):
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        preview = client.post('/api/import/preview', json={'opml': OPML})
        assert preview.status_code == 200
        feeds = preview.json()['feeds']
        assert len(feeds) == 2
        telegram = next(f for f in feeds if f['kind'] == 'telegram')
        assert telegram['identity'] == 'telegram:example_channel'
        assert telegram['folders'] == ['2 Технологии', '3 Политика']
        assert client.post('/api/import', json={'opml': OPML}).status_code == 409
        assert client.get('/api/subscriptions').json()['feeds'] == []
        body = {'opml': OPML, 'choices': {telegram['identity']: '3 Политика'}}
        result = client.post('/api/import', json=body)
        assert result.status_code == 200
        saved = client.get('/api/subscriptions').json()
        tg = next(f for f in saved['feeds'] if f['kind'] == 'telegram')
        assert tg['initial_days'] == 1
        assert tg['public_ai'] is False and tg['read_history'] is False
        assert tg['auto_read'] is None and tg['connection'] == 'needs_connection'
        assert 'https://rsshub.example/telegram/channel/example_channel' in tg['original_urls']
        assert client.post('/api/import', json=body).status_code == 200
        assert len(client.get('/api/subscriptions').json()['feeds']) == 2
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        assert client.get('/api/subscriptions').json() == saved


def test_effective_fields_reset_and_feed_permissions_survive_restart(tmp_path: Path):
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        plan = client.post('/api/import/preview', json={'opml': OPML}).json()
        tg = next(f for f in plan['feeds'] if f['kind'] == 'telegram')
        saved = client.post('/api/import', json={'opml': OPML, 'choices': {tg['identity']: '3 Политика'}}).json()
        feed = next(f for f in saved['feeds'] if f['kind'] == 'telegram')
        folder = feed['folder_id']
        url = '/api/settings/feed/' + feed['id']
        assert client.patch('/api/settings/oko/global', json={'theses': True, 'processor': 'ollama:local'}).status_code == 200
        assert client.patch('/api/settings/folder/' + folder, json={'prompt_theses': 'Только темы', 'schedule': '18:30'}).status_code == 200
        result = client.patch(url, json={'digest_days': 7}).json()
        assert result['effective']['theses'] == {'value': True, 'scope': 'oko', 'owner': 'global'}
        assert result['effective']['prompt_theses']['value'] == 'Только темы'
        assert result['effective']['schedule']['scope'] == 'folder'
        assert result['effective']['digest_days']['value'] == 7
        assert client.patch(url, json={'prompt_theses': 'Коротко'}).json()['effective']['prompt_theses']['scope'] == 'feed'
        reset = client.patch(url, json={'prompt_theses': None}).json()
        assert reset['effective']['prompt_theses']['value'] == 'Только темы'
        assert reset['overrides'] == {'digest_days': 7}
        patch = {'importance': 'high', 'initial_days': 7, 'auto_read': False, 'public_ai': True, 'read_history': False, 'transport': 'telegram'}
        assert client.patch('/api/feeds/' + feed['id'], json=patch).status_code == 200
        assert client.patch(url, json={'importance': 'high'}).status_code == 422
        assert client.patch(url, json={'theses': 'yes'}).status_code == 422
        assert client.patch(url, json={'schedule': '25:30'}).status_code == 422
        assert client.patch('/api/feeds/' + feed['id'], json={'folder_id': 'missing'}).status_code == 404
        # Reimport cannot reset explicit choices or permissions.
        client.post('/api/import', json={'opml': OPML, 'choices': {tg['identity']: '3 Политика'}})
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        restored = next(f for f in client.get('/api/subscriptions').json()['feeds'] if f['id'] == feed['id'])
        for field, value in patch.items():
            assert restored[field] == value
        assert client.get(url).json()['effective']['processor']['value'] == 'ollama:local'
        assert client.get(url).json()['effective']['digest_days']['value'] == 7
        assert client.get('/api/settings/folder/' + folder).json()['effective']['digest_days']['value'] == 1


def test_folders_and_sorting_use_feed_importance_not_folder_prefix(tmp_path: Path):
    import time
    from oko.sources import Publication, Material

    class Sample:
        def __init__(self, item, published):
            self.item, self.published = item, published

        async def discover(self, cursor):
            return [Publication(self.item, 'https://example.org/' + self.item, self.published)], 'done'

        async def fetch(self, publication):
            return Material(self.item, 'Текст')

    opml = '<opml><body><outline text="9 Второй"><outline text="A" xmlUrl="https://example.org/a.xml"/></outline><outline text="1 Первый"><outline text="B" xmlUrl="https://example.org/b.xml"/></outline></body></opml>'
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        feeds = client.post('/api/import', json={'opml': opml}).json()['feeds']
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


def test_import_rejects_unsafe_xml_and_explains_ambiguous_telegram(tmp_path: Path):
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        assert client.post('/api/import', json={'opml': '<!DOCTYPE opml [<!ENTITY x "bad">]><opml><body/></opml>'}).status_code == 400
        assert client.post('/api/import', json={'opml': '<opml>'}).status_code == 400
        assert client.post('/api/import', json={'opml': '<opml><body><outline xmlUrl="file:///etc/passwd"/></body></opml>'}).status_code == 400
        assert client.get('/api/subscriptions').json()['feeds'] == []
        result = client.post('/api/import', json={'opml': '<opml><body><outline text="Вложенная"><outline text="Папка"><outline text="Неоднозначный TG" xmlUrl="https://rsshub.example/telegram/channel/123?limit=50"/></outline></outline></body></opml>'}).json()
        feed = result['feeds'][0]
        assert feed['kind'] == 'unresolved' and feed['connection'] == 'ambiguous'
        assert [f['title'] for f in result['folders']] == ['Вложенная', 'Вложенная / Папка']
        assert client.patch('/api/feeds/' + feed['id'], json={'transport': 'telegram'}).status_code == 400
        assert client.patch('/api/feeds/' + feed['id'], json={'transport': 'rss'}).json()['transport'] == 'rss'
        assert client.patch('/api/feeds/' + feed['id'], json={'public_ai': 'true'}).status_code == 422


def test_new_folder_on_reimport_requires_choice_with_existing_folder(tmp_path: Path):
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        original = '<opml><body><outline text="Первая"><outline text="RSS" xmlUrl="https://example.org/rss"/></outline></body></opml>'
        feed = client.post('/api/import', json={'opml': original}).json()['feeds'][0]
        changed = original.replace('Первая', 'Другая')
        conflict = client.post('/api/import', json={'opml': changed})
        assert conflict.status_code == 409
        assert conflict.json()['detail']['conflicts'][0]['folders'] == ['Другая', 'Первая']
        saved = client.post('/api/import', json={'opml': changed, 'choices': {feed['identity']: 'Первая'}}).json()
        assert len(saved['feeds']) == 1 and saved['feeds'][0]['folder_id'] == feed['folder_id']


def test_rsshub_display_options_keep_channel_identity_and_conflict(tmp_path: Path):
    opml = '''<opml><body><outline text="Первая"><outline xmlUrl="https://rsshub.example/telegram/channel/example_channel"/></outline><outline text="Другая"><outline xmlUrl="https://rsshub.example/telegram/channel/example_channel/showLinkPreview=1&amp;showFwdFrom=1&amp;showMediaTagAsEmoji=1"/></outline></body></opml>'''
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        feeds = client.post('/api/import/preview', json={'opml': opml}).json()['feeds']
        assert len(feeds) == 1
        assert feeds[0]['kind'] == 'telegram'
        assert feeds[0]['folders'] == ['Первая', 'Другая']
        assert len(feeds[0]['original_urls']) == 2
        assert client.post('/api/import', json={'opml': opml}).status_code == 409
