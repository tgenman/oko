"""The compatibility status field remains present for read publications."""
from fastapi.testclient import TestClient

from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app, wait_for_recovery


def test_status_viewed_ids_stays_empty_after_marking_read(tmp_path):
    _, items = library(tmp_path, [1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        for path in ('/api/v3/status', '/api/v3/status/details'):
            response = client.get(path)
            assert response.status_code == 200, response.text
            assert response.json()['viewed_ids'] == []
        assert client.post('/api/v3/cards/read', json={'ids':[items[0]]}).status_code == 204
        assert client.get('/api/v3/publication/'+items[0]).status_code == 200
        for path in ('/api/v3/status', '/api/v3/status/details'):
            response = client.get(path)
            assert response.status_code == 200, response.text
            assert response.json()['viewed_ids'] == []
