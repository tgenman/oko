import json
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_fetch_failure_retry_and_finish_without_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','article','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'fetch','article',1),1,'failure',code='article-limit')
        row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article' AND finished IS NULL").fetchone()
        assert row and json.loads(row['body'])['objects']==[dict(kind='material',id='article')]
        with store.db: store.db.execute('UPDATE jobs SET next=0 WHERE id=%s',(job_id,))
        job=store.jobs.next_job(1,feeds=['feed'])
        assert job and job.id==job_id
        assert json.loads(store.db.execute('SELECT body FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0])['state']=='retrying'
        with transaction(store.db):store.jobs.finish(job)
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_owner_transfer_and_problem_change_rollback_together(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected',ARRAY[]::text[]),('b','b','B','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','rss','x')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','article',true),('b','article',false)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job_id,[],1)
            before=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:article-limit:a:fetch:article' AND finished IS NULL").fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.remove_feed('a')
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:article-limit:b:fetch:article' AND finished IS NULL").fetchone()
                raise ValueError('rollback')
        after=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:article-limit:a:fetch:article' AND finished IS NULL").fetchone()
        assert tuple(after)==tuple(before)
        assert store.db.execute("SELECT is_primary FROM feed_publications WHERE feed_id='a' AND publication_id='article'").fetchone()[0]
    finally:store.db.close()


def test_linked_fetch_failure_updates_all_owners_and_resolves_together(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('root-x','other_web','root-x'),('root-y','other_web','root-y'),('article','other_web','article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','root-x',true),('y','root-y',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-x','article'),('root-y','article')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','article','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'fetch','article',1),1,'failure',code='article-limit')
        rows=store.db.execute("SELECT identity FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL ORDER BY identity",(str(job_id),)).fetchall()
        assert [row[0] for row in rows]==['job:article-limit:x:fetch:article','job:article-limit:y:fetch:article']
        with store.db:store.db.execute('UPDATE jobs SET status=\'running\' WHERE id=%s',(job_id,))
        with transaction(store.db):store.jobs.finish(Job(job_id,'fetch','article',1))
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND outcome='resolved'",(str(job_id),)).fetchone()[0]==2
    finally:store.db.close()


def test_http_status_reads_recovered_fetch_without_mutation(tmp_path,postgres_url):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit')")
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            before=db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article' AND finished IS NULL").fetchone()
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert any(p['id']==before[0] for p in response.json()['problems']['current'])
        etag=response.headers.get('etag')
        if etag:assert client.get('/api/v3/status/details',headers={'If-None-Match':etag}).status_code==304
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article' AND finished IS NULL").fetchone()==before
