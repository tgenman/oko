import json
from tests.support.storage import Store
from oko.storage.job_types import Job


def test_unexpected_media_retry_saves_problem_without_status_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','img','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','m',1),1,'failure',code='media-failed')
        record=store.db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE body::jsonb->>'code'='media-failed' AND finished IS NULL").fetchone()
        assert record is not None
        before=dict(record)
        store.problems.snapshot(2)
        after=dict(store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(record['id'],)).fetchone())
        assert after==before,(json.loads(before['body']),json.loads(after['body']))
    finally:store.db.close()

def test_explicit_work_download_media_is_managed_by_event_owner(tmp_path,postgres_url):
    from oko.storage.media_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','img','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure','work-download_media') RETURNING id").fetchone()[0]
        candidates=managed_candidates(store,job,1)
        assert len(candidates)==1 and candidates[0]['code']=='work-download_media' and candidates[0]['media_implicit_code'] is False
    finally:store.db.close()


def test_unexpected_media_retry_rolls_back_job_and_problem_together(tmp_path,postgres_url):
    import pytest
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','img','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.jobs.retry(Job(job,'download_media','m',1),1,'failure')
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'code'='download_media' AND finished IS NULL").fetchone()
                raise ValueError('rollback')
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='running'
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'code'='download_media' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_work_fetch_error_code_is_not_silently_lost_from_media(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','img','pending')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure','work-fetch')")
        store.problems.recover_media_problems()
        assert any(p['code']=='work-fetch' for p in store.problems.snapshot(2)['current'])
    finally:store.db.close()


def test_unexpected_media_retry_without_explicit_code_is_visible(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','img','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','m',1),1,'failure')
        assert any(p['code']=='download_media' for p in store.problems.snapshot(2)['current'])
    finally:store.db.close()
