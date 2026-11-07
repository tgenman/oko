import json
import pytest
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


@pytest.mark.parametrize('kind',['filter','theses'])
def test_processing_failure_and_finish_are_saved_without_status_read(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
            store.db.execute("UPDATE publications SET feed_id='feed' WHERE id='article'")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,'article','running',1) RETURNING id",(kind,)).fetchone()[0]
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.jobs.retry(Job(job_id,kind,'article',1),1,'failure',code='model-invalid')
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'operation'=%s AND finished IS NULL",('producer:'+kind,)).fetchone()
                raise ValueError('rollback')
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'operation'=%s AND finished IS NULL",('producer:'+kind,)).fetchone()
        store.jobs.retry(Job(job_id,kind,'article',1),1,'failure',code='model-invalid')
        row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:model-invalid:feed:'+kind+':article',)).fetchone()
        assert row and json.loads(row['body'])['objects']==[dict(kind='material',id='article')]
        with store.db:store.db.execute("UPDATE jobs SET status='running',generation=2 WHERE id=%s",(job_id,))
        with transaction(store.db):store.jobs.finish(Job(job_id,kind,'article',2))
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_settings_reconciliation_and_problem_roll_back_together(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    from oko.subscriptions.subscriptions import Overrides
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        with store.db:
            row=store.db.execute("UPDATE jobs SET status='pending',error='failure',problem_code='model-invalid' WHERE kind='filter' AND item=%s RETURNING id",(item,)).fetchone()
            store.problems.sync_processing_problem(row['id'],[],1)
            before=store.db.execute("SELECT id,body,finished FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(row['id']),)).fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.subscriptions.update_settings('feed','fixture',Overrides(filter_enabled=False))
                store.settings_changed('feed','fixture')
                assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]=='blocked'
                raise ValueError('rollback')
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]=='pending'
        after=store.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_http_status_reads_recovered_processing_without_mutation(tmp_path,postgres_url,kind):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
        db.execute("UPDATE publications SET feed_id='feed' WHERE id='article'")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'article','failed','failure','model-invalid')",(kind,))
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            before=db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:model-invalid:feed:'+kind+':article',)).fetchone()
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert any(p['id']==before[0] for p in response.json()['problems']['current'])
        etag=response.headers.get('etag')
        if etag:assert client.get('/api/v3/status/details',headers={'If-None-Match':etag}).status_code==304
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before[0],)).fetchone()==before
