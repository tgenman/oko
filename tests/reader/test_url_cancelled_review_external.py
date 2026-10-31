import asyncio,json
from oko.collection.adapters import ExternalKey,ExternalReference,Platform
from oko.storage.transaction import transaction
from tests.support.worker_library import worker_library,workers,pass_worker

def test_url_change_invalidates_retained_result_even_when_job_cancelled(tmp_path,postgres_url):
    store,item,execution,tasks=worker_library(tmp_path,database_url=postgres_url)
    try:
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        with transaction(store.db):
            from oko.storage.job_types import Job
            row=store.db.execute("UPDATE jobs SET status='running',generation=generation+1 WHERE item=%s AND kind='theses' RETURNING id,generation",(item,)).fetchone()
            store.jobs.cancel(Job(row['id'],'theses',item,row['generation']))
            assert store.db.execute("SELECT status FROM jobs WHERE id=%s",(row['id'],)).fetchone()[0]=='cancelled'
            row=store.db.execute('SELECT platform,external_id,processing_metadata FROM publications WHERE id=%s',(item,)).fetchone()
            assert json.loads(row['processing_metadata'])['summary']
            store.publications.identify(item,ExternalReference(ExternalKey(Platform(row['platform']),row['external_id']),'https://example.test/new'))
        metadata=json.loads(store.db.execute('SELECT processing_metadata FROM publications WHERE id=%s',(item,)).fetchone()[0])
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        assert store.processing.theses.read(item) is None
        assert metadata['summary'].get('source_url_stale') is True
    finally:store.db.close()

def test_cancelled_stays_cancelled_and_explicit_retry_replaces_stale_summary(tmp_path,postgres_url):
    from oko.storage.job_types import Job
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

def test_cancelled_stale_summary_after_disable_enable_is_requeued(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.services.subscriptions import SubscriptionService
    from oko.subscriptions.subscriptions import Overrides
    store,item,execution,tasks=worker_library(tmp_path,database_url=postgres_url)
    try:
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        with transaction(store.db):
            row=store.db.execute("UPDATE jobs SET status='running',generation=generation+1 WHERE item=%s AND kind='theses' RETURNING id,generation",(item,)).fetchone()
            store.jobs.cancel(Job(row['id'],'theses',item,row['generation']))
            row=store.db.execute('SELECT platform,external_id FROM publications WHERE id=%s',(item,)).fetchone()
            store.publications.identify(item,ExternalReference(ExternalKey(Platform(row['platform']),row['external_id']),'https://example.test/new'))
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        service=SubscriptionService(execution.require_store)
        with transaction(store.db):service.update_settings('oko','global',Overrides(theses=False))
        with transaction(store.db):service.update_settings('oko','global',Overrides(theses=True))
        status=store.db.execute("SELECT status FROM jobs WHERE item=%s AND kind='theses'",(item,)).fetchone()[0]
        assert status=='pending',status
    finally:store.db.close()
