"""HTTP cost budgets for cold lists and media, independent of machine speed."""
from fastapi.testclient import TestClient
from tests.support.reading_cost import library, traced_app, tracing
from tests.support.reader import HEADERS


def test_cold_list_batches_card_inputs(tmp_path):
    library(tmp_path, [10] * 4)
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        response = client.post('/api/v3/reading/check', json=dict(storage_id=None, resources=[dict(
            kind='list', options=dict(target=dict(scope='all'), sort='newest', show_hidden=False),
            known_version=None, known_cards={})]))
        assert response.status_code == 200, response.text
        cards = [r for r in response.json()['resources'] if r['kind'] == 'card']
        assert len(cards) == 40
        assert all(r['state'] == 'changed' and r['data']['availability'] == 'available' for r in cards)
        assert int(response.headers['X-Test-SQL']) <= 20, app.state.last_sql


def test_current_image_uses_only_saved_content(tmp_path):
    _, items = library(tmp_path, [1])
    (tmp_path / 'content' / items[0] / '1' / 'image-test.png').write_bytes(b'synthetic-image')
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        response = client.get(f'/api/v3/materials/{items[0]}/images/image-test.png')
        assert response.status_code == 200, response.text
        assert response.content == b'synthetic-image'
        assert int(response.headers['X-Test-SQL']) <= 4, app.state.last_sql


def test_settings_reconciliation_batches_reads_and_preserves_other_feed(tmp_path):
    feeds, items = library(tmp_path, [20] * 4)
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        response = client.patch('/api/v3/settings/oko/global', json={'theses': False})
        assert response.status_code == 200, response.text
        reads = [q for q in app.state.last_sql if q.lstrip().upper().startswith(('SELECT', 'WITH'))]
        assert len(reads) < 100, reads
        other = client.get('/api/v3/reading/materials/' + items[1]).json()
        response = client.patch('/api/v3/settings/feed/' + feeds[0], json={'prompt_filters': 'Synthetic filter'})
        assert response.status_code == 200, response.text
        unchanged = client.get('/api/v3/reading/materials/' + items[1]).json()
        assert unchanged == other


def test_list_batch_boundary_and_scope_parity(tmp_path):
    feeds, _ = library(tmp_path, [401, 2])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        for sort in ('newest', 'oldest', 'importance'):
            legacy = client.get('/api/v3/cards', params={'feed': feeds[0], 'sort': sort}).json()
            reply = client.post('/api/v3/reading/check', json=dict(storage_id=None, resources=[dict(
                kind='list', options=dict(target=dict(scope='feed', feed_id=feeds[0]), sort=sort, show_hidden=False),
                known_version=None, known_cards={})]))
            assert reply.status_code == 200, reply.text
            rows = reply.json()['resources']
            assert next(r['data']['ids'] for r in rows if r['kind'] == 'list') == [c['id'] for c in legacy]
            assert all(r['state'] == 'changed' for r in rows)
            assert int(reply.headers['X-Test-SQL']) <= 25


def test_media_disappears_after_viewing(tmp_path):
    _, items = library(tmp_path, [1])
    (tmp_path / 'content' / items[0] / '1' / 'image-test.png').write_bytes(b'image')
    with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
        url = f'/api/v3/materials/{items[0]}/images/image-test.png'
        assert client.get(url).status_code == 200
        assert client.get(f'/api/v3/materials/{items[0]}/media/original.md').status_code == 404
        assert client.post(f'/api/v3/materials/{items[0]}/viewed').status_code == 200
        assert client.get(url).status_code == 404


def test_batched_feeds_keep_individual_failure_boundary(tmp_path):
    feeds, _ = library(tmp_path, [1, 1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        def corrupt():
            db = app.state.runtime.require_store().db
            with db:
                db.execute("UPDATE feeds SET importance='legacy' WHERE id=?", (feeds[0],))
        client.portal.call(corrupt)
        response = client.post('/api/v3/reading/check', json=dict(storage_id=None, resources=[
            dict(kind='feed', id=identity, known_version=None) for identity in feeds]))
        assert response.status_code == 200, response.text
        states = {r['id']: r['state'] for r in response.json()['resources']}
        assert states == {feeds[0]: 'failed', feeds[1]: 'changed'}
