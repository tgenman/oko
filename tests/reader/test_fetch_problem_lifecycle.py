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
            store.db.execute("UPDATE publications SET feed_id='feed' WHERE id='article'")
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


def test_owner_removal_and_problem_change_rollback_together(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected',ARRAY[]::text[]),('b','b','B','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','rss','x')")
            store.db.execute("UPDATE publications SET feed_id='a' WHERE id='article'")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job_id,[],1)
            before=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:article-limit:a:fetch:article' AND finished IS NULL").fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.remove_feed('a')
                assert store.db.execute("SELECT feed_id FROM publications WHERE id='article'").fetchone()[0] is None
                raise ValueError('rollback')
        after=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:article-limit:a:fetch:article' AND finished IS NULL").fetchone()
        assert tuple(after)==tuple(before)
        assert store.db.execute("SELECT feed_id FROM publications WHERE id='article'").fetchone()[0] == 'a'
    finally:store.db.close()


def test_linked_fetch_failure_updates_all_owners_and_resolves_together(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('root-x','other_web','root-x'),('root-y','other_web','root-y'),('article','other_web','article')")
            store.db.execute("UPDATE publications SET feed_id=CASE id WHEN 'root-x' THEN 'x' WHEN 'root-y' THEN 'y' END WHERE id IN ('root-x','root-y')")
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
        db.execute("UPDATE publications SET feed_id='feed' WHERE id='article'")
        db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code) VALUES ('fetch','article','failed','failure','article-limit')")
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


def test_manual_fetch_retry_clears_reason_but_preserves_source_wait(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
            deadline=store.wall_clock()+3600
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,reason_code,reason_message,wait_scope,retry_at,next) VALUES ('fetch','article','unavailable',4,'source-wait','old','source',%s,123) RETURNING id",(deadline,)).fetchone()[0]
            store.jobs.enqueue_fetch('article')
            row=store.db.execute('SELECT status,generation,wait_scope,retry_at,reason_code,reason_message FROM jobs WHERE id=%s',(job,)).fetchone()
            assert tuple(row)==('pending',5,'source',deadline,None,None)
    finally:store.db.close()


def test_fetch_diagnostics_reads_job_reason(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code) VALUES ('fetch','article','failed','Current explanation','fetch-denied') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],store.wall_clock())
            row=store.db.execute("SELECT body FROM reader_problems WHERE identity='job:fetch-denied:feed:fetch:article' AND finished IS NULL").fetchone()
            assert row and json.loads(row['body'])['reason']=='Current explanation'
    finally:store.db.close()


def test_unavailable_fetch_without_optional_reason_remains_visible(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status) VALUES ('fetch','article','unavailable') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],store.wall_clock())
            row=store.db.execute("SELECT body FROM reader_problems WHERE identity='job:work-fetch:feed:fetch:article' AND finished IS NULL").fetchone()
            assert row and json.loads(row['body'])['objects']==[dict(kind='material',id='article')]
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        rows=store.problems.snapshot(store.wall_clock())['current']
        assert any(p['code']=='work-fetch' and any(o['id']=='article' for o in p['objects']) for p in rows)
    finally:store.db.close()


def test_restart_recovers_unavailable_fetch_without_optional_reason(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    with store.db:
        store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
        store.db.execute("INSERT INTO jobs(kind,item,status) VALUES ('fetch','article','unavailable')")
    store.db.close()
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        rows=store.problems.snapshot(store.wall_clock())['current']
        assert any(p['code']=='work-fetch' and any(o['id']=='article' for o in p['objects']) for p in rows)
    finally:store.db.close()


def test_cancelling_history_does_not_mark_failed_fetch_successful(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
            store.db.execute("INSERT INTO history_members(feed,item,outcome) VALUES ('feed','article','pending')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('saved','other_web','saved','feed')")
            store.db.execute("INSERT INTO history_members(feed,item,outcome) VALUES ('feed','saved','saved')")
            store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','saved','done',1)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,reason_code,reason_message) VALUES ('fetch','article','failed',1,'fetch-failed','Unavailable') RETURNING id").fetchone()[0]
            store.jobs.cancel_history('feed')
            row=store.db.execute('SELECT status,generation,reason_code,reason_message FROM jobs WHERE id=%s',(job,)).fetchone()
            assert tuple(row)==('cancelled',2,None,None)
            assert tuple(store.db.execute("SELECT status,generation FROM jobs WHERE kind='fetch' AND item='saved'").fetchone())==('done',1)
    finally:store.db.close()
