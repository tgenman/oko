from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.telegram import Telegram
from tests.support.feed_url import HEADERS, client_for, verify, add


def test_readding_telegram_opens_existing_without_changing_history_or_settings(tmp_path):
    with TestClient(create_app(tmp_path, token='test', telegram_client=Telegram()), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        body = dict(account='fixture', peer='@fixture', folder='Старое', auto_read=False,
                    initial_history=dict(mode='count', count=2))
        first = client.post('/api/telegram/connect', json=body).json()
        client.patch('/api/feeds/' + first['id'], json={'importance': 'high'})
        before = client.get('/api/subscriptions').json()
        response = client.post('/api/telegram/connect', json={**body, 'folder': 'Новое', 'auto_read': True,
                    'initial_history': dict(mode='count', count=100)})
        assert response.status_code == 200, response.text
        assert response.json()['id'] == first['id']
        assert client.get('/api/subscriptions').json() == before
        configured = client.post('/api/telegram/connect', json={**body, 'feed_id': first['id'], 'auto_read': True})
        assert configured.status_code == 200
        assert configured.json()['auto_read'] is True


def test_readding_rss_preserves_history_and_settings(tmp_path):
    with client_for(tmp_path) as client:
        checked = verify(client)
        first = add(client, checked, initial_history={'mode': 'count', 'count': 2}).json()
        client.patch('/api/feeds/' + first['id'], json={'importance': 'high'})
        before = client.get('/api/subscriptions').json()
        response = add(client, checked, title='Иное', folder='Новая', initial_history={'mode': 'count', 'count': 100})
        assert response.status_code == 200
        assert client.get('/api/subscriptions').json() == before


def test_legacy_opml_history_remains_unconfigured_and_settings_survive_restart(tmp_path):
    opml = '<opml><body><outline text="Старые"><outline text="Лента" xmlUrl="https://example.org/feed.xml"/></outline></body></opml>'
    with client_for(tmp_path) as client:
        response = client.post('/api/import', json={'opml': opml})
        assert response.status_code == 200
        feed = client.get('/api/subscriptions').json()['feeds'][0]
        assert feed['initial_history'] is None
        assert feed['initial_days'] == 1
        client.patch('/api/feeds/' + feed['id'], json={'importance': 'high', 'initial_days': 7})
        before = client.get('/api/subscriptions').json()
        assert client.post('/api/import', json={'opml': opml}).status_code == 200
        assert client.get('/api/subscriptions').json() == before
    with client_for(tmp_path) as client:
        assert client.get('/api/subscriptions').json() == before
        repeated = add(client, verify(client), folder='Другая', initial_history={'mode': 'count', 'count': 99})
        assert repeated.status_code == 200
        assert client.get('/api/subscriptions').json() == before


def test_legacy_cursor_does_not_replay_older_posts_on_restart(tmp_path):
    import time
    from email.utils import formatdate
    import httpx
    now = time.time()
    xml = '<rss><channel><title>Legacy</title>' + ''.join(
        f'<item><guid>{name}</guid><link>https://legacy.test/{name}</link><title>{name}</title><pubDate>{formatdate(stamp, usegmt=True)}</pubDate></item>'
        for name, stamp in [('recent', now - 30), ('old', now - 3 * 86400)]) + '</channel></rss>'
    def reply(request):
        return httpx.Response(200, text=xml if request.url.path == '/feed' else '<article>Legacy article</article>')
    def open_reader():
        return TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS)
    with open_reader() as client:
        client.post('/api/import', json={'opml': '<opml><body><outline text="Legacy" xmlUrl="https://legacy.test/feed"/></body></opml>'})
        client.post('/api/tabs/reader/heartbeat')
        for _ in range(100):
            cards = client.get('/api/cards').json()
            if cards: break
            time.sleep(.02)
        assert {c['url'] for c in cards} == {'https://legacy.test/recent'}
        feed = client.get('/api/subscriptions').json()['feeds'][0]
        assert feed['initial_history'] is None
        frozen_since = client.get('/api/settings/feed/' + feed['id']).json()['feed']['legacy_since']
        assert now - 86500 < frozen_since < now - 86300
        client.patch('/api/feeds/' + feed['id'], json={'initial_days': 7})
        assert client.get('/api/settings/feed/' + feed['id']).json()['feed']['legacy_since'] == frozen_since
    with open_reader() as client:
        client.post('/api/tabs/reader/heartbeat')
        client.post('/api/collect')
        time.sleep(.15)
        assert {c['url'] for c in client.get('/api/cards').json()} == {'https://legacy.test/recent'}
        assert client.get('/api/subscriptions').json()['feeds'][0]['initial_history'] is None
