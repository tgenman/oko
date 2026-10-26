"""Completion behavior and database work observed through the reader HTTP seam."""
import contextvars
import time
import json
import pytest

import psycopg
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import HEADERS
from tests.support.worker_library import worker_library


def library(root, database_url, count):
    store, original, _, _ = worker_library(root, database_url=database_url)
    store.set_work_enabled(False)
    store.processing.background = True
    with store.db:
        template = dict(store.db.execute('SELECT * FROM items WHERE id=?', (original,)).fetchone())
        fields = list(template)
        for number in range(1, count):
            identity = f'{number:032x}'
            row = dict(template, id=identity, source_id='completion:'+identity, external_id='completion:'+identity)
            store.db.execute('INSERT INTO items('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')', tuple(row[field] for field in fields))
            store.db.execute('INSERT INTO feed_publications VALUES (?,?)', ('demo', identity))
    store.db.close()


def test_completion_removes_captured_library_with_one_reachability_pass(postgres_url, tmp_path, monkeypatch):
    library(tmp_path, postgres_url, 10)
    recording = contextvars.ContextVar('completion_queries', default=None)
    queries = []
    execute = psycopg.Connection.execute

    def observed(connection, query, *args, **kwargs):
        captured = recording.get()
        if captured is not None and isinstance(query, str):
            captured.append(query)
        return execute(connection, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, 'execute', observed)
    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url)

    @app.middleware('http')
    async def capture(request, call_next):
        token = recording.set(queries)
        try:
            return await call_next(request)
        finally:
            recording.reset(token)

    with TestClient(app, headers=HEADERS) as client:
        for _ in range(1000):
            store = app.state.host.execution._store
            if store is not None and not store.processing.recovering:
                break
            time.sleep(.01)
        preview = client.post('/api/v3/folders/demo/completion/preview')
        assert preview.status_code == 200
        assert preview.json()['cards_count'] == preview.json()['publications_count'] == 10
        queries.clear()
        completed = client.post('/api/v3/folders/demo/completion', json={'token': preview.json()['token']})
        assert completed.status_code == 200, completed.text
        passes = sum('WITH RECURSIVE needed' in query for query in queries)
        completion_calls = len(queries)
        assert client.get('/api/v3/cards').json() == []
        assert client.post('/api/v3/folders/demo/completion', json={'token': preview.json()['token']}).status_code == 200
        assert passes == 1
        assert completion_calls <= 350


def test_preview_counts_cards_without_projecting_each_material(postgres_url, tmp_path, monkeypatch):
    library(tmp_path, postgres_url, 10)
    queries = []
    execute = psycopg.Connection.execute

    def observed(connection, query, *args, **kwargs):
        if isinstance(query, str):
            queries.append(query)
        return execute(connection, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, 'execute', observed)
    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url)
    with TestClient(app, headers=HEADERS) as client:
        for _ in range(1000):
            store = app.state.host.execution._store
            if store is not None and not store.processing.recovering:
                break
            time.sleep(.01)
        queries.clear()
        preview = client.post('/api/v3/folders/demo/completion/preview')
        captured = list(queries)
        assert preview.status_code == 200
        assert preview.json()['cards_count'] == preview.json()['publications_count'] == 10
        assert len(captured) <= 40


@pytest.mark.parametrize('corruption', ['content', 'attachment', 'media_ids', 'processing_metadata'])
def test_completion_preserves_unreadable_and_pending_members(tmp_path, corruption):
    library(tmp_path, None, 3)
    app = create_app(tmp_path, token='test', api_only=True)
    with TestClient(app, headers=HEADERS) as client:
        for _ in range(1000):
            store = app.state.host.execution._store
            if store is not None and not store.processing.recovering:
                break
            time.sleep(.01)
        assert len(client.get('/api/v3/cards').json()) == 3
        # Model an unreadable saved copy and a discovered, not-yet-loaded
        # publication; fixture writes precede all observations under test.
        def damage_fixture():
            with store.db:
                identity = f'{1:032x}'
                if corruption == 'attachment':
                    content = json.loads(store.db.execute('SELECT content FROM items WHERE id=?', (identity,)).fetchone()[0])
                    content['extra']['_reader']['attachments'] = [{'name':'missing-mime', 'kind':'image'}]
                    store.db.execute('UPDATE items SET content=? WHERE id=?', (json.dumps(content), identity))
                elif corruption in ('media_ids', 'processing_metadata'):
                    store.db.execute("UPDATE items SET " + corruption + "='invalid-json' WHERE id=?", (identity,))
                else:
                    store.db.execute("UPDATE items SET content='{}' WHERE id=?", (identity,))
                store.db.execute('UPDATE items SET complete=0 WHERE id=?', (f'{2:032x}',))
        client.portal.call(damage_fixture)
        assert len(client.get('/api/v3/cards').json()) == 2
        preview = client.post('/api/v3/feeds/demo/completion/preview').json()
        assert preview['cards_count'] == 2
        assert preview['publications_count'] == 3
        assert client.post('/api/v3/feeds/demo/completion', json={'token': preview['token']}).status_code == 200
        assert client.get('/api/v3/cards').json() == []
        assert client.post('/api/v3/feeds/demo/completion/preview').json()['publications_count'] == 0
