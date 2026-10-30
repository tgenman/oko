from pathlib import Path
import argparse
from tempfile import TemporaryDirectory
from tests.reader.test_history_dispatch import archive_seed
from tests.support.storage import Store, analyze

parser=argparse.ArgumentParser()
parser.add_argument('--foreign-jobs', action='store_true')
args=parser.parse_args()

with TemporaryDirectory(prefix='oko-membership-plan-') as root:
    root=Path(root)
    archive_seed(root)
    store=Store(root,lambda _:None)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id) VALUES ('live',0,'other_web','live')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('archive','live',true,true)")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection) VALUES ('foreign','foreign','Foreign','default','rss','rss','connected')")
            store.db.executemany("INSERT INTO publications(id,viewed,platform,external_id) VALUES (%s,0,'other_web',%s)", [(f'foreign-{i}',f'foreign-{i}') for i in range(10000)])
            store.db.executemany("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('foreign',%s,true,true)", [(f'foreign-{i}',) for i in range(10000)])
            analyze(store.db)
        queries = {
            'current': "SELECT i.id FROM feed_publications m JOIN publications i ON i.id=m.publication_id WHERE m.feed_id='archive' AND m.is_primary AND m.is_unviewed AND i.viewed=0",
            'direct': "SELECT i.id FROM feed_publications m JOIN publications i ON i.id=m.publication_id WHERE m.feed_id='archive' AND m.is_primary AND i.viewed=0",
            'live_first': "WITH live AS MATERIALIZED (SELECT id FROM publications WHERE viewed=0) SELECT i.id FROM live i JOIN feed_publications m ON m.publication_id=i.id WHERE m.feed_id='archive' AND m.is_primary",
        }
        if args.foreign_jobs:
            store.db.executemany("INSERT INTO jobs(kind,item,status,next) VALUES ('fetch',%s,'pending',0)", [(f'foreign-{i}',) for i in range(10000)])
        store.db.execute("INSERT INTO jobs(kind,item,status,next) VALUES ('fetch','live','pending',0)")
        store.db.commit()
        from oko.storage.transaction import transaction
        from tests.support.postgres_work import scan_rows
        class Rollback(Exception):
            pass
        for modified in (False, True):
            original=store.db.execute
            stats={'rows_scanned':0,'selects':0}
            def execute(query, parameters=()):
                sql=query.replace('owner.is_unviewed AND ', '') if modified else query
                cursor=original(sql,parameters)
                if sql.lstrip().upper().startswith('SELECT'):
                    plan=store.db._db.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+sql, parameters or None).fetchone()[0][0]['Plan']
                    stats['rows_scanned']+=scan_rows(plan)
                    stats['selects']+=1
                return cursor
            try:
                with transaction(store.db):
                    store.db.execute=execute
                    job=store.jobs.next_job(0,feeds=['archive'])
                    assert job and job.item=='live'
                    raise Rollback()
            except Rollback:
                pass
            finally:
                store.db.execute=original
            print('dispatch_without_flag' if modified else 'dispatch_current',stats,flush=True)
        correlated = """SELECT j.id FROM jobs j WHERE j.kind='fetch' AND j.status='pending' AND j.next<=0
            AND (EXISTS (SELECT 1 FROM feed_publications owner JOIN publications own ON own.id=owner.publication_id
                    WHERE owner.publication_id=j.item AND owner.is_primary AND owner.feed_id='archive' AND own.viewed=0)
                 OR (NOT EXISTS (SELECT 1 FROM feed_publications owner WHERE owner.publication_id=j.item AND owner.is_primary)
                     AND EXISTS (SELECT 1 FROM publications article JOIN publication_links l ON l.target_publication_id=article.id
                         JOIN publications root ON root.id=l.source_publication_id
                         JOIN feed_publications owner ON owner.publication_id=root.id
                         WHERE article.id=j.item AND article.viewed=0 AND root.viewed=0 AND owner.is_primary AND owner.feed_id='archive')))
            AND NOT EXISTS (SELECT 1 FROM history_members m JOIN initial_history h ON h.feed=m.feed
                WHERE (h.paused=1 OR (h.scan_complete=0 AND (h.selection::jsonb->>'mode'='count'
                    OR h.feed IN (SELECT id FROM feeds WHERE transport='rss')))) AND m.item=j.item OFFSET 0)
            ORDER BY j.id LIMIT 1"""
        plan=store.db._db.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+correlated).fetchone()[0][0]
        print('candidate_correlated_fetch',{'rows_scanned':scan_rows(plan['Plan']),'execution_ms':round(plan['Execution Time'],3),'output':plan['Plan']['Actual Rows']},flush=True)
        for name,query in queries.items():
            plan=store.db._db.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+query).fetchone()[0][0]
            def total(node): return node['Actual Rows']*node['Actual Loops']+sum(total(child) for child in node.get('Plans',[]))
            print(name,{'rows_touched':total(plan['Plan']),'execution_ms':round(plan['Execution Time'],3),'output':plan['Plan']['Actual Rows']},flush=True)
    finally:
        store.db.close()
