import pytest
from oko.storage.job_types import Job
from tests.support.storage import Store

@pytest.mark.parametrize('kind',['filter','theses'])
def test_implicit_failure_and_code_changes_keep_occurrence(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,'article','pending',1) RETURNING id",(kind,)).fetchone()[0]
        with store.db:
            store.db.execute("UPDATE jobs SET error='failure',problem_code=NULL WHERE id=%s",(job,))
        row=store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone()
        store.jobs._claim(row)
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row is not None
        occurrence=row['id']
        with store.db:store.db.execute("UPDATE jobs SET status='running',generation=2 WHERE id=%s",(job,))
        store.jobs.retry(Job(job,kind,'article',2),2,'specific',code='model-invalid')
        row=store.db.execute("SELECT id,body::jsonb->>'code' AS code FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row and row['id']==occurrence and row['code']=='model-invalid'
        with store.db:store.db.execute("UPDATE jobs SET status='running',generation=3 WHERE id=%s",(job,))
        from oko.storage.processing_job_problems import managed_candidates
        with store.db:
            before=managed_candidates(store,job,3)
            store.db.execute("UPDATE jobs SET status='pending',error='again',problem_code=NULL WHERE id=%s",(job,))
            store.problems.sync_processing_problem(job,before,3)
        row=store.db.execute("SELECT id,body::jsonb->>'code' AS code FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row and row['id']==occurrence and row['code']=='work-'+kind
    finally:store.db.close()

@pytest.mark.parametrize('kind',['filter','theses'])
def test_implicit_legacy_error_is_recovered_with_same_occurrence(tmp_path,postgres_url,kind):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=dict(code='work-'+kind,section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='article')],action=None,operation='producer:status',state='current',next_at=None)
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'article','failed','failure',NULL)",(kind,))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy',%s,'background',%s,1)",('job:work-'+kind+':feed:'+kind+':article',json.dumps(body)))
        store.db.close(); store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT id,body::jsonb->>'processing_job_id' AS owner FROM reader_problems WHERE id='legacy' AND finished IS NULL").fetchone()
        assert row and row['owner'] is not None
        assert any(p['id']=='legacy' for p in store.problems.snapshot(2)['current'])
    finally:store.db.close()

def test_implicit_recovery_batches_a_thousand_jobs(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT CASE WHEN n%2=0 THEN 'filter' ELSE 'theses' END,'article-'||n,'failed','failure',NULL FROM generate_series(1,1000) n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_processing_problems()
        finally:store.db.set_trace_callback(None)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'processing_job_id' IS NOT NULL AND finished IS NULL").fetchone()[0]==1000
        assert len(sql)<250,len(sql)
    finally:store.db.close()

@pytest.mark.parametrize('kind',['filter','theses'])
def test_implicit_occurrence_closes_on_finish_and_rolls_back(tmp_path,postgres_url,kind):
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error) VALUES (%s,'article','pending',1,'failure') RETURNING id",(kind,)).fetchone()[0]
            row=store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone()
            running=store.jobs._claim(row)
            occurrence=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.jobs.finish(running)
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(occurrence,)).fetchone()[0] is None
        with transaction(store.db):store.jobs.finish(running)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(occurrence,)).fetchone()[0] is not None
    finally:store.db.close()

def test_changing_implicit_code_does_not_overwrite_foreign_occurrence(tmp_path,postgres_url):
    import json
    from oko.storage.processing_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('filter','article','failed','failure') RETURNING id").fetchone()[0]
            store.problems.sync_processing_problem(job,[],1)
            old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
            foreign=dict(code='model-invalid',operation='producer:fetch',objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:model-invalid:feed:filter:article','background',%s,1)",(json.dumps(foreign),))
            previous=managed_candidates(store,job,2)
            store.db.execute("UPDATE jobs SET problem_code='model-invalid' WHERE id=%s",(job,))
            store.problems.sync_processing_problem(job,previous,2)
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0])==foreign
        row=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row and row['id']==old and row['identity'].startswith('job:processing-isolated:')
    finally:store.db.close()

@pytest.mark.parametrize('kind',['filter','theses'])
def test_http_implicit_error_reads_without_write(tmp_path,postgres_url,kind):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id) VALUES ('article')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'article','failed','failure',NULL)",(kind,))
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            before=db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:work-'+kind+':feed:'+kind+':article',)).fetchone()
        response=client.get('/api/v3/status/details')
        assert response.status_code==200 and any(p['id']==before[0] for p in response.json()['problems']['current'])
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before[0],)).fetchone()==before

@pytest.mark.parametrize('kind',['filter','theses'])
def test_removing_owner_keeps_implicit_occurrence(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected',ARRAY[]::text[]),('b','b','B','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','rss','x')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','article',true),('b','article',false)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES (%s,'article','failed','failure') RETURNING id",(kind,)).fetchone()[0]
            store.problems.sync_processing_problem(job,[],1)
            occurrence=store.db.execute("SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:work-'+kind+':a:'+kind+':article',)).fetchone()[0]
        store.remove_feed('a')
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:work-'+kind+':b:'+kind+':article',)).fetchone()
        assert row and row[0]==occurrence
    finally:store.db.close()

def test_status_does_not_scan_ten_thousand_implicit_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT CASE WHEN n%2=0 THEN 'filter' ELSE 'theses' END,'foreign-'||n,'failed','failure',NULL FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.candidates(1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if 'SELECT jobs . *' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()

@pytest.mark.parametrize('old_code,new_code',[('work-filter','model-invalid'),('model-invalid',None)])
def test_recovery_rekeys_unmarked_implicit_legacy_when_current_code_changed(tmp_path,postgres_url,old_code,new_code):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=dict(code=old_code,section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='article')],action=None,operation='producer:status',state='current',next_at=None)
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','article','failed','failure',%s)",(new_code,))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy',%s,'background',%s,1)",('job:'+old_code+':feed:filter:article',json.dumps(body)))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:'+(new_code or 'work-filter')+':feed:filter:article',)).fetchone()
        assert row and row[0]=='legacy'
    finally:store.db.close()

def test_empty_code_without_error_matches_legacy_status_and_event(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article'),('clean')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','article','pending',NULL,'') RETURNING id").fetchone()[0]
            clean=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','clean','pending',NULL,NULL) RETURNING id").fetchone()[0]
        store.problems.recover_processing_problems()
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(clean),)).fetchone()
        assert not any(p.get('code')=='work-filter' for p in store.problems.candidates(1))
        with store.db:
            row=store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone()
            store.jobs._claim(row)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]==1
    finally:store.db.close()
