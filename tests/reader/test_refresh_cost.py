"""Manual refresh admits work without reprojecting a saved reading library."""
import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from oko.collection.contracts import Publication
from oko.storage.store import Store
from oko.subscriptions.subscriptions import AddFeed
from tests.support.collection_acceptance import discovered
from tests.support.reading_cost import request_sql, traced_app
from tests.support.reader import HEADERS
from tests.support.source import Image, SyntheticMaterial, publication_packet
from tests.support.youtube import PNG


def saved_library(root, dsn, count, feeds=4):
    store = Store(root, lambda _: None, database_url=dsn)
    try:
        for index in range(feeds):
            feed = store.subscriptions.add_feed(AddFeed(url=f'https://fixture.test/{index}',
                title=f'Synthetic {index}', folder='Fixture', verification='synthetic'))['id']
            store.jobs.enqueue_discovery(feed)
            job = store.jobs.next_job(store.clock(), feeds=[feed])
            assert job is not None
            discovered(store, [publication_packet(Publication(str(n), f'https://fixture.test/{index}/{n}', 1800000000),
                SyntheticMaterial('Saved title', 'Saved body', images=(Image('image.png', PNG),)))
                for n in range(count)], '{}', job, feed)
        with store.db:
            for row in store.db.execute('SELECT * FROM media_files').fetchall():
                key = 'media/'+row['publication_id']+'/'+row['id']
                path = root/key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(PNG)
                store.db.execute("UPDATE media_files SET status='ready',storage_key=? WHERE id=?", (key, row['id']))
            for row in store.db.execute('SELECT id,content FROM items').fetchall():
                content = json.loads(row['content'])
                media = [dict(m) for m in store.db.execute('SELECT * FROM media_files WHERE publication_id=?', (row['id'],))]
                store.media.project_header(content, media)
                store.db.execute('UPDATE items SET content=? WHERE id=?', (json.dumps(content), row['id']))
            store.db.execute("UPDATE jobs SET status='done'")
            store.db.execute("INSERT INTO jobs(kind,item,status) SELECT 'fetch',id,'done' FROM items")
        store.set_work_enabled(True)
    finally:
        store.db.close()


@pytest.mark.parametrize('count', [4, 32])
def test_refresh_ready_library_has_bounded_postgres_traffic(postgres_url, tmp_path, monkeypatch, count):
    saved_library(tmp_path, postgres_url, count)
    queries = []
    execute = psycopg.Connection.execute

    def observed(connection, query, *args, **kwargs):
        if request_sql.get() is not None and isinstance(query, str):
            queries.append(query)
        return execute(connection, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, 'execute', observed)
    app = traced_app(tmp_path, database_url=postgres_url)
    with TestClient(app, headers=HEADERS) as client:
        assert client.get('/api/v3/status').status_code == 200
        before = client.get('/api/v3/cards').json()
        assert len(before) == count * 4
        queries.clear()
        response = client.post('/api/v3/collect')
        assert response.status_code == 200
        assert len(queries) < 200, len(queries)
        assert client.get('/api/v3/cards').json() == before


def test_refresh_yields_between_feeds_with_postgres_latency(postgres_url, tmp_path, monkeypatch):
    import asyncio
    from contextvars import ContextVar
    import time

    saved_library(tmp_path, postgres_url, 4)
    refresh = ContextVar('refresh_request', default=False)
    execute = psycopg.Connection.execute

    def network_latency(connection, query, *args, **kwargs):
        if refresh.get():
            # A controlled 2 ms database round trip, not a substituted repository.
            time.sleep(.002)
        return execute(connection, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, 'execute', network_latency)
    app = traced_app(tmp_path, database_url=postgres_url)

    @app.middleware('http')
    async def responsiveness(request, call_next):
        if request.url.path != '/api/v3/collect':
            return await call_next(request)
        gaps = []
        running = True

        async def tick():
            previous = time.perf_counter()
            while running:
                await asyncio.sleep(.001)
                now = time.perf_counter()
                gaps.append(now - previous)
                previous = now

        token = refresh.set(True)
        task = asyncio.create_task(tick())
        await asyncio.sleep(0)
        try:
            response = await call_next(request)
            await asyncio.sleep(.002)
            response.headers['X-Test-Event-Loop-Gap'] = str(max(gaps, default=0))
            return response
        finally:
            refresh.reset(token)
            running = False
            await task

    with TestClient(app, headers=HEADERS) as client:
        assert client.get('/api/v3/status').status_code == 200
        response = client.post('/api/v3/collect')
        assert response.status_code == 200
        assert float(response.headers['X-Test-Event-Loop-Gap']) < .15
        assert client.get('/api/v3/status').json()['runtime_state'] == 'running'



def test_power_off_interrupts_refresh_without_waiting_for_the_library(postgres_url, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time

    saved_library(tmp_path, postgres_url, 1, feeds=20)
    entered = threading.Event()
    execute = psycopg.Connection.execute

    def network_latency(connection, query, *args, **kwargs):
        if request_sql.get() is not None:
            entered.set()
            time.sleep(.002)
        return execute(connection, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, 'execute', network_latency)
    app = traced_app(tmp_path, database_url=postgres_url)
    with TestClient(app, headers=HEADERS) as client:
        assert client.get('/api/v3/status').status_code == 200
        entered.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            refresh = pool.submit(client.post, '/api/v3/collect', headers={'X-Oko-Action': 'refresh-stop'})
            assert entered.wait(2)
            assert client.get('/api/v3/status').status_code == 200
            assert not refresh.done(), 'Reading waited for the entire manual refresh'
            assert client.get('/api/v3/settings/oko/global').status_code == 200
            assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
            response = refresh.result(timeout=5)
        assert response.status_code == 409
        assert response.json()['code'] == 'runtime_disabled'
        assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
        assert client.get('/api/v3/commands/refresh-stop').json()['outcome'] == 'failed'
