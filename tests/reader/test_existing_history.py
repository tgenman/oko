from tests.support.reader import wait_for
from tests.support.presence import presence
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.feed_url import HEADERS


def test_readding_telegram_opens_existing_without_changing_history_or_settings(tmp_path):
    import httpx
    from tests.support.telegram_web import page
    transport=httpx.MockTransport(lambda request:httpx.Response(200,text=page([(11,'Public post')])))
    with TestClient(create_app(tmp_path, token='test', rss_transport=transport), headers=HEADERS) as client:
        presence(client, 'reader')
        body = dict(source_locator='@fixture', folder='Старое', initial_history=dict(mode='count', count='2'))
        feed_id = client.post('/api/v3/telegram/connect',json=body).json()['feed_id']
        first = next(feed for feed in client.get('/api/v3/subscriptions').json()['feeds'] if feed['id']==feed_id)
        client.patch('/api/v3/feeds/' + first['id'], json={'importance': 'high'})
        # Compare the complete catalogue only after legitimate history progress settles.
        wait_for(client,lambda: client.get('/api/v3/settings/feed/'+first['id']).json()['feed']['history']['state']=='complete')
        before = client.get('/api/v3/subscriptions').json()
        response = client.post('/api/v3/telegram/connect', json={**body, 'folder': 'Новое',
                    'initial_history': dict(mode='count', count='100')})
        assert response.status_code == 409, response.text
        assert client.get('/api/v3/subscriptions').json() == before
