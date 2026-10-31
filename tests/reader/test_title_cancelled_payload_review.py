import asyncio,json
from oko.storage.transaction import transaction
from oko.storage.job_types import Job
from oko.collection.contracts import Material
from oko.services.collection import CollectionService
from tests.support.source import StructuredSource
from tests.support.worker_library import worker_library,workers,pass_worker

def test_cancelled_title_job_with_legacy_payload_remains_cancelled(tmp_path,postgres_url):
    store,item,execution,tasks=worker_library(tmp_path,database_url=postgres_url)
    try:
        with transaction(store.db):
            from oko.subscriptions.subscriptions import Overrides
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters=''))
            store.settings_changed()
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title,published_known)
                SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,'Old title',0 FROM publications WHERE id=%s""",(item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
            store.processing.reconcile_accepted_item(item)
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        with transaction(store.db):
            row=store.db.execute("UPDATE jobs SET status='running',generation=generation+1,payload='{}' WHERE item=%s AND kind='theses' RETURNING id,generation",(item,)).fetchone()
            store.jobs.cancel(Job(row['id'],'theses',item,row['generation']))
            assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]=='cancelled'
            material=StructuredSource(None).describe(store.publication('linked'),Material('New title','Useful text'))
            _,effects=CollectionService(execution.require_store).collected('linked',material,manual=False)
            store.jobs.apply_collection(effects)
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]=='cancelled'
    finally:store.db.close()

def test_cancelled_legacy_job_with_changed_signature_can_be_requeued(tmp_path,postgres_url):
    from oko.subscriptions.subscriptions import Overrides
    from oko.services.subscriptions import SubscriptionService
    store,item,execution,tasks=worker_library(tmp_path,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters=''))
            store.settings_changed()
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        with transaction(store.db):
            row=store.db.execute("UPDATE jobs SET status='running',generation=generation+1,payload='{}' WHERE item=%s AND kind='theses' RETURNING id,generation",(item,)).fetchone()
            store.jobs.cancel(Job(row['id'],'theses',item,row['generation']))
        with transaction(store.db):SubscriptionService(execution.require_store).update_settings('oko','global',Overrides(prompt_theses='New relevant rule'))
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]=='pending'
    finally:store.db.close()

def test_cancelled_legacy_title_change_then_explicit_reenable_requeues(tmp_path,postgres_url):
    from oko.subscriptions.subscriptions import Overrides
    from oko.services.subscriptions import SubscriptionService
    store,item,execution,tasks=worker_library(tmp_path,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters=''))
            store.settings_changed()
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title,published_known)
                SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,'Old title',0 FROM publications WHERE id=%s""",(item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
            store.processing.reconcile_accepted_item(item)
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        with transaction(store.db):
            row=store.db.execute("UPDATE jobs SET status='running',generation=generation+1,payload='{}' WHERE item=%s AND kind='theses' RETURNING id,generation",(item,)).fetchone()
            store.jobs.cancel(Job(row['id'],'theses',item,row['generation']))
            material=StructuredSource(None).describe(store.publication('linked'),Material('New title','Useful text'))
            _,effects=CollectionService(execution.require_store).collected('linked',material,manual=False)
            store.jobs.apply_collection(effects)
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]=='cancelled'
        service=SubscriptionService(execution.require_store)
        with transaction(store.db):service.update_settings('oko','global',Overrides(theses=False,prompt_filters=''))
        with transaction(store.db):service.update_settings('oko','global',Overrides(theses=True,prompt_filters=''))
        status=store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]
        assert status=='pending',status
    finally:store.db.close()
