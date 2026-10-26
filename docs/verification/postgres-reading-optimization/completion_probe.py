"""Run from either checkout using PYTHONPATH; only disposable synthetic schemas."""
import argparse
import contextvars
from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time

import psycopg
from fastapi.testclient import TestClient
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.postgres import isolated_database
from tests.support.worker_library import worker_library
from tests.support.app import create_app
from tests.support.reader import HEADERS

parser = argparse.ArgumentParser()
parser.add_argument('--dsn', required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
recording = contextvars.ContextVar('completion_probe', default=None)
all_records = []
execute = psycopg.Connection.execute
executemany = psycopg.Cursor.executemany


def observed(connection, query, *parameters, **kwargs):
    started = time.perf_counter()
    result = execute(connection, query, *parameters, **kwargs)
    pg = result.pgresult
    payload = sum(len(value) for row in range(pg.ntuples) for col in range(pg.nfields)
                  if (value := pg.get_value(row, col)) is not None) if pg else 0
    record = dict(sql=query if isinstance(query, str) else str(query), ms=1000*(time.perf_counter()-started),
                  rows=result.rowcount, result_bytes=payload, batch_rows=0, batch=False)
    all_records.append(record)
    if (records := recording.get()) is not None:
        records.append(record)
    return result


def observed_many(cursor, query, parameters, **kwargs):
    parameters = list(parameters)
    started = time.perf_counter()
    result = executemany(cursor, query, parameters, **kwargs)
    record = dict(sql=str(query), ms=1000*(time.perf_counter()-started), rows=cursor.rowcount,
                  result_bytes=0, batch_rows=len(parameters), batch=True)
    all_records.append(record)
    if (records := recording.get()) is not None:
        records.append(record)
    return result


psycopg.Connection.execute = observed
psycopg.Cursor.executemany = observed_many
results = []
for size in (10, 100, 1000):
    with isolated_database(args.dsn) as dsn, TemporaryDirectory(prefix='oko-completion-') as directory:
        with psycopg.connect(dsn, autocommit=True) as installer:
            migrate(installer, MIGRATIONS)
        root = Path(directory)
        store, original, _, _ = worker_library(root, database_url=dsn)
        store.set_work_enabled(False)
        store.processing.background = True
        with store.db:
            template = dict(store.db.execute('SELECT * FROM items WHERE id=?', (original,)).fetchone())
            fields = list(template)
            for number in range(1, size):
                identity = f'{number:032x}'
                row = dict(template, id=identity, source_id='completion:'+identity, external_id='completion:'+identity)
                store.db.execute('INSERT INTO items('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')', tuple(row[field] for field in fields))
                store.db.execute('INSERT INTO feed_publications VALUES (?,?)', ('demo', identity))
        store.db.close()
        app = create_app(root, token='test', api_only=True, database_url=dsn)
        captured = []

        @app.middleware('http')
        async def capture(request, call_next):
            records = []
            token = recording.set(records)
            started = time.perf_counter()
            try:
                response = await call_next(request)
                captured.append(dict(path=request.url.path, ms=1000*(time.perf_counter()-started),
                    execute=sum(not r['batch'] for r in records), batch_calls=sum(r['batch'] for r in records),
                    batch_rows=sum(r['batch_rows'] for r in records), rows=sum(max(0,r['rows']) for r in records),
                    result_bytes=sum(r['result_bytes'] for r in records),
                    recursive=sum('WITH RECURSIVE needed' in r['sql'] for r in records), records=records))
                return response
            finally:
                recording.reset(token)

        with TestClient(app, headers=HEADERS) as client:
            for _ in range(10000):
                live = app.state.host.execution._store
                if live is not None and not live.processing.recovering:
                    break
                time.sleep(.01)
            all_records.clear()
            preview = client.post('/api/v3/folders/demo/completion/preview')
            assert preview.status_code == 200, preview.text
            assert preview.json()['cards_count'] == preview.json()['publications_count'] == size
            completed = client.post('/api/v3/folders/demo/completion', json={'token':preview.json()['token']})
            assert completed.status_code == 200, completed.text
            total = len(all_records)
            assert client.get('/api/v3/cards').json() == []
            requests = captured[:2]
            background = total-sum(len(r['records']) for r in requests)
            for r in requests:
                r['query_counts'] = dict(Counter(record['sql'] for record in r.pop('records')))
            results.append(dict(size=size, requests=requests, additional_background_calls=background))
            for r in requests:
                print(size, {key:value for key,value in r.items() if key!='query_counts'},flush=True)
    args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2))
