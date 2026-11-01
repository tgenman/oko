from tests.support.cards import read_all_cards
from tests.support.presence import presence
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import HEADERS


def test_navigation_counts_loading_publications_and_empty_feeds(tmp_path):
    from tests.support.sources import LateSource
    from tests.support.reader import wait_for
    with TestClient(create_app(tmp_path, source=LateSource(), token='test'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        assert client.get('/api/v3/reading-composition').json()['count'] == 0
        presence(client, 'a')
        wait_for(client, lambda: len(read_all_cards(client, '/api/v3/cards')) == 3)
        assert all(card['loading'] for card in read_all_cards(client, '/api/v3/cards'))
        assert client.get('/api/v3/reading-composition').json()['count'] == 3
        assert client.get('/api/v3/reading-composition').json()['feeds'][0]['has_unviewed'] is True


def test_completion_metadata_includes_hidden_and_survives_restart(tmp_path):
    from tests.support.filters import FilterSource, transport, setup_filters
    from tests.support.reader import wait_for
    options = dict(source=FilterSource(), token='test', api_only=True, ai_transport=transport([]))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        setup_filters(client)
        cards = wait_for(client, lambda: (rows if len(rows := read_all_cards(client, '/api/v3/cards'))==2 and all(client.get('/api/v3/publication/'+r['id']).json()['filter']['state']=='ready' for r in rows) else None))
        for card in cards:
            assert client.post('/api/v3/materials/'+card['id']+'/viewed').status_code == 200
        nav = client.get('/api/v3/reading-composition').json()
        assert nav['feeds'][0]['count'] == 0
        assert nav['feeds'][0]['has_unviewed'] is True
        assert len(read_all_cards(client, '/api/v3/cards?show_hidden=true')) == 1
        preview = client.post('/api/v3/folders/demo/completion/preview').json()
        assert client.post('/api/v3/folders/demo/completion',json={'token':preview['token']}).status_code == 200
        assert client.get('/api/v3/reading-composition').json()['feeds'][0]['has_unviewed'] is False
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['id'] == 'demo'
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/reading-composition').json()['feeds'][0]['has_unviewed'] is False
