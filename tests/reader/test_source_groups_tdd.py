from tests.support.job_claim import claim_job, retry_job
import pytest
from tests.support.storage import Store


@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses'])
def test_job_events_create_and_finish_source_occurrence_without_get(kind,tmp_path,postgres_url):
    from oko.domain.job import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','other_web','p')")
            store.db.execute("UPDATE publications SET feed_id='f' WHERE id='p'")
            if kind=='download_media':store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','file','unavailable')")
            item='m' if kind=='download_media' else 'f' if kind=='feed-delete' else 'p'
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,%s,'running',1) RETURNING id",(kind,item)).fetchone()[0]
        retry_job(store, Job(job,kind,item,1),1,'failure',code='source-network')
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'=%s AND finished IS NULL",('job:source-network:f:'+kind,)).fetchone()
        assert row
        with transaction(store.db):store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
        with transaction(store.db):store.jobs.finish(Job(job,kind,item,1))
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0] is not None
    finally:store.db.close()


def test_source_failure_and_group_rollback_together(tmp_path,postgres_url):
    from oko.domain.job import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','other_web','p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                retry_job(store, Job(job,'fetch','p',1),1,'failure',code='source-network')
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity' IS NOT NULL AND finished IS NULL").fetchone()
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='running'
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity' IS NOT NULL AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_source_failure_changes_group_after_retry_claim(tmp_path,postgres_url):
    from oko.domain.job import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','other_web','p')")
            store.db.execute("UPDATE publications SET feed_id='f' WHERE id='p'")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        retry_job(store, Job(job_id,'fetch','p',1),1,'offline',code='source-network')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:fetch' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):store.db.execute('UPDATE jobs SET next=0 WHERE id=%s',(job_id,))
        claimed=claim_job(store, 1,feeds=['f'])
        assert claimed
        assert store.db.execute("SELECT body::jsonb->>'state' FROM reader_problems WHERE id=%s",(old,)).fetchone()[0]=='retrying'
        retry_job(store, claimed,1,'access changed',code='source-reconnect')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-reconnect:f:fetch' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_removing_one_of_two_source_roots_keeps_both_saved_groups(tmp_path,postgres_url):
    from oko.storage.transaction import transaction
    from oko.domain.job import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected',ARRAY[]::text[]),('b','b','B','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('ra','rss','ra'),('rb','rss','rb'),('p','rss','p')")
            store.db.execute("UPDATE publications SET feed_id=CASE id WHEN 'ra' THEN 'a' WHEN 'rb' THEN 'b' END WHERE id IN ('ra','rb')")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('ra','p'),('rb','p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        retry_job(store, Job(job,'fetch','p',1),1,'failure',code='source-network')
        a=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:a:fetch' AND finished IS NULL").fetchone()[0]
        b=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:b:fetch' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):
            store.remove_feed('a')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(a,)).fetchone()[0] is None
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(b,)).fetchone()[0] is None
    finally:store.db.close()


def test_http_source_group_is_visible_and_get_does_not_write(tmp_path,postgres_url):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from oko.storage.source_groups import sync_jobs
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    with store.db as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','other_web','p')")
        db.execute("UPDATE publications SET feed_id='f' WHERE id='p'")
        job=db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code) VALUES ('fetch','p','failed','failure','source-network') RETURNING id").fetchone()[0]
        sync_jobs(store,[job],store.wall_clock())
    store.db.close()
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            before=db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:fetch' AND finished IS NULL").fetchone()
        assert before
        response=client.get('/api/v3/status/details')
        assert response.status_code==200 and any(p['id']==before[0] for p in response.json()['problems']['current'])
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before[0],)).fetchone()==before


def test_claim_reloads_source_after_selected_row_becomes_stale(tmp_path,postgres_url):
    from oko.storage import source_groups
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            row=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation) VALUES ('fetch','p','f','pending',1) RETURNING *").fetchone()
        with transaction(store.db):
            store.db.execute("UPDATE jobs SET reason_message='offline',reason_code='source-network' WHERE id=%s",(row['id'],))
            source_groups.sync_jobs(store,[row['id']],1)
        claimed=store.jobs._claim(row)
        assert claimed is not None
        current=store.db.execute('SELECT source_event_owned,status FROM jobs WHERE id=%s',(row['id'],)).fetchone()
        assert tuple(current)==(True,'running')
        problem=store.db.execute("SELECT body::jsonb->>'state' FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'source_group_identity'='job:source-network:f:fetch'").fetchone()
        assert problem is not None and problem[0]=='retrying'
    finally:store.db.close()


def test_manual_feed_refresh_resolves_old_source_problem_and_keeps_wait(tmp_path,postgres_url):
    from oko.services.feed_refresh import FeedRefresh
    from oko.domain.job import Job
    from oko.storage.transaction import transaction
    from oko.collection.adapters import AdapterIssue, AdapterErrorKind
    from datetime import datetime, timezone, timedelta
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('p','other_web','p','f')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        deadline=datetime.fromtimestamp(store.wall_clock(),timezone.utc)+timedelta(hours=1)
        issue=AdapterIssue('source-network','Temporary limitation',AdapterErrorKind.source_wait,scope='feed:f',retry_at=deadline)
        retry_job(store, Job(job,'fetch','p',1),1,issue.message,issue=issue)
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:fetch' AND finished IS NULL").fetchone()
        assert old
        with transaction(store.db):
            FeedRefresh(store.media,store.subscriptions,store.jobs).refresh('f')
        row=store.db.execute('SELECT status,reason_code,reason_message,wait_scope,retry_at FROM jobs WHERE id=%s',(job,)).fetchone()
        assert row['status']=='pending' and row['reason_code'] is None and row['reason_message'] is None
        assert row['wait_scope']=='feed:f' and row['retry_at']>=deadline.timestamp()
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()[0] is not None
    finally:store.db.close()
