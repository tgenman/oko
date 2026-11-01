import pytest
from tests.support.storage import Store


@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses','delete','feed-delete'])
def test_job_events_create_and_finish_source_occurrence_without_get(kind,tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            if kind=='download_media':store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','file','unavailable')")
            item='m' if kind=='download_media' else 'f' if kind=='feed-delete' else 'p'
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,%s,'running',1) RETURNING id",(kind,item)).fetchone()[0]
        store.jobs.retry(Job(job,kind,item,1),1,'failure',code='source-network')
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'=%s AND finished IS NULL",('job:source-network:f:'+kind,)).fetchone()
        assert row
        with transaction(store.db):store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
        with transaction(store.db):store.jobs.finish(Job(job,kind,item,1))
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0] is not None
    finally:store.db.close()


def test_source_failure_and_group_rollback_together(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.jobs.retry(Job(job,'fetch','p',1),1,'failure',code='source-network')
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity' IS NOT NULL AND finished IS NULL").fetchone()
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='running'
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity' IS NOT NULL AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_removing_one_of_two_source_roots_finishes_only_its_group(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected',ARRAY[]::text[]),('b','b','B','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('ra','rss','ra'),('rb','rss','rb'),('p','rss','p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','ra',true),('b','rb',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('ra','p'),('rb','p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'fetch','p',1),1,'failure',code='source-network')
        a=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:a:fetch' AND finished IS NULL").fetchone()[0]
        b=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:b:fetch' AND finished IS NULL").fetchone()[0]
        store.remove_feed('a')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(a,)).fetchone()[0] is not None
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(b,)).fetchone()[0] is None
    finally:store.db.close()


def test_http_source_group_is_visible_and_get_does_not_write(tmp_path,postgres_url):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id) VALUES ('p')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','failure','source-network')")
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
            store.db.execute("UPDATE jobs SET error='offline',problem_code='source-network' WHERE id=%s",(row['id'],))
            source_groups.sync_jobs(store,[row['id']],1)
        claimed=store.jobs._claim(row)
        assert claimed is not None
        current=store.db.execute('SELECT source_event_owned,status FROM jobs WHERE id=%s',(row['id'],)).fetchone()
        assert tuple(current)==(True,'running')
        problem=store.db.execute("SELECT body::jsonb->>'state' FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'source_group_identity'='job:source-network:f:fetch'").fetchone()
        assert problem is not None and problem[0]=='retrying'
    finally:store.db.close()
