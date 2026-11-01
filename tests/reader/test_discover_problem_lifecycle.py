import json
import pytest
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_discover_failure_and_completion_are_saved_without_status_read(tmp_path, postgres_url):
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job_id = store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('discover','feed','running',1) RETURNING id").fetchone()[0]
        job=Job(job_id,'discover','feed',1)
        identity='job:source-connection:feed:discover'
        def current():
            return store.db.execute('SELECT id,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.jobs.retry(job,1,'source failed',code='source-connection')
                assert current()
                raise ValueError('rollback')
        assert current() is None
        store.jobs.retry(job,1,'source failed',code='source-connection')
        prior=current(); assert prior
        with store.db: store.db.execute('UPDATE jobs SET next=0 WHERE id=%s',(job_id,))
        claimed=store.jobs.next_job(1,feeds=['feed'])
        assert claimed and claimed.id==job_id
        assert json.loads(current()['body'])['state']=='retrying'
        with transaction(store.db): store.jobs.finish(claimed)
        assert current() is None
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(prior['id'],)).fetchone()[0]=='resolved'
    finally: store.db.close()


def test_profile_problem_suppresses_discovery_failure_without_status_read(tmp_path,postgres_url):
    from tests.support.read_problem import setup
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation) VALUES ('discover','feed','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'discover','feed',1),1,'failure',code='identity-unconfirmed')
        identity='job:identity-unconfirmed:feed:discover:feed'
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert not store.check_telegram_profile('personal',1,202,2)
        assert not store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert store.check_telegram_profile('personal',1,101,3)
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
    finally:store.db.close()


def test_http_reports_recovered_source_failure_without_get_writes(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    import psycopg
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover','feed','failed','failure','source-connection')")
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            before=db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:source-connection:feed:discover' AND finished IS NULL").fetchone()
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert any(p['id']==before[0] for p in response.json()['problems']['current'])
        etag=response.headers.get('etag')
        if etag: assert client.get('/api/v3/status/details',headers={'If-None-Match':etag}).status_code==304
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:source-connection:feed:discover' AND finished IS NULL").fetchone()==before
