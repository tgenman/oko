from tests.support.reader import wait_for
from tests.support.telegram import connected_feed
from tests.support.presence import presence, close_presence
from tests.support.telegram import connect_request
from tests.support.subscriptions import saved_sources
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.telegram import Telegram
from tests.support.feed_url import HEADERS, client_for, verify, add


def test_readding_telegram_opens_existing_without_changing_history_or_settings(tmp_path):
    with TestClient(create_app(tmp_path, token='test', telegram_client=Telegram()), headers=HEADERS) as client:
        presence(client, 'reader')
        body = dict(account='fixture', peer='@fixture', folder='Старое', auto_read=False,
                    initial_history=dict(mode='count', count=2))
        first = connected_feed(client, connect_request(client, json=body))
        client.patch('/api/v3/feeds/' + first['id'], json={'importance': 'high'})
        # Compare the complete catalogue only after legitimate history progress settles.
        wait_for(client,lambda: client.get('/api/v3/settings/feed/'+first['id']).json()['feed']['history']['state']=='complete')
        before = client.get('/api/v3/subscriptions').json()
        response = connect_request(client, json={**body, 'folder': 'Новое', 'auto_read': True,
                    'initial_history': dict(mode='count', count=100)})
        assert response.status_code == 409, response.text
        assert client.get('/api/v3/subscriptions').json() == before
        configured = connect_request(client, json={**body, 'feed_id': first['id'], 'auto_read': True})
        assert configured.status_code == 422
        assert configured.json()['code'] == 'request_invalid'
        assert client.get('/api/v3/subscriptions').json() == before


def test_readding_rss_preserves_history_and_settings(tmp_path):
    with client_for(tmp_path) as client:
        checked = verify(client)
        first = add(client, checked, initial_history={'mode': 'count', 'count': 2}).json()
        client.patch('/api/v3/feeds/' + first['id'], json={'importance': 'high'})
        before = client.get('/api/v3/subscriptions').json()
        response = add(client, checked, title='Иное', folder='Новая', initial_history={'mode': 'count', 'count': 100})
        assert response.status_code == 200
        assert client.get('/api/v3/subscriptions').json() == before


def test_legacy_opml_history_remains_unconfigured_and_settings_survive_restart(tmp_path):
    saved_sources(tmp_path, [('https://example.org/feed.xml', 'Лента', 'Старые')])
    with client_for(tmp_path) as client:
        response = client.get('/api/v3/subscriptions')
        assert response.status_code == 200
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]
        assert feed['history']['state'] == 'absent'
        assert feed['initial_days'] == 1
        client.patch('/api/v3/feeds/' + feed['id'], json={'importance': 'high', 'initial_days': 7})
        before = client.get('/api/v3/subscriptions').json()
        assert client.get('/api/v3/subscriptions').status_code == 200
        assert client.get('/api/v3/subscriptions').json() == before
    with client_for(tmp_path) as client:
        assert client.get('/api/v3/subscriptions').json() == before
        repeated = verify(client)
        assert repeated['state'] == 'existing'
        assert repeated['existing_feed']['id'] == feed['id']
        assert client.get('/api/v3/subscriptions').json() == before


def test_legacy_cursor_does_not_replay_older_posts_on_restart(tmp_path):
    saved_sources(tmp_path, [('https://legacy.test/feed', 'Legacy', 'Без папки')])
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
        client.get('/api/v3/subscriptions')
        presence(client, 'reader')
        for _ in range(100):
            cards = client.get('/api/v3/cards').json()
            if cards: break
            time.sleep(.02)
        assert {c['source_url'] for c in cards} == {'https://legacy.test/recent'}
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]
        assert feed['history']['state'] == 'absent'
        frozen_since = client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']['legacy_since']
        from datetime import datetime
        assert now - 86500 < datetime.fromisoformat(frozen_since.replace('Z','+00:00')).timestamp() < now - 86300
        client.patch('/api/v3/feeds/' + feed['id'], json={'initial_days': 7})
        assert client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']['legacy_since'] == frozen_since
    with open_reader() as client:
        presence(client, 'reader')
        client.post('/api/v3/collect')
        time.sleep(.15)
        assert {c['source_url'] for c in client.get('/api/v3/cards').json()} == {'https://legacy.test/recent'}
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['history']['state'] == 'absent'
