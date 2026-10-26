"""Measure existing before-commit list maintenance; no application code changes.

This is a storage-level cost experiment, not a substitute for HTTP behavior tests.
Run from the checkout with PYTHONPATH=. and OKO_TEST_POSTGRES_URL set to a
synthetic server. Every case owns a temporary schema and data directory.
"""
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import psycopg

from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from oko.storage.store import Store
from tests.support.postgres import isolated_database


recording = ContextVar('list_cost', default=None)
execute, sha256, dumps = psycopg.Connection.execute, hashlib.sha256, json.dumps


def measured_execute(connection, sql, params=None, **kwargs):
    started = perf_counter()
    cursor = execute(connection, sql, params, **kwargs)
    record = recording.get()
    if record is not None:
        is_ids = sql.replace(' ', '').startswith('SELECTi.idFROMitems')
        record['sql'].append(dict(sql=sql, ms=(perf_counter()-started)*1000, rows=cursor.rowcount, ids=is_ids))
    return cursor


def measured_hash(data=b'', **kwargs):
    started = perf_counter()
    value = sha256(data, **kwargs)
    record = recording.get()
    if record is not None:
        record['hash_ms'] += (perf_counter()-started)*1000
        record['hashed_bytes'] += len(data)
    return value


def measured_json(value, *args, **kwargs):
    started = perf_counter()
    result = dumps(value, *args, **kwargs)
    record = recording.get()
    if record is not None:
        record['json_ms'] += (perf_counter()-started)*1000
    return result


psycopg.Connection.execute, hashlib.sha256, json.dumps = measured_execute, measured_hash, measured_json
results = []
for size in (10, 100, 1000):
    for variants in (1, 6):
        with isolated_database(os.environ['OKO_TEST_POSTGRES_URL']) as dsn, TemporaryDirectory(prefix='oko-list-cost-') as directory:
            with psycopg.connect(dsn, autocommit=True) as installer:
                migrate(installer, MIGRATIONS)
            root = Path(directory)
            store = Store(root, lambda _: None, database_url=dsn)
            store.subscriptions.ensure_demo()
            with store.db:
                store.db.executemany("INSERT INTO items(id,feed_id,published,platform,external_id) VALUES (?,'demo',?,'other_web',?)",
                                     [(f'item-{n:04}', n, f'item-{n:04}') for n in range(size)])
                store.db.executemany("INSERT INTO feed_publications VALUES ('demo',?)", [(f'item-{n:04}',) for n in range(size)])
            options = [dict(target=target, sort=sort, show_hidden=hidden)
                       for target in ({'scope':'all'}, {'scope':'feed','feed_id':'demo'}, {'scope':'folder','folder_id':'demo'})
                       for sort, hidden in ([('newest',False)] if variants == 1 else [(s,h) for s in ('newest','oldest','importance') for h in (False,True)])]
            for option in options:
                store.read_versions.list_version(option)
            case = dict(items=size, variants_per_scope=variants, scopes=3, registered_lists=len(options), operations=[])
            def versions():
                return [store.read_versions.list_version(option)['version'] for option in options]
            flush = store.db.before_commit
            capture_flush = [False]
            records = []
            def observed_flush():
                if not capture_flush[0]:
                    return flush()
                record = dict(sql=[], hash_ms=0., json_ms=0., hashed_bytes=0)
                token = recording.set(record)
                started = perf_counter()
                try:
                    return flush()
                finally:
                    record['flush_ms'] = (perf_counter()-started)*1000
                    recording.reset(token)
                    records.append(record)
            store.db.before_commit = observed_flush
            def change(name, statements, rollback=False):
                before = versions()
                records.clear()
                capture_flush[0] = True
                started = perf_counter()
                try:
                    with store.db:
                        for sql, args in statements:
                            store.db.execute(sql,args)
                        if rollback:
                            raise RuntimeError('synthetic rollback')
                except RuntimeError:
                    if not rollback:
                        raise
                finally:
                    capture_flush[0] = False
                elapsed = (perf_counter()-started)*1000
                after = versions()
                operation = dict(name=name, transaction_ms=elapsed, version_deltas=[b-a for a,b in zip(before,after)], flushes=list(records))
                case['operations'].append(operation)
                print(size, variants, name, round(elapsed,3), [round(r['flush_ms'],3) for r in records], flush=True)
                return operation
            reorder = change('reorder', [('UPDATE items SET published=? WHERE id=?',(size+1,'item-0000'))])
            assert all(delta==1 for delta in reorder['version_deltas'])
            noop = change('noop', [('UPDATE items SET published=? WHERE id=?',(size+1,'item-0000'))])
            assert not any(noop['version_deltas'])
            netzero = change('A-B-A', [('UPDATE items SET viewed=? WHERE id=?',(1,'item-0000')),('UPDATE items SET viewed=? WHERE id=?',(0,'item-0000'))])
            assert not any(netzero['version_deltas'])
            rollback = change('rollback', [('UPDATE items SET viewed=? WHERE id=?',(1,'item-0000'))], rollback=True)
            assert not any(rollback['version_deltas']) and rollback['flushes']==[]
            membership = change('remove_membership', [('DELETE FROM feed_publications WHERE publication_id=?',('item-0000',))])
            assert all(delta==1 for delta in membership['version_deltas'])
            before_restart = versions()
            # Explain the representative read for each order. Time includes
            # sort inputs; this is not an exclusive CPU profile of Sort itself.
            case['sort_plans'] = []
            for option in options[:variants]:
                statement = store.read_versions._ids('all', ':owner', ':sort', ':hidden')
                compiled = store.db._queries.compile(statement)
                plan = store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+compiled,
                    dict(owner='',sort=option['sort'],hidden=int(option['show_hidden']))).fetchone()[0][0]
                def sorts(node):
                    return ([{key:node[key] for key in ('Node Type','Actual Total Time','Actual Rows','Sort Method','Sort Space Used','Sort Space Type') if key in node}] if node['Node Type'] in ('Sort','Incremental Sort') else []) + [item for child in node.get('Plans',[]) for item in sorts(child)]
                case['sort_plans'].append(dict(sort=option['sort'],hidden=option['show_hidden'], execution_ms=plan['Execution Time'],nodes=sorts(plan['Plan'])))
            store.db.close()
            store = Store(root, lambda _: None, database_url=dsn)
            assert versions() == before_restart
            case['restart_versions_preserved'] = True
            store.db.close()
            results.append(case)
Path(__file__).with_name('versions.json').write_text(dumps(results, indent=2)+'\n')
