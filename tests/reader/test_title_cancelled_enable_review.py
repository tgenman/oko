import asyncio
from oko.subscriptions.subscriptions import Overrides
from oko.services.subscriptions import SubscriptionService
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.worker_library import worker_library,workers,pass_worker

def test_cancelled_theses_can_be_reenabled_without_signature_change(tmp_path,postgres_url):
    store,item,execution,tasks=worker_library(tmp_path,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters=''))
            store.settings_changed()
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        with transaction(store.db):
            row=store.db.execute("UPDATE jobs SET status='running',generation=generation+1 WHERE item=%s AND kind='theses' RETURNING id,generation",(item,)).fetchone()
            store.jobs.cancel(Job(row['id'],'theses',item,row['generation']))
        service=SubscriptionService(execution.require_store)
        with transaction(store.db):service.update_settings('oko','global',Overrides(theses=False,prompt_filters=''))
        with transaction(store.db):service.update_settings('oko','global',Overrides(theses=True,prompt_filters=''))
        status=store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]
        assert status in ('pending','done'),status
    finally:store.db.close()
