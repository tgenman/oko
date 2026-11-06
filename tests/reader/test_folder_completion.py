from tests.support.cards import read_all_cards
from tests.support.presence import presence
from pathlib import Path
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.synthetic_source import SyntheticSource
from tests.support.processes import wait_cards

HEADERS = {'Authorization': 'Bearer test-token'}


def test_folder_confirmation_deletes_all_originals_and_is_repeatable(tmp_path: Path):
    with TestClient(create_app(tmp_path, source=SyntheticSource(), token='test-token'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'a')
        originals = wait_cards(client, 3)
        response = client.post('/api/v3/folders/fixture/completion/preview')
        assert response.status_code == 200
        preview = response.json()
        assert preview['cards_count'] == 3 and preview['publications_count'] == 3
        assert len(read_all_cards(client, '/api/v3/cards')) == 3
        for _ in range(2):
            response = client.post('/api/v3/folders/fixture/completion', json={'token': preview['token']})
            assert response.status_code == 200
        assert read_all_cards(client, '/api/v3/cards') == []
        for item in originals:
            assert client.get('/api/v3/publication/' + item['id']).status_code == 404
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
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
        accepted = changed.json()['details']['preview']['token']
        assert client.post(f'/api/v3/folders/{folder}/completion', json={'token': accepted}).status_code == 200
        current[0] = rss(guid='three', url='https://source.test/three')
        client.post('/api/v3/collect')
        remaining = rss_cards(client, 1)
        assert client.post(f'/api/v3/folders/{folder}/completion', json={'token': accepted}).status_code == 200
        assert read_all_cards(client, '/api/v3/cards')[0]['id'] == remaining[0]['id']


def test_other_folder_and_wrong_folder_confirmation_are_untouched(tmp_path):
    import httpx
    from tests.support.rss import rss, import_feed, wait_cards as rss_cards
    def response(request):
        return httpx.Response(200, text=rss() if request.url.path == '/feed' else '<article>Другой материал</article>')
    with TestClient(create_app(tmp_path, source=SyntheticSource(), token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        import_feed(client)
        other = next(f['folder_id'] for f in client.get('/api/v3/subscriptions').json()['feeds'] if f['id'] != 'fixture')
        presence(client, 'a')
        rss_cards(client, 4)
        preview = client.post('/api/v3/folders/fixture/completion/preview').json()
        assert preview['cards_count'] == 3
        assert client.post(f'/api/v3/folders/{other}/completion', json={'token': preview['token']}).status_code == 409
        assert len(read_all_cards(client, '/api/v3/cards')) == 4
        assert client.post('/api/v3/folders/fixture/completion', json={'token': preview['token']}).status_code == 200
        assert len(read_all_cards(client, '/api/v3/cards')) == 1
        assert len(read_all_cards(client, '/api/v3/cards?folder=' + other)) == 1


def test_delete_failure_is_reported_and_recovered_without_blocking_collection(tmp_path):
    from tests.support.reader import Clock, wait_for
    clock=Clock()
    fail = [True]
    def fault(point):
        if point == 'before_delete' and fail[0]:
            raise OSError('disk error')
    options = dict(source=SyntheticSource(), token='test-token', fault=fault,clock=clock,wall_clock=clock)
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'a')
        old_ids=[card['id'] for card in wait_cards(client, 3)]
        preview = client.post('/api/v3/folders/fixture/completion/preview').json()
        assert client.post('/api/v3/folders/fixture/completion', json={'token': preview['token']}).status_code == 503
        assert read_all_cards(client, '/api/v3/cards') == []
        assert any(p['code']=='feed_delete_failed' for p in client.get('/api/v3/status/details').json()['problems']['current'])
    class Counting(SyntheticSource):
        calls = 0
        async def discover(self, cursor):
            self.calls += 1
            return await super().discover(cursor)
    source = Counting()
    with TestClient(create_app(tmp_path, source=source, token='test-token', fault=fault,clock=clock,wall_clock=clock), headers=HEADERS) as client:
        presence(client, 'b')
        assert not any(card['id'] in old_ids for card in read_all_cards(client, '/api/v3/cards'))
        wait_for(client,lambda:source.calls >= 1)
        fail[0] = False
        clock.now += 61
        wait_for(client, lambda: not any((tmp_path/'content'/identity).exists() for identity in old_ids))
        assert not any((tmp_path/'content'/identity).exists() for identity in old_ids)
