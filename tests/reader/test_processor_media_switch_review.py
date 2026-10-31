import json,pytest
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


@pytest.mark.parametrize('first,second',[('processor-config','media-failed'),('media-failed','processor-config')])
def test_media_and_processor_switch_atomically(first,second,tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','rss','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','synthetic','file','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','m',1),1,'failure',code=first)
        def codes():return {json.loads(r['body'])['code'] for r in store.db.execute("SELECT body FROM reader_problems WHERE finished IS NULL AND (identity='processor:configuration' OR body::jsonb->>'operation'='producer:download_media')")}
        assert first in codes()
        with store.db:store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.jobs.retry(Job(job,'download_media','m',1),1,'failure',code=second)
                assert second in codes() and first not in codes()
                raise RuntimeError('rollback')
        assert first in codes() and second not in codes()
        store.jobs.retry(Job(job,'download_media','m',1),1,'failure',code=second)
        assert second in codes() and first not in codes()
    finally:store.db.close()
