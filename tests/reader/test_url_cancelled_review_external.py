import asyncio,json
from oko.collection.adapters import ExternalKey,ExternalReference,Platform
from oko.storage.transaction import transaction
from tests.support.worker_library import worker_library,workers,pass_worker


def test_cancelled_stays_cancelled_and_explicit_retry_replaces_stale_summary(tmp_path,postgres_url):
    from oko.domain.job import Job
    store,item,execution,tasks=worker_library(tmp_path,database_url=postgres_url)
    try:
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        with transaction(store.db):
            row=store.db.execute("UPDATE jobs SET status='running',generation=generation+1 WHERE item=%s AND kind='theses' RETURNING id,generation",(item,)).fetchone()
            store.jobs.cancel(Job(row['id'],'theses',item,row['generation']))
            row=store.db.execute('SELECT platform,external_id,summarized_content FROM publications WHERE id=%s',(item,)).fetchone()
            saved=row['summarized_content']
            store.publications.identify(item,ExternalReference(ExternalKey(Platform(row['platform']),row['external_id']),'https://example.test/new'))
        assert store.db.execute("SELECT status FROM jobs WHERE item=%s AND kind='theses'",(item,)).fetchone()[0]=='cancelled'
        assert store.db.execute('SELECT summarized_content FROM publications WHERE id=%s',(item,)).fetchone()[0]==saved
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        assert store.processing.theses.read(item) is None
        with transaction(store.db):assert tasks.retry_theses(item)
        assert store.db.execute("SELECT status FROM jobs WHERE item=%s AND kind='theses'",(item,)).fetchone()[0]=='pending'
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        assert store.processing.theses.read(item) is not None
    finally:store.db.close()
