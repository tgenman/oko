"""Card pages and idempotent viewed commands at the reader HTTP interface."""
from fastapi.testclient import TestClient
from tests.support.reading_cost import library, traced_app
from tests.support.reader import HEADERS


def test_card_pages_support_cursor_etag_and_lost_cursor(tmp_path):
    feeds, _ = library(tmp_path, [5])
    with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
        first = client.get('/api/v3/cards', params={'feed': feeds[0], 'limit': 2})
        assert first.status_code == 200
        page = first.json()
        assert len(page['items']) == 2
        assert page['total'] == 5
        assert first.headers['etag']
        assert client.get('/api/v3/cards', params={'feed': feeds[0], 'limit': 2}, headers={'If-None-Match': first.headers['etag']}).status_code == 304
        cursor = page['next_cursor']
        next_page = client.get('/api/v3/cards', params={'feed': feeds[0], 'limit': 2, 'cursor': cursor}).json()
        assert next_page['items'][0]['id'] == cursor
        assert not set(c['id'] for c in page['items']) & set(c['id'] for c in next_page['items'])
        assert client.post('/api/v3/cards/read', json={'ids': [cursor]}).status_code == 204
        fresh = client.get('/api/v3/cards', params={'feed': feeds[0], 'limit': 2}, headers={'If-None-Match': first.headers['etag']})
        assert fresh.status_code == 200 and fresh.json()['total'] == 4
        assert fresh.headers['etag'] != first.headers['etag']
        lost = client.get('/api/v3/cards', params={'feed': feeds[0], 'limit': 2, 'cursor': cursor})
        assert lost.status_code == 409
        assert lost.json() == {'code': 'cursor_expired'}


def test_corrupt_source_url_becomes_placeholder_without_breaking_page(tmp_path):
    import time
    from tests.support.completion_library import library as seed
    seed(tmp_path, None, 3)
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        store = app.state.host.execution._store
        for _ in range(1000):
            if store is not None and not store.processing.recovering:
                break
            time.sleep(.01)
        first = client.get('/api/v3/cards')
        identity = f'{1:032x}'
        previous=next(card for card in first.json()['items'] if card['id']==identity)
        def damage():
            with store.db:
                store.db.execute("UPDATE publications SET url='javascript:alert(1)' WHERE id=%s", (identity,))
        client.portal.call(damage)
        response = client.get('/api/v3/cards', headers={'If-None-Match': first.headers['etag']})
        assert response.status_code == 200
        page=response.json()
        assert page['total']==3 and len(page['items'])==3
        broken=next(card for card in page['items'] if card['id']==identity)
        assert broken['title']=='Публикация недоступна'
        assert broken['source_url'] is None and broken['preview_url'] is None
        assert broken['published_at']==previous['published_at']
