"""Bound HTTP reading work independently of machine speed and background work."""
import re
import pytest
from fastapi.testclient import TestClient

from tests.support.storage import Store
from tests.support.postgres_work import measure_reads, scan_rows
from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app, tracing


@pytest.mark.parametrize('count', [26, 100])
def test_reading_http_budgets(tmp_path, count):
    feeds, items = library(tmp_path, [count] * 4)
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        response = client.get('/api/v3/reading-composition')
        assert response.status_code == 200
        assert response.json()['count'] == count * 4
        # Baseline for both library sizes: the service must reuse feed facts.
        assert int(response.headers['X-Test-SQL']) <= 11
        response = client.get('/api/v3/cards', params={'feed': feeds[0]})
        assert response.status_code == 200
        assert len(response.json()['items']) == min(count,40)
        assert response.json()['total'] == count
        assert {card['feed_id'] for card in response.json()['items']} == {feeds[0]}
        assert int(response.headers['X-Test-SQL']) < 50 + 11 * count
        response = client.get('/api/v3/cards')
        assert response.status_code == 200
        assert len(response.json()['items']) == 40
        assert response.json()['total'] == count * 4
        assert int(response.headers['X-Test-SQL']) < 80 + 11 * count * 4
        response = client.get('/api/v3/materials/' + items[0])
        assert response.status_code == 200
        assert response.json()['content']['description'] == 'Synthetic video'
        assert int(response.headers['X-Test-SQL']) <= 23
        jobs = [q for q in app.state.last_sql if re.search(r'\bFROM jobs\b', q, re.IGNORECASE)]
        # The compatible legacy response also reads history and fetch state.
        # The shared job snapshot must not be queried twice.
        assert len(jobs) <= 3 and len(jobs) == len(set(jobs)), jobs


def test_opening_one_material_does_not_read_unrelated_feeds(tmp_path):
    costs = []
    for count in (4, 20):
        root = tmp_path / str(count)
        _, items = library(root, [5] * count)
        app = traced_app(root)
        with TestClient(app, headers=HEADERS) as client:
            tracing(client, app)
            response = client.get('/api/v3/materials/' + items[0])
            assert response.status_code == 200
            costs.append(int(response.headers['X-Test-SQL']))
    assert costs[1] <= costs[0] + 2, costs


def test_selected_scope_sql_does_not_grow_with_other_materials(tmp_path):
    costs = []
    for other_count in (26, 100):
        root = tmp_path / str(other_count)
        feeds, _ = library(root, [26, other_count, other_count, other_count])
        app = traced_app(root)
        with TestClient(app, headers=HEADERS) as client:
            tracing(client, app)
            nav = client.get('/api/v3/reading-composition').json()
            folder = next(f['feed']['folder_id'] for f in nav['feeds'] if f['feed']['id'] == feeds[0])
            response = client.get('/api/v3/cards', params={'folder': folder})
            assert response.status_code == 200
            assert len(response.json()['items']) == 26
            costs.append(int(response.headers['X-Test-SQL']))
            assert client.patch('/api/v3/settings/feed/' + feeds[0], json={'originals': False}).status_code == 200
            response = client.get('/api/v3/cards', params={'feed': feeds[0]})
            assert all('originals' not in card for card in response.json()['items'])
            assert all(client.get('/api/v3/materials/'+card['id']).json()['originals'] is False for card in response.json()['items'])
    assert costs[1] <= costs[0] + 2, costs


def test_unviewed_index_bounds_archive_work_and_survives_reopen(tmp_path):
    store = Store(tmp_path, lambda _: None)
    store.subscriptions.ensure_demo()
    # Current archive fixture: only 100 records are still unviewed.
    with store.db:
        # Keep the membership statistics stale, as immediately after a bulk import.
        store.db._db.execute('ALTER TABLE feed_publications SET (autovacuum_enabled=false)')
        store.db.executemany("INSERT INTO items ( id , published , viewed , platform , external_id ) VALUES ( %s , %s , %s , 'other_web' , %s )",
            [(f'archive-{n:05}', n, int(n >= 100), f'archive-{n:05}') for n in range(20000)])
        store.db.executemany("INSERT INTO feed_publications(feed_id,publication_id) VALUES ('demo',%s)", [(f'archive-{n:05}',) for n in range(20000)])
        assert store.db.execute("SELECT 1 FROM pg_indexes WHERE schemaname=current_schema() AND indexname='items_unviewed_order'").fetchone()
        store.db._db.execute('ANALYZE items')
    store.db.close()
    for _ in range(2):
        store = Store(tmp_path, lambda _: None)
        with measure_reads(store.db) as work:
            rows = store.db.execute('SELECT id,feed_id FROM items_read WHERE viewed=0 ORDER BY published DESC').fetchall()
        assert len(rows) == 100
        assert rows[0]['id'] == 'archive-00099'
        assert work.rows < 2000, work
        with measure_reads(store.db) as feed_work:
            feed_rows = store.db.execute("SELECT id FROM unviewed_items_by_feed WHERE feed_id='demo' AND viewed=0 ORDER BY published DESC").fetchall()
        assert len(feed_rows) == 100
        assert feed_work.rows < 2000, feed_work
        # Include transactional entity versions and navigation counts, as well as
        # the index and processing markers; this fixed cost does not scan the archive.
        with store.db:
            store.db.begin()
            for viewed in (1, 0):
                plan = store.db._db.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) UPDATE items SET viewed=%s WHERE id=%s',
                    (viewed, 'archive-00000')).fetchone()[0][0]['Plan']
                assert scan_rows(plan) < 10, plan
                assert plan.get('Shared Hit Blocks', 0) + plan.get('Shared Read Blocks', 0) < 1600, plan
        store.db.close()

    # A small feed must also stay bounded when many other publications are active.
    store = Store(tmp_path, lambda _: None)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id) VALUES ('foreign'),('small')")
            store.db.execute("INSERT INTO items(id,viewed) SELECT 'foreign-' || n,0 FROM generate_series(1,1500) AS n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id) SELECT 'foreign',id FROM items WHERE id LIKE 'foreign-%'")
            store.db.execute("INSERT INTO items(id,viewed) SELECT 'small-' || n,0 FROM generate_series(1,10) AS n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id) SELECT 'small',id FROM items WHERE id LIKE 'small-%'")
            store.db._db.execute('ANALYZE items')
        with measure_reads(store.db) as small_work:
            small_rows = store.db.execute("SELECT id FROM unviewed_items_by_feed WHERE feed_id='small'").fetchall()
        assert len(small_rows) == 10
        assert small_work.rows < 200, small_work
    finally:
        store.db.close()
