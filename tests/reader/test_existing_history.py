from tests.support.reader import wait_for
from tests.support.telegram import connected_feed
from tests.support.presence import presence, close_presence
from tests.support.telegram import connect_request
from tests.support.subscriptions import configured_sources
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
        assert configured.status_code == 200, configured.text
        current=client.get('/api/v3/subscriptions').json()
        assert current['feeds'][0]['id']==first['id']
        assert current['feeds'][0]['auto_read'] is True


def test_readding_rss_preserves_history_and_settings(tmp_path):
    with client_for(tmp_path) as client:
        checked = verify(client)
        first = add(client, checked, initial_history={'mode': 'count', 'count': 2}).json()
        client.patch('/api/v3/feeds/' + first['id'], json={'importance': 'high'})
        before = client.get('/api/v3/subscriptions').json()
        response = add(client, checked, title='Иное', folder='Новая', initial_history={'mode': 'count', 'count': 100})
        assert response.status_code == 200
        assert client.get('/api/v3/subscriptions').json() == before
