"""Scoped batch reading stays compatible with the publication pipeline."""
from fastapi.testclient import TestClient
from tests.support.reading_cost import library, traced_app, tracing
from tests.support.reader import HEADERS


def test_scoped_cards_use_prefetched_inputs(tmp_path):
    feeds, _ = library(tmp_path, [10, 10])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        response = client.get('/api/v3/cards',params={'feed':feeds[0]})
        assert response.status_code == 200, response.text
        cards = response.json()['items']
        assert len(cards) == 10
        assert all(card['feed_id'] == feeds[0] for card in cards)
        assert int(response.headers['X-Test-SQL']) <= 30, app.state.last_sql
