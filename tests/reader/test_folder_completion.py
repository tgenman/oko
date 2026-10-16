from tests.support.presence import presence, close_presence
from pathlib import Path
from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.demo import DemoSource
from tests.support.processes import wait_cards

HEADERS = {'Authorization': 'Bearer test-token'}


def test_folder_confirmation_deletes_all_originals_and_is_repeatable(tmp_path: Path):
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test-token'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'a')
        originals = wait_cards(client, 3)
        response = client.post('/api/v3/folders/demo/completion/preview')
        assert response.status_code == 200
        preview = response.json()
        assert preview['cards_count'] == 3 and preview['publications_count'] == 3
        assert len(client.get('/api/v3/cards').json()) == 3
        for _ in range(2):
            response = client.post('/api/v3/folders/demo/completion', json={'token': preview['token']})
            assert response.status_code == 200
        assert client.get('/api/v3/cards').json() == []
        for item in originals:
            assert client.get('/api/v3/materials/' + item['id']).status_code == 404
            assert not (tmp_path / 'content' / item['id']).exists()


def test_new_arrivals_require_new_confirmation_and_old_retry_leaves_them(tmp_path):
    import httpx
    from tests.support.rss import rss, import_feed, wait_cards as rss_cards
    current = [rss()]
    def response(request):
        return httpx.Response(200, text=current[0] if request.url.path == '/feed' else '<article>Текст</article>')
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        feed = import_feed(client)
        folder = feed['folder_id']
        presence(client, 'a')
        rss_cards(client, 1)
        preview = client.post(f'/api/v3/folders/{folder}/completion/preview').json()
        current[0] = rss(guid='two', url='https://source.test/two')
        client.post('/api/v3/collect')
        rss_cards(client, 2)
        changed = client.post(f'/api/v3/folders/{folder}/completion', json={'token': preview['token']})
        assert changed.status_code == 409
        assert changed.json()['details']['preview']['cards_count'] == 2
        assert len(client.get('/api/v3/cards').json()) == 2
        accepted = changed.json()['details']['preview']['token']
        assert client.post(f'/api/v3/folders/{folder}/completion', json={'token': accepted}).status_code == 200
        current[0] = rss(guid='three', url='https://source.test/three')
        client.post('/api/v3/collect')
        remaining = rss_cards(client, 1)
        assert client.post(f'/api/v3/folders/{folder}/completion', json={'token': accepted}).status_code == 200
        assert client.get('/api/v3/cards').json()[0]['id'] == remaining[0]['id']


def test_other_folder_and_wrong_folder_confirmation_are_untouched(tmp_path):
    import httpx
    from tests.support.rss import rss, import_feed, wait_cards as rss_cards
    def response(request):
        return httpx.Response(200, text=rss() if request.url.path == '/feed' else '<article>Другой материал</article>')
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        import_feed(client)
        other = next(f['folder_id'] for f in client.get('/api/v3/subscriptions').json()['feeds'] if f['id'] != 'demo')
        presence(client, 'a')
        rss_cards(client, 4)
        preview = client.post('/api/v3/folders/demo/completion/preview').json()
        assert preview['cards_count'] == 3
        assert client.post(f'/api/v3/folders/{other}/completion', json={'token': preview['token']}).status_code == 409
        assert len(client.get('/api/v3/cards').json()) == 4
        assert client.post('/api/v3/folders/demo/completion', json={'token': preview['token']}).status_code == 200
        assert len(client.get('/api/v3/cards').json()) == 1
        assert len(client.get('/api/v3/cards?folder=' + other).json()) == 1




def test_delete_failure_is_reported_then_recovered_before_collection(tmp_path):
    from tests.support.reader import wait_for
    fail = [True]
    def fault(point):
        if point == 'before_delete' and fail[0]:
            raise OSError('disk error')
    options = dict(source=DemoSource(), token='test-token', fault=fault)
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'a')
        wait_cards(client, 3)
        preview = client.post('/api/v3/folders/demo/completion/preview').json()
        assert client.post('/api/v3/folders/demo/completion', json={'token': preview['token']}).status_code == 503
        assert client.get('/api/v3/cards').json() == []
        assert any(p['code']=='feed_delete_failed' for p in client.get('/api/v3/status').json()['problems']['current'])
    class Counting(DemoSource):
        calls = 0
        async def discover(self, cursor):
            self.calls += 1
            return await super().discover(cursor)
    source = Counting()
    with TestClient(create_app(tmp_path, source=source, token='test-token', fault=fault), headers=HEADERS) as client:
        presence(client, 'b')
        assert client.get('/api/v3/cards').json() == []
        assert source.calls == 0
        fail[0] = False
        wait_for(client, lambda: not list((tmp_path/'content').iterdir()))
        assert not list((tmp_path / 'content').iterdir())
        assert client.get('/api/v3/cards').json() == []


def test_folder_deletion_cancels_late_fetch(tmp_path):
    from tests.support.sources import LateSource
    from tests.support.reader import wait_for
    with TestClient(create_app(tmp_path, source=LateSource(), token='test-token'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'a')
        wait_for(client, lambda: len(client.get('/api/v3/cards').json()) == 3)
        preview = client.post('/api/v3/folders/demo/completion/preview').json()
        assert preview['publications_count'] == 3
        assert client.post('/api/v3/folders/demo/completion', json={'token': preview['token']}).status_code == 200
        assert client.get('/api/v3/cards').json() == []
        assert all(b'Late secret' not in p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())




def test_removed_member_requires_recount_and_restart_requires_fresh_preview(tmp_path):
    options = dict(source=DemoSource(), token='test-token')
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'a')
        cards = wait_cards(client, 3)
        preview = client.post('/api/v3/folders/demo/completion/preview').json()
        client.post('/api/v3/materials/' + cards[0]['id'] + '/viewed')
        changed = client.post('/api/v3/folders/demo/completion', json={'token': preview['token']})
        assert changed.status_code == 409
        assert changed.json()['details']['preview']['publications_count'] == 2
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        result = client.post('/api/v3/folders/demo/completion', json={'token': preview['token']})
        assert result.status_code == 409
        assert result.json()['details']['preview']['publications_count'] == 2
        assert len(client.get('/api/v3/cards').json()) == 2
        assert client.post('/api/v3/folders/missing/completion/preview').status_code == 404






def test_unreadable_original_still_belongs_to_folder_completion(tmp_path):
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test-token'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'a')
        originals = wait_cards(client, 3)
        broken = originals[0]['id']
        # Corrupt the external content file; observe everything through the reader.
        (tmp_path / 'content' / broken / '1' / 'original.md').write_text('unreadable')
        assert client.get('/api/v3/materials/' + broken).status_code == 404
        assert len(client.get('/api/v3/cards').json()) == 2
        assert next(f['count'] for f in client.get('/api/v3/folders').json() if f['id'] == 'demo') == 3
        preview = client.post('/api/v3/folders/demo/completion/preview').json()
        assert preview['cards_count'] == 2 and preview['publications_count'] == 3
        assert client.post('/api/v3/folders/demo/completion', json={'token': preview['token']}).status_code == 200
        assert client.get('/api/v3/cards').json() == []
        for original in originals:
            assert client.get('/api/v3/materials/' + original['id']).status_code == 404
        assert client.post('/api/v3/folders/demo/completion/preview').json()['publications_count'] == 0
