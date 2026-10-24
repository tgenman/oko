"""Scoped batch reading stays compatible with the publication pipeline."""
from fastapi.testclient import TestClient
from tests.support.reading_cost import library, traced_app, tracing
from tests.support.reader import HEADERS


def test_scoped_cards_use_prefetched_inputs(tmp_path):
    feeds, _ = library(tmp_path, [10, 10])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        response = client.post('/api/v3/reading/check', json=dict(storage_id=None, resources=[dict(
            kind='list', known_version=None, known_cards={},
            options=dict(target=dict(scope='feed', feed_id=feeds[0]), sort='newest', show_hidden=False))]))
        assert response.status_code == 200, response.text
        resources = response.json()['resources']
        cards = [r for r in resources if r['kind'] == 'card']
        assert len(cards) == 10
        assert all(r['state'] == 'changed' and r['data']['feed_id'] == feeds[0] for r in cards)
        assert int(response.headers['X-Test-SQL']) <= 30, app.state.last_sql
