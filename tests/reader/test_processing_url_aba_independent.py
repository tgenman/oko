import asyncio
import httpx
from oko.collection.adapters import ExternalKey,ExternalReference,Platform
from oko.storage.transaction import transaction
from tests.support.filters import transport
from tests.support.worker_library import worker_library,workers,pass_worker

def test_url_round_trip_revokes_running_summary_attempt(tmp_path):
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        native=transport([],theses=True)
        async def model(request):
            if request.url.path=='/v1/chat/completions':entered.set();await release.wait()
            return native.handler(request)
        store,item,execution,tasks=worker_library(tmp_path)
        try:
            await pass_worker(workers(execution,tasks)[0],tasks)
            tasks._ai=None;tasks._ai_job=None
            worker=workers(execution,tasks,httpx.MockTransport(model))[1]
            await worker.tick();await asyncio.wait_for(entered.wait(),2)
            old=tasks._ai_job
            row=store.db.execute('SELECT platform,external_id,url FROM publications WHERE id=%s',(item,)).fetchone()
            key=ExternalKey(Platform(row['platform']),row['external_id'])
            for url in ('https://example.test/changed',row['url']):
                with transaction(store.db):
                    store.publications.identify(item,ExternalReference(key,url))
                    store.processing.reconcile_accepted_item(item)
            assert old is not None and not store.jobs.valid(old)
        finally:
            release.set()
            if tasks._ai:await tasks._ai
            store.db.close()
    asyncio.run(scenario())

def test_url_change_rejects_result_referencing_old_url(tmp_path):
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        native=transport([],theses=True)
        async def model(request):
            if request.url.path=='/v1/chat/completions':entered.set();await release.wait()
            return native.handler(request)
        store,item,execution,tasks=worker_library(tmp_path)
        try:
            await pass_worker(workers(execution,tasks)[0],tasks)
            tasks._ai=None;tasks._ai_job=None
            await workers(execution,tasks,httpx.MockTransport(model))[1].tick()
            await asyncio.wait_for(entered.wait(),2)
            row=store.db.execute('SELECT platform,external_id FROM publications WHERE id=%s',(item,)).fetchone()
            with transaction(store.db):
                store.publications.identify(item,ExternalReference(ExternalKey(Platform(row['platform']),row['external_id']),'https://example.test/changed'))
                store.processing.reconcile_accepted_item(item)
            release.set();await tasks._ai
            row=store.db.execute('SELECT summarized_content FROM publications WHERE id=%s',(item,)).fetchone()
            import json
            assert json.loads(row['summarized_content'])=={},row['summarized_content']
        finally:
            release.set()
            if tasks._ai:await tasks._ai
            store.db.close()
    asyncio.run(scenario())


def test_url_invalidation_survives_restart_with_saved_results_intact(tmp_path):
    import json
    from tests.support.storage import Store
    store,item,execution,tasks=worker_library(tmp_path)
    try:
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        row=store.db.execute('SELECT platform,external_id,filtered_content,summarized_content FROM publications WHERE id=%s',(item,)).fetchone()
        before=tuple(row)[2:]
        with transaction(store.db):store.publications.identify(item,ExternalReference(ExternalKey(Platform(row['platform']),row['external_id']),'https://example.test/new'))
        store.db.close()
        store=Store(tmp_path,lambda _:None)
        row=store.db.execute('SELECT filtered_content,summarized_content FROM publications WHERE id=%s',(item,)).fetchone()
        assert tuple(row)==before
        assert store.filters.read(item)['status']=='pending'
        assert store.processing.theses.read(item) is None
        assert json.loads(row['summarized_content'])
    finally:store.db.close()
