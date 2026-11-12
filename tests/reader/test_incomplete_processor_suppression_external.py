from tests.support.job_claim import retry_job
import json
import pytest
from tests.support.storage import Store
from tests.support.postgres_work import measure_reads
from oko.storage.transaction import transaction


def test_processor_failure_suppresses_then_restores_incomplete_atomically(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            content=json.dumps({'blocks':[{'type':'text','role':'body','text':'partial'}],'extra':{}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,content,status) VALUES ('p','rss','p',%s,'pending_collection')",(content,))
            store.db.execute("UPDATE publications SET feed_id='f' WHERE id='p'")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','done',1) RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO jobs(kind,item,status,generation,reason_message,reason_code) SELECT 'fetch','foreign-'||n,'failed',1,'foreign','processor-config' FROM generate_series(1,10000)n")
            store.incomplete_draft.mark(['p'])
        store.db._db.execute('ANALYZE jobs')
        old=store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):
            store.db.execute("UPDATE jobs SET status='pending' WHERE id=%s",(job,))
            claim=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                retry_job(store, claim,100,'unavailable',code='processor-config')
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is None
        with measure_reads(store.db) as work, transaction(store.db):retry_job(store, claim,100,'unavailable',code='processor-config')
        assert work.rows<2000,work
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'code'='processor-config' AND finished IS NULL").fetchone()
        with transaction(store.db):claim=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        with transaction(store.db):store.jobs.finish(claim)
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()[0]!=old
    finally:store.db.close()
