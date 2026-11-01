"""Feed completion at HTTP: exact scope, captured members and Telegram rules."""
from tests.support.cards import read_all_cards
from fastapi.testclient import TestClient
import httpx
from tests.support.app import create_app
from tests.support.reader import HEADERS, wait_for
from tests.support.presence import presence
from tests.support.filters import FilterSource, transport, setup_filters
from tests.support.sources import LateSource


def test_feed_completion_includes_hidden_and_preserves_subscription(tmp_path):
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=transport([])), headers=HEADERS) as client:
        setup_filters(client)
        wait_for(client, lambda: len(rows := read_all_cards(client, '/api/v3/cards?show_hidden=true')) == 3 and all(client.get('/api/v3/publication/'+r['id']).json()['filter']['state']=='ready' for r in rows))
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
        preview = client.post('/api/v3/feeds/demo/completion/preview').json()
        assert preview['publications_count'] == 3
        response = client.post('/api/v3/feeds/demo/completion', json={'token':preview['token']}, headers={'X-Oko-Action':'complete-demo'})
        assert response.status_code == 200, response.text
        assert read_all_cards(client, '/api/v3/cards?show_hidden=true') == []
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['id'] == 'demo'
        assert not client.get('/api/v3/reading-composition').json()['feeds'][0]['has_unviewed']
        receipt = client.get('/api/v3/commands/complete-demo').json()
        assert receipt['context'] == {'operation':'complete_feed','feed_id':'demo'}
        assert receipt['outcome'] == 'succeeded'
        assert client.post('/api/v3/feeds/demo/completion', json={'token':preview['token']}).status_code == 200


def test_feed_completion_covers_loading_and_rejects_late_fetch(tmp_path):
    with TestClient(create_app(tmp_path,source=LateSource(),token='test'),headers=HEADERS) as client:
        presence(client,'a')
        rows=wait_for(client,lambda: read_all_cards(client, '/api/v3/cards'))
        assert any(r['loading'] for r in rows)
        token=client.post('/api/v3/feeds/demo/completion/preview').json()['token']
        assert client.post('/api/v3/feeds/demo/completion',json={'token':token}).status_code == 200
        assert read_all_cards(client, '/api/v3/cards') == []
        assert not list((tmp_path/'content').glob('*/*'))
