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


def test_read_cards_is_idempotent_and_does_not_read_unselected_cards(tmp_path):
    feeds, _ = library(tmp_path, [4])
    with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
        page = client.get('/api/v3/cards', params={'feed': feeds[0]}).json()
        ids = [card['id'] for card in (page['items'] if isinstance(page, dict) else page)]
        assert client.post('/api/v3/cards/read', json={'ids': [ids[0], ids[1], 'missing']}).status_code == 204
        assert client.post('/api/v3/cards/read', json={'ids': [ids[0], ids[1], 'missing']}).status_code == 204
        remaining = client.get('/api/v3/cards', params={'feed': feeds[0]}).json()
        assert [card['id'] for card in (remaining['items'] if isinstance(remaining, dict) else remaining)] == ids[2:]


def test_viewing_cards_does_not_rebuild_obsolete_list_versions(tmp_path):
    from tests.support.reading_cost import tracing
    feeds, _ = library(tmp_path, [4])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        ids = [card['id'] for card in client.get('/api/v3/cards', params={'feed': feeds[0]}).json()['items']]
        tracing(client, app)
        assert client.post('/api/v3/cards/read', json={'ids': ids[:2]}).status_code == 204
        assert not any('reader_lists' in query.lower() for query in app.state.last_sql)
        assert client.get('/api/v3/cards', params={'feed': feeds[0]}).json()['total'] == 2
