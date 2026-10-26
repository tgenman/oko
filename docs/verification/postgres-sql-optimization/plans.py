"""Reproduce local plans; OKO_TEST_POSTGRES_URL must name a disposable database."""
import json
import os
from pathlib import Path

import psycopg

from tests.support.postgres import isolated_database
from oko.storage.postgres_migrations import migrate, verify
from oko.storage.postgres_schema import MIGRATIONS


output = Path(__file__).with_name('plans.json')
results = {'plans': {}, 'equal_cases': []}
with isolated_database(os.environ['OKO_TEST_POSTGRES_URL']) as dsn, psycopg.connect(dsn, autocommit=True) as db:
    migrate(db, MIGRATIONS[:3])
    # Planner ballast only; suppress fixture-trigger work in this private schema.
    db.execute('SET session_replication_role=replica')
    db.execute("INSERT INTO items(id,source_id,platform,external_id) SELECT 'i'||n,'i'||n,'rss','i'||n FROM generate_series(1,50000) n")
    db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm'||n,'i'||n,'r','https://example.invalid','a','ready' FROM generate_series(1,10000) n")
    db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'i'||n,'i'||(n+1) FROM generate_series(1,49999) n")
    db.execute("INSERT INTO jobs(kind,item,status) SELECT 'fetch','i'||n,'done' FROM generate_series(1,50000) n")
    db.execute("INSERT INTO jobs(kind,item,status) SELECT 'download_media','m'||n,'done' FROM generate_series(1,10000) n")
    db.execute('SET session_replication_role=origin')
    db.execute('ANALYZE')

    def query(ids, union):
        marks = ','.join('%s' for _ in ids)
        first = f"""SELECT j.*,coalesce(m.publication_id,j.item) AS reader_item FROM jobs j
            LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item WHERE j.item IN ({marks})"""
        second = f""" UNION SELECT j.*,m.publication_id AS reader_item FROM media_files m
            JOIN jobs j ON j.kind='download_media' AND m.id=j.item WHERE m.publication_id IN ({marks})"""
        return first + (second if union else f' OR m.publication_id IN ({marks})')

    for ids in [('missing',), ('i1',), ('i1', 'm1'), ('i1', 'i2', 'm2')]:
        before = db.execute(query(ids, False), ids + ids).fetchall()
        after = db.execute(query(ids, True), ids + ids).fetchall()
        assert set(before) == set(after) and len(before) == len(after), ids
        results['equal_cases'].append({'ids': ids, 'rows': len(after)})

    def plan(name, statement, args):
        result = db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ' + statement, args).fetchone()[0][0]
        results['plans'][name] = result
        print(name, round(result['Execution Time'], 3), result['Plan'].get('Shared Hit Blocks'), flush=True)

    for repeat in range(3):
        plan('jobs_before_' + str(repeat), query(('i1',), False), ('i1', 'i1'))
        plan('jobs_after_' + str(repeat), query(('i1',), True), ('i1', 'i1'))
    reverse = 'SELECT source_publication_id FROM publication_links WHERE target_publication_id=%s'
    plan('reverse_before', reverse, ('i25000',))
    migrate(db, MIGRATIONS)
    verify(db, MIGRATIONS)
    plan('reverse_after', reverse, ('i25000',))
    results['server'] = db.execute('SELECT version()').fetchone()[0]
    results['migration_versions'] = [row[0] for row in db.execute('SELECT version FROM oko_schema_migrations ORDER BY version')]
output.write_text(json.dumps(results, indent=2) + '\n')
