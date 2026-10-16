from tests.support.presence import presence, close_presence
from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.demo import DemoSource
from tests.support.processes import wait_cards
from tests.support.reader import HEADERS


def test_navigation_counts_and_feed_selection(tmp_path):
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test'), headers=HEADERS) as client:
        assert client.get('/api/v3/reading-composition', headers={'Authorization': 'Bearer wrong'}).status_code == 403
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        cards = wait_cards(client, 3)
        navigation = client.get('/api/v3/reading-composition').json()
        assert navigation['count'] == 3
        assert next(f['count'] for f in navigation['folders'] if f['id'] == 'demo') == 3
        assert next(f['count'] for f in navigation['feeds'] if f['feed']['id'] == 'demo') == 3
        assert client.get('/api/v3/cards?feed=demo').json() == cards
        assert client.get('/api/v3/cards?feed=missing').json() == []
        assert client.get('/api/v3/cards?folder=missing&feed=demo').json() == []
        client.post('/api/v3/materials/' + cards[0]['id'] + '/viewed')
        assert client.get('/api/v3/reading-composition').json()['count'] == 2


def test_navigation_counts_loading_publications_and_empty_feeds(tmp_path):
    from tests.support.sources import LateSource
    from tests.support.reader import wait_for
    with TestClient(create_app(tmp_path, source=LateSource(), token='test'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        assert client.get('/api/v3/reading-composition').json()['count'] == 0
        presence(client, 'a')
        wait_for(client, lambda: len(client.get('/api/v3/cards').json()) == 3)
        assert all(not (card['availability'] == 'available') for card in client.get('/api/v3/cards').json())
        assert client.get('/api/v3/reading-composition').json()['count'] == 3
