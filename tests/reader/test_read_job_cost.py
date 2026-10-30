from tests.support.storage import Store
from tests.support.postgres_work import scan_rows


def test_status_query_ignores_managed_read_jobs(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'read','foreign-'||n,'failed','failure','source-connection' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        statements=[];store.db.set_trace_callback(statements.append)
        try: assert not [p for p in store.problems.candidates(1) if p.get('operation')=='producer:read']
        finally: store.db.set_trace_callback(None)
        query=next(q for q in statements if 'SELECT jobs . *' in q)
        explained=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]
        assert scan_rows(explained['Plan'])<1000
    finally:store.db.close()


def test_unidentifiable_legacy_status_problem_survives_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            old=dict(id='job:source-x:read:',code='source-x',section='object',title='Error',consequence='Saved',next_step='Retry',objects=[],action=None,operation='producer:status',created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
            store.problems.sync_candidate(old['id'],old,1)
            store.db.execute("DELETE FROM state WHERE key='read-problems-recovered-v1'")
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()[0] is None
        store.problems.snapshot(2)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()[0] is None
    finally:store.db.close()


def test_read_sync_does_not_scan_same_feed_unrelated_command_problems(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            body=json.dumps(dict(code='action-failed',operation='POST:/x',objects=[dict(kind='feed',id='feed')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'p'||n,'action:'||n,'action',%s,1 FROM generate_series(1,10000) n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        statements=[];store.db.set_trace_callback(statements.append)
        try:
            with store.db:store.problems.sync_read_feed('feed',2)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in statements if 'WITH affected AS MATERIALIZED' in q)
        needle=json.dumps([dict(kind='feed',id='feed')])
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(needle,)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000
    finally:store.db.close()
