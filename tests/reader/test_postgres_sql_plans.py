from tests.support.storage import analyze
"""Plans of real reader HTTP queries on an isolated synthetic PostgreSQL archive."""
from contextvars import ContextVar

import re
import psycopg
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import HEADERS
from tests.support.worker_library import worker_library


def nodes(plan):
    yield plan
    for child in plan.get('Plans', []):
        yield from nodes(child)


def test_opened_material_does_not_scan_foreign_job_archive(postgres_url, tmp_path, monkeypatch):
    store, identity, _, _ = worker_library(tmp_path, database_url=postgres_url)
    store.set_work_enabled(False)
    store.db.close()
    with psycopg.connect(postgres_url, autocommit=True) as seed:
        # No runtime work: archived unrelated rows are only planner ballast.
        seed.execute('SET session_replication_role=replica')
        seed.execute("INSERT INTO items(id,platform,external_id,viewed) SELECT 'archive-'||n,'rss','archive-'||n,1 FROM generate_series(1,20000) n")
        seed.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'media-'||n,'archive-'||n,'r','https://example.invalid','a','ready' FROM generate_series(1,10000) n")
        seed.execute("INSERT INTO jobs(kind,item,status) SELECT 'fetch','archive-'||n,'done' FROM generate_series(1,20000) n")
        seed.execute("INSERT INTO jobs(kind,item,status) SELECT 'download_media','media-'||n,'done' FROM generate_series(1,10000) n")
        seed.execute('SET session_replication_role=origin')
        analyze(seed)
    capture = ContextVar('material_plan_capture', default=None)
    execute = psycopg.Connection.execute

    def observed(db, query, params=None, **kwargs):
        result = execute(db, query, params, **kwargs)
        records = capture.get()
        if records is not None and isinstance(query, str) and re.search(r'\b(?:FROM|JOIN)\s+(?:jobs|media_files)\b', query, re.IGNORECASE):
            records.append((query, params))
        return result

    monkeypatch.setattr(psycopg.Connection, 'execute', observed)
    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url)
    statements = []

    @app.middleware('http')
    async def measure(request, call_next):
        token = capture.set(statements)
        try:
            return await call_next(request)
        finally:
            capture.reset(token)

    with TestClient(app, headers=HEADERS) as client:
        response = client.get('/api/v3/publication/' + identity)
        assert response.status_code == 200, response.text
        material = response.json()
        assert material['text'] == 'Useful text'
    assert statements, 'No material jobs query was observed at the HTTP boundary'
    with psycopg.connect(postgres_url, autocommit=True) as reader:
        for query, params in statements:
            plan = reader.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + query, params).fetchone()[0][0]['Plan']
            assert not [node for node in nodes(plan) if node['Node Type'] == 'Seq Scan' and node.get('Relation Name') in ('jobs', 'media_files')], plan
