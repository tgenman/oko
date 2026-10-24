"""Bound HTTP reading work independently of machine speed and background work."""
import re
import pytest
from fastapi.testclient import TestClient

from oko.storage.store import Store
from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app, tracing, request_sql


@pytest.mark.parametrize('count', [26, 100])
def test_reading_http_budgets(tmp_path, count):
    feeds, items = library(tmp_path, [count] * 4)
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        response = client.get('/api/v3/reading-composition')
        assert response.status_code == 200
        assert response.json()['count'] == count * 4
        assert int(response.headers['X-Test-SQL']) < 80 + 2 * count * 4
        response = client.get('/api/v3/cards', params={'feed': feeds[0]})
        assert response.status_code == 200
        assert len(response.json()) == count
        assert {card['feed_id'] for card in response.json()} == {feeds[0]}
        assert int(response.headers['X-Test-SQL']) < 50 + 11 * count
        response = client.get('/api/v3/cards')
        assert response.status_code == 200
        assert len(response.json()) == count * 4
        assert int(response.headers['X-Test-SQL']) < 80 + 11 * count * 4
        response = client.get('/api/v3/materials/' + items[0])
        assert response.status_code == 200
        assert response.json()['content']['description'] == 'Synthetic video'
        assert int(response.headers['X-Test-SQL']) < 35
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


def test_selected_scope_and_file_reads_do_not_grow_with_other_materials(tmp_path, monkeypatch):
    from collections import Counter
    from pathlib import Path

    costs = []
    original_read = Path.read_bytes
    reads = Counter()

    def read(path):
        if path.name == 'original.md' and request_sql.get() is not None:
            reads[str(path)] += 1
        return original_read(path)

    monkeypatch.setattr(Path, 'read_bytes', read)
    for other_count in (26, 100):
        root = tmp_path / str(other_count)
        feeds, _ = library(root, [26, other_count, other_count, other_count])
        app = traced_app(root)
        with TestClient(app, headers=HEADERS) as client:
            tracing(client, app)
            nav = client.get('/api/v3/reading-composition').json()
            folder = next(f['feed']['folder_id'] for f in nav['feeds'] if f['feed']['id'] == feeds[0])
            reads.clear()
            response = client.get('/api/v3/cards', params={'folder': folder})
            assert response.status_code == 200
            assert len(response.json()) == 26
            assert not reads  # Publication text comes from SQLite, not document files.
            costs.append(int(response.headers['X-Test-SQL']))
            assert client.patch('/api/v3/settings/feed/' + feeds[0], json={'originals': False}).status_code == 200
            response = client.get('/api/v3/cards', params={'feed': feeds[0]})
            assert all(card['originals'] is False for card in response.json())
    assert costs[1] <= costs[0] + 2, costs


def test_unviewed_index_bounds_archive_work_and_survives_reopen(tmp_path):
    store = Store(tmp_path, lambda _: None)
    # Current archive fixture: only 100 records are still unviewed.
    with store.db:
        store.db.executemany("INSERT INTO items(id,published,viewed,feed_id,platform,external_id) VALUES (?,?,?,?,'other_web',?)",
            [(f'archive-{n:05}', n, int(n >= 100), 'demo',f'archive-{n:05}') for n in range(20000)])
        store.db.execute('DROP INDEX items_unviewed_order')
    store.db.close()
    for _ in range(2):
        store = Store(tmp_path, lambda _: None)
        steps = []
        store.db.set_progress_handler(lambda: steps.append(1) or 0, 1)
        rows = store.db.execute('SELECT id,feed_id FROM items WHERE viewed=0 ORDER BY published DESC').fetchall()
        store.db.set_progress_handler(None, 0)
        assert len(rows) == 100
        assert rows[0]['id'] == 'archive-00099'
        assert len(steps) < 2000, len(steps)
        # Include transactional entity versions and navigation counts, as well as
        # the index and processing markers; this fixed cost does not scan the archive.
        writes = []
        store.db.set_progress_handler(lambda: writes.append(1) or 0, 1)
        with store.db:
            store.db.execute('UPDATE items SET viewed=1 WHERE id=?', ('archive-00000',))
            store.db.execute('UPDATE items SET viewed=0 WHERE id=?', ('archive-00000',))
        store.db.set_progress_handler(None, 0)
        assert len(writes) < 1600, len(writes)
        store.db.close()
