"""Compare isolated synthetic HTTP reading; choose the checkout with --repo.

OKO_TEST_POSTGRES_URL points only to a disposable PostgreSQL server. Each case
creates, migrates and drops its own schema; no production configuration is read.
"""
import argparse
import contextvars
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

parser = argparse.ArgumentParser()
parser.add_argument('--repo', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--sizes', nargs='+', type=int, default=[10, 100, 1000])
args = parser.parse_args()
sys.path.insert(0, str(args.repo.resolve()))

import psycopg
from fastapi.testclient import TestClient
from oko.storage import postgres_connection
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.postgres import isolated_database
from tests.support.worker_library import worker_library
from tests.support.app import create_app
from tests.support.reader import HEADERS, Clock

active = contextvars.ContextVar('reading_probe', default=None)
background = None
original_execute = psycopg.Connection.execute
original_many = psycopg.Cursor.executemany
original_records = postgres_connection._records


def counters():
    return dict(execute=0, executemany=0, batch_rows=0, returned_rows=0,
                decoded_rows=0, decoded_value_bytes=0, sql_ms=0.)


def selected():
    current = active.get()
    return current if current is not None else background


def execute(connection, query, *parameters, **options):
    start = time.perf_counter()
    cursor = original_execute(connection, query, *parameters, **options)
    target = selected()
    if target is not None:
        target['execute'] += 1
        target['sql_ms'] += (time.perf_counter() - start) * 1000
        if cursor.description:
            target['returned_rows'] += max(0, cursor.rowcount)
    return cursor


def executemany(cursor, query, values, *parameters, **options):
    rows = list(values)
    target = selected()
    if target is not None:
        target['executemany'] += 1
        target['batch_rows'] += len(rows)
    return original_many(cursor, query, rows, *parameters, **options)


def records(cursor):
    make = original_records(cursor)
    def row(values):
        target = selected()
        if target is not None:
            target['decoded_rows'] += 1
            target['decoded_value_bytes'] += sum(len(str(value).encode('utf-8')) for value in values if value is not None)
        return make(values)
    return row


psycopg.Connection.execute = execute
psycopg.Cursor.executemany = executemany
postgres_connection._records = records
report = dict(repo=str(args.repo.resolve()), head=subprocess.check_output(['git', '-C', str(args.repo), 'rev-parse', 'HEAD'], text=True).strip(),
              note='Working source tree; read-only HTTP requests. SQL timings include instrumentation. Decoded value bytes are UTF-8 lengths, not PostgreSQL protocol bytes. execute excludes trigger statements; executemany rows are counted separately.', cases=[])
dsn = os.environ['OKO_TEST_POSTGRES_URL']
for axis in ('feeds', 'materials'):
    for size in args.sizes:
        clock = Clock()
        with isolated_database(dsn) as private_dsn, TemporaryDirectory(prefix='oko-reading-probe-') as directory:
            with psycopg.connect(private_dsn, autocommit=True) as installer:
                migrate(installer, MIGRATIONS)
            root = Path(directory)
            store, first, _, _ = worker_library(root, database_url=private_dsn)
            store.set_work_enabled(False)
            store.processing.background = True
            with store.db:
                if axis == 'feeds':
                    store.db.executemany("INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport) VALUES (?,?,?,'rss','demo','[]','connected','rss')",
                        [(f'{n+100000:032x}', f'{n+100000:032x}', f'Synthetic {n}') for n in range(size-1)])
                else:
                    template = dict(store.db.execute('SELECT * FROM items WHERE id=?', (first,)).fetchone())
                    fields = list(template)
                    rows = []
                    for n in range(1, size):
                        identity = f'{n:032x}'
                        row = dict(template, id=identity, source_id=identity, external_id=identity)
                        rows.append(tuple(row[field] for field in fields))
                    store.db.executemany('INSERT INTO items('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')', rows)
                    store.db.executemany('INSERT INTO feed_publications VALUES (?,?)', [('demo', f'{n:032x}') for n in range(1, size)])
            store.db.close()
            app = create_app(root, token='test', api_only=True, database_url=private_dsn, **clock.options())
            captured = []
            @app.middleware('http')
            async def capture(request, call_next):
                counter = counters()
                token = active.set(counter)
                try:
                    response = await call_next(request)
                    captured.append(counter)
                    return response
                finally:
                    active.reset(token)
            case = dict(axis=axis, size=size, feeds=size if axis=='feeds' else 1, materials=size if axis=='materials' else 1, requests=[])
            background = counters()
            with TestClient(app, headers=HEADERS) as client:
                deadline = time.monotonic() + 120
                while True:
                    storage = app.state.host.execution._store
                    if storage is not None and not storage.processing.recovering:
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError('Synthetic recovery did not finish')
                    time.sleep(.02)
                assert client.get('/api/v3/status').status_code == 200
                case['startup_and_recovery'] = background
                background = None
                captured.clear()
                def request(name, method, path, **options):
                    global background
                    background = counters()
                    start = time.perf_counter()
                    response = client.request(method, path, **options)
                    elapsed = (time.perf_counter()-start)*1000
                    assert response.status_code == 200, (name, response.status_code, response.text[:400])
                    bg = background
                    background = None
                    case['requests'].append(dict(name=name, method=method, path=path, response_bytes=len(response.content),
                        ms=round(elapsed,3), request=captured.pop(), concurrent_background=bg))
                    return response.json()
                request('subscriptions', 'GET', '/api/v3/subscriptions')
                request('navigation', 'POST', '/api/v3/reading/check', json=dict(storage_id=None, resources=[dict(kind='navigation',known_version=None)]))
                body = dict(storage_id=None, resources=[dict(kind='list', known_version=None, known_cards={}, options=dict(target=dict(scope='all'), sort='newest',show_hidden=False))])
                first_list = request('compact', 'POST', '/api/v3/reading/check', json=body)
                listing = next(resource for resource in first_list['resources'] if resource['kind']=='list')
                body['storage_id'] = first_list['storage_id']
                body['resources'][0].update(known_version=listing['version'],known_cards={resource['id']:resource['version'] for resource in first_list['resources'] if resource['kind']=='card'})
                request('unchanged', 'POST', '/api/v3/reading/check', json=body)
                request('scoped_cards', 'GET', '/api/v3/cards', params={'feed':'demo'})
                request('opened', 'GET', '/api/v3/reading/materials/'+first)
                background = counters()
                time.sleep(.15)
                case['idle_background_150ms'] = background
                background = None
            report['cases'].append(case)
            args.output.write_text(json.dumps(report, indent=2)+'\n')
            print(axis, size, [(entry['name'],entry['request']['execute']) for entry in case['requests']], flush=True)
