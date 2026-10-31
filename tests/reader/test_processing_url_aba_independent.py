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

def test_url_change_invalidates_completed_jobs_without_erasing_result(tmp_path):
    import json
    store,item,execution,tasks=worker_library(tmp_path)
    try:
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        old=store.db.execute('SELECT filtered_content,summarized_content,platform,external_id FROM publications WHERE id=%s',(item,)).fetchone()
        assert json.loads(old['filtered_content']) and json.loads(old['summarized_content'])
        with transaction(store.db):
            store.publications.identify(item,ExternalReference(ExternalKey(Platform(old['platform']),old['external_id']),'https://example.test/changed'))
            store.processing.reconcile_accepted_item(item)
        current=store.db.execute('SELECT filtered_content,summarized_content FROM publications WHERE id=%s',(item,)).fetchone()
        assert tuple(current)==tuple(old[:2])
        statuses={r['kind']:r['status'] for r in store.db.execute("SELECT kind,status FROM jobs WHERE item=%s AND kind IN ('filter','theses')",(item,))}
        assert statuses['filter']=='pending' and statuses['theses']!='done',statuses
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        assert store.db.execute("SELECT status FROM jobs WHERE item=%s AND kind='theses'",(item,)).fetchone()[0]=='pending'
    finally:store.db.close()

def test_linked_url_change_revokes_parent_and_rollback_restores_attempt(tmp_path):
    from oko.services.processing import ProcessingService
    store,item,execution,tasks=worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title)
                SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,title FROM publications WHERE id=%s""",(item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
            store.processing.reconcile_accepted_item(item)
            job=store.jobs.claim_processing('filter',store.clock())
            prepared=ProcessingService(execution.require_store).prepare(item,'filter')
            assert job and prepared.input and any(source.id=='linked' for source in prepared.input.request.sources)
            store.jobs.save_input(job,prepared.input)
        ref=ExternalReference(ExternalKey(Platform.other_web,'linked'),'https://example.test/new-child')
        import pytest
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.publications.identify('linked',ref)
                assert not store.jobs.valid(job)
                raise RuntimeError('rollback')
        assert store.jobs.valid(job)
        with transaction(store.db):store.publications.identify('linked',ref)
        assert not store.jobs.valid(job)
    finally:store.db.close()

def test_late_model_failure_after_url_change_does_not_attach_to_new_attempt(tmp_path):
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        native=transport([],theses=True)
        async def model(request):
            if request.url.path=='/v1/chat/completions':
                entered.set();await release.wait();return httpx.Response(500,text='synthetic old failure')
            return native.handler(request)
        store,item,execution,tasks=worker_library(tmp_path)
        try:
            await workers(execution,tasks,httpx.MockTransport(model))[0].tick()
            await asyncio.wait_for(entered.wait(),2)
            row=store.db.execute('SELECT platform,external_id FROM publications WHERE id=%s',(item,)).fetchone()
            with transaction(store.db):store.publications.identify(item,ExternalReference(ExternalKey(Platform(row['platform']),row['external_id']),'https://example.test/changed'))
            release.set();await tasks._ai
            row=store.db.execute("SELECT status,error,problem_code FROM jobs WHERE item=%s AND kind='filter'",(item,)).fetchone()
            assert row['status']=='pending' and row['error'] is None and row['problem_code'] is None
        finally:
            release.set()
            if tasks._ai:await tasks._ai
            store.db.close()
    asyncio.run(scenario())

def test_same_url_and_rolled_back_url_change_preserve_result_and_jobs(tmp_path):
    store,item,execution,tasks=worker_library(tmp_path)
    try:
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        row=store.db.execute('SELECT platform,external_id,url,processing_metadata FROM publications WHERE id=%s',(item,)).fetchone()
        jobs=[tuple(r) for r in store.db.execute("SELECT id,status,generation,input_snapshot FROM jobs WHERE item=%s AND kind IN ('filter','theses') ORDER BY kind",(item,))]
        key=ExternalKey(Platform(row['platform']),row['external_id'])
        with transaction(store.db):store.publications.identify(item,ExternalReference(key,row['url']))
        assert [tuple(r) for r in store.db.execute("SELECT id,status,generation,input_snapshot FROM jobs WHERE item=%s AND kind IN ('filter','theses') ORDER BY kind",(item,))]==jobs
        import pytest
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.publications.identify(item,ExternalReference(key,'https://example.test/new'))
                raise RuntimeError('rollback')
        saved=store.db.execute('SELECT url,processing_metadata FROM publications WHERE id=%s',(item,)).fetchone()
        assert tuple(saved)==(row['url'],row['processing_metadata'])
        assert [tuple(r) for r in store.db.execute("SELECT id,status,generation,input_snapshot FROM jobs WHERE item=%s AND kind IN ('filter','theses') ORDER BY kind",(item,))]==jobs
    finally:store.db.close()

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

def test_unreadable_source_snapshot_revokes_linked_running_attempt(tmp_path):
    from oko.services.processing import ProcessingService
    store,item,execution,tasks=worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title)
                SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,title FROM publications WHERE id=%s""",(item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
            store.processing.reconcile_accepted_item(item)
            job=store.jobs.claim_processing('filter',store.clock())
            prepared=ProcessingService(execution.require_store).prepare(item,'filter')
            assert job and prepared.input
            store.jobs.save_input(job,prepared.input)
            store.db.execute("UPDATE jobs SET input_snapshot=jsonb_set(input_snapshot,'{sources}','[{\"broken\":true}]'::jsonb) WHERE id=%s",(job.id,))
        with transaction(store.db):store.publications.identify('linked',ExternalReference(ExternalKey(Platform.other_web,'linked'),'https://example.test/new-child'))
        assert not store.jobs.valid(job)
    finally:store.db.close()
