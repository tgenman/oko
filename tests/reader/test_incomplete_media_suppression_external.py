import json
import pytest
from tests.support.storage import Store
from tests.support.postgres_work import measure_reads
from oko.storage.transaction import transaction
from oko.storage.incomplete_group_draft import save_keys


def test_explicit_media_failure_suppresses_then_restores_incomplete_atomically(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            content=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,%s,0)",(content,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','file','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','done',1) RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) SELECT 'download_media','foreign-'||n,'failed',1,'foreign','media_unavailable' FROM generate_series(1,10000)n")
            save_keys(store.db,['p']);store.incomplete_draft.mark(['p'])
        store.db._db.execute('ANALYZE jobs')
        old=store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):
            store.db.execute("UPDATE jobs SET status='pending' WHERE id=%s",(job,))
            claim=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.jobs.retry(claim,100,'unavailable',code='media_unavailable')
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is None
        with measure_reads(store.db) as work, transaction(store.db):store.jobs.retry(claim,100,'unavailable',code='media_unavailable')
        assert work.rows<2000,work
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'code'='media_unavailable' AND finished IS NULL").fetchone()
        with transaction(store.db):claim=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        with transaction(store.db):store.jobs.finish(claim)
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()[0]!=old
    finally:store.db.close()
