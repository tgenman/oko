import json
from tests.support.storage import Store


def test_legacy_unrelated_material_without_job_is_preserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id) VALUES ('feed')")
            store.db.execute("INSERT INTO publications(id) VALUES ('unrelated')")
            body=dict(code='article-limit',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='unrelated')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:ghost','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None
        assert json.loads(row['body'])==body
    finally:store.db.close()


def test_unrelated_legacy_without_job_survives_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='article-limit',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='unrelated')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('unrelated')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:ghost','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        store.problems.snapshot(2)
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


def test_legacy_invalid_object_is_not_silently_finished(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='article-limit',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='target'),dict(kind='feed',id='elsewhere')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('target')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:target','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        store.problems.snapshot(2)
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


def test_proven_legacy_link_without_job_can_finish(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='article-limit',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='root')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('root'),('target')")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','target')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:target','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE id='old'").fetchone()[0]=='resolved'
    finally:store.db.close()


def test_legacy_link_proof_ignores_unrelated_links(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) SELECT 'root-'||n FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO publications(id) SELECT 'target-'||n FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'root-'||n,'target-'||n FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE publication_links')
        sql=[];store.db.set_trace_callback(sql.append)
        try:assert store.problems.fetch_linked_pairs({'target-1'},{'root-1'})=={('root-1','target-1')}
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if q.startswith('SELECT source_publication_id,target_publication_id'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query,(['target-1'],['root-1'])).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()
