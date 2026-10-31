import pytest
from tests.support.storage import Store
from oko.storage.job_types import Job


@pytest.mark.parametrize('kind,code',[('filter','filter-error'),('theses','theses-error'),('filter',''),('theses','')])
def test_removing_selected_feed_closes_cancelled_stage_problem(tmp_path,postgres_url,kind,code):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO folders(id,title) VALUES ('folder','Folder') ON CONFLICT DO NOTHING")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','folder','rss','rss','connected',ARRAY[]::text[]),('b','b','B','folder','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,processing_metadata) VALUES ('p',0,'rss','p',%s)",(json.dumps({'_pass':{'feed_id':'a'}}),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES (%s,'p','pending',%s,'failure') RETURNING id",(kind,code)).fetchone()[0]
            store.problems.sync_processing_problem(job,[],1)
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
        store.remove_feed('a')
        assert store.db.execute("SELECT 1 FROM publications WHERE id='p' AND viewed=0").fetchone()
        jobrow=store.db.execute('SELECT status,error,problem_code FROM jobs WHERE id=%s',(job,)).fetchone()
        assert jobrow['status']=='cancelled',dict(jobrow)
        row=store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()
        assert row['finished'] is not None,dict(row)
    finally:store.db.close()

def test_removing_selected_feed_rolls_back_stage_capture(tmp_path,postgres_url,monkeypatch):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO folders(id,title) VALUES ('folder','Folder') ON CONFLICT DO NOTHING")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','folder','rss','rss','connected',ARRAY[]::text[]),('b','b','B','folder','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,processing_metadata) VALUES ('p',0,'rss','p',%s)",(json.dumps({'_pass':{'feed_id':'a'}}),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('filter','p','pending',2,'filter-error','failure') RETURNING id").fetchone()[0]
            store.problems.sync_processing_problem(job,[],1)
        before=store.db.execute("SELECT id,identity,body,finished,outcome FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        original=store.problems.sync_processing_problems
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('abort')
        monkeypatch.setattr(store.problems,'sync_processing_problems',fail)
        with pytest.raises(RuntimeError,match='abort'):store.remove_feed('a')
        assert store.db.execute('SELECT id,identity,body,finished,outcome FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()==before
        j=store.db.execute('SELECT status,generation,problem_code,error FROM jobs WHERE id=%s',(job,)).fetchone()
        assert tuple(j)==('pending',2,'filter-error','failure')
        assert store.db.execute("SELECT 1 FROM feed_publications WHERE feed_id='a' AND publication_id='p'").fetchone()
        assert store.db.execute("SELECT connection FROM feeds WHERE id='a'").fetchone()[0]=='connected'
    finally:store.db.close()

def test_selected_feed_stage_capture_ignores_foreign_jobs(tmp_path,postgres_url):
    import json
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO folders(id,title) VALUES ('folder','Folder') ON CONFLICT DO NOTHING")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','folder','rss','rss','connected',ARRAY[]::text[]),('b','b','B','folder','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,processing_metadata) VALUES ('p',0,'rss','p',%s)",(json.dumps({'_pass':{'feed_id':'a'}}),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('filter','p','pending','filter-error','failure') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) SELECT 'filter','foreign-'||n,'pending','filter-error','failure' FROM generate_series(1,10000)n")
            store.problems.sync_processing_problem(job,[],1)
            store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.remove_feed('a')
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT id,kind,identity_event_owned FROM jobs j WHERE kind=ANY(%s) AND item=ANY(%s)'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(['filter','theses'],['p'])).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()
