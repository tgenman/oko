import json
from tests.support.storage import Store


def test_refresh_done_fetch_updates_its_explicit_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete) VALUES ('item','rss','item',0,0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','item',true,true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','item','done','failure','fetch-failed') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
        before=store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'operation'='producer:fetch' AND finished IS NULL").fetchone()
        assert json.loads(before['body'])['next_step']=='Повторите загрузку вручную.'
        store.jobs.refresh_feed('feed')
        after=store.db.execute('SELECT id,body FROM reader_problems WHERE id=%s AND finished IS NULL',(before['id'],)).fetchone()
        assert after and json.loads(after['body'])['next_step']=='Око повторит попытку автоматически.'
    finally:store.db.close()


def test_refresh_fetch_and_occurrence_roll_back_together(tmp_path,postgres_url,monkeypatch):
    import pytest
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete) VALUES ('item','rss','item',0,0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','item',true,true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('fetch','item','done',3,'failure','fetch-failed') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
        before=store.db.execute('SELECT status,generation,error,problem_code FROM jobs WHERE id=%s',(job,)).fetchone()
        saved=store.db.execute("SELECT id,body,finished FROM reader_problems WHERE body::jsonb->>'operation'='producer:fetch'").fetchone()
        original=store.problems.sync_fetch
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('abort')
        monkeypatch.setattr(store.problems,'sync_fetch',fail)
        with pytest.raises(RuntimeError,match='abort'):
            with transaction(store.db):store.jobs.refresh_feed('feed')
        assert store.db.execute('SELECT status,generation,error,problem_code FROM jobs WHERE id=%s',(job,)).fetchone()==before
        assert store.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()==saved
    finally:store.db.close()


def test_refresh_does_not_read_unrelated_fetch_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete) VALUES ('item','rss','item',0,0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','item',true,true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','item','done','failure','fetch-failed') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','foreign-'||n,'failed','failure','fetch-failed' FROM generate_series(1,10000) n")
            store.problems.sync_fetch(job,[],1)
            store.db._db.execute('ANALYZE jobs')
        statements=[];store.db.set_trace_callback(statements.append)
        try:store.jobs.refresh_feed('feed')
        finally:store.db.set_trace_callback(None)
        projection=next(q for q in statements if 'SELECT j.id,j.kind,j.item,j.status,j.error,j.problem_code,j.feed_id' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+projection,([job],)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()
