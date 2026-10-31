from tests.support.storage import Store
from tests.support.worker_library import worker_library
from oko.services.processing import ProcessingService
from oko.storage import publication_links
from oko.storage.transaction import transaction

def test_linked_source_title_changes_revoke_running_parent(tmp_path):
    store,item,execution,tasks=worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title)
                SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,'Old title' FROM publications WHERE id=%s""",(item,))
            store.db.execute("UPDATE publications SET published_known=0 WHERE id='linked'")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
            store.processing.reconcile_accepted_item(item)
            job=store.jobs.claim_processing('filter',store.clock())
            prepared=ProcessingService(execution.require_store).prepare(item,'filter')
            assert job and prepared.input and any(s.id=='linked' and s.title=='Old title' for s in prepared.input.request.sources)
            store.jobs.save_input(job,prepared.input)
        from oko.services.collection import CollectionService
        from oko.collection.contracts import Material
        from tests.support.source import StructuredSource
        with transaction(store.db):
            source=StructuredSource(None)
            material=source.describe(store.publication('linked'),Material('New title','Useful text'))
            _,effects=CollectionService(execution.require_store).collected('linked',material,manual=False)
            store.jobs.apply_collection(effects)
        assert store.db.execute("SELECT title FROM publications WHERE id='linked'").fetchone()[0]=='New title'
        assert not store.jobs.valid(job)
    finally:store.db.close()

def test_linked_title_change_retains_results_and_requeues_parent(tmp_path):
    import asyncio
    from oko.services.collection import CollectionService
    from oko.collection.contracts import Material
    from tests.support.source import StructuredSource
    from tests.support.worker_library import workers,pass_worker
    store,item,execution,tasks=worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title,published_known)
                SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,'Old title',0 FROM publications WHERE id=%s""",(item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
            store.processing.reconcile_accepted_item(item)
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        old=store.db.execute('SELECT filtered_content,summarized_content FROM publications WHERE id=%s',(item,)).fetchone()
        assert old and old[0]!='{}' and old[1]!='{}'
        with transaction(store.db):
            material=StructuredSource(None).describe(store.publication('linked'),Material('New title','Useful text'))
            _,effects=CollectionService(execution.require_store).collected('linked',material,manual=False)
            store.jobs.apply_collection(effects)
        current=store.db.execute('SELECT filtered_content,summarized_content FROM publications WHERE id=%s',(item,)).fetchone()
        assert tuple(current)==tuple(old)
        statuses={r['kind']:r['status'] for r in store.db.execute("SELECT kind,status FROM jobs WHERE item=%s AND kind IN ('filter','theses')",(item,))}
        assert statuses['filter']=='pending' and statuses['theses']!='done'
    finally:store.db.close()


def test_linked_title_change_rollback_restores_running_parent(tmp_path):
    import pytest
    from oko.services.collection import CollectionService
    from oko.collection.contracts import Material
    from tests.support.source import StructuredSource
    store,item,execution,tasks=worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title,published_known)
                SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,'Old title',0 FROM publications WHERE id=%s""",(item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
            store.processing.reconcile_accepted_item(item)
            job=store.jobs.claim_processing('filter',store.clock())
            prepared=ProcessingService(execution.require_store).prepare(item,'filter')
            assert job and prepared.input;store.jobs.save_input(job,prepared.input)
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                material=StructuredSource(None).describe(store.publication('linked'),Material('New title','Useful text'))
                _,effects=CollectionService(execution.require_store).collected('linked',material,manual=False)
                store.jobs.apply_collection(effects)
                assert not store.jobs.valid(job)
                raise RuntimeError('rollback')
        assert store.jobs.valid(job)
        assert store.db.execute("SELECT title FROM publications WHERE id='linked'").fetchone()[0]=='Old title'
    finally:store.db.close()

def test_linked_title_roundtrip_rejects_real_late_model_reply(tmp_path):
    import asyncio,httpx
    from oko.services.collection import CollectionService
    from oko.collection.contracts import Material
    from tests.support.filters import transport
    from tests.support.source import StructuredSource
    from tests.support.worker_library import workers,saved
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        native=transport([],theses=True)
        async def model(request):
            if request.url.path=='/v1/chat/completions':entered.set();await release.wait()
            return native.handler(request)
        store,item,execution,tasks=worker_library(tmp_path)
        try:
            with transaction(store.db):
                store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title,published_known)
                    SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,'Old title',0 FROM publications WHERE id=%s""",(item,))
                store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
                store.processing.reconcile_accepted_item(item)
            worker=workers(execution,tasks,httpx.MockTransport(model))[0]
            await worker.tick();await asyncio.wait_for(entered.wait(),2)
            old_job=tasks._ai_job
            for title in ('New title','Old title'):
                with transaction(store.db):
                    material=StructuredSource(None).describe(store.publication('linked'),Material(title,'Useful text'))
                    _,effects=CollectionService(execution.require_store).collected('linked',material,manual=False)
                    store.jobs.apply_collection(effects)
            assert old_job and not store.jobs.valid(old_job)
            release.set();await tasks._ai
            assert saved(store,item)['filtered_content']=={}
        finally:
            release.set()
            if tasks._ai:await tasks._ai
            store.db.close()
    asyncio.run(scenario())

def test_linked_title_marks_cancelled_saved_summary_stale_without_restart(tmp_path):
    import asyncio,json
    from oko.services.collection import CollectionService
    from oko.collection.contracts import Material
    from oko.storage.job_types import Job
    from tests.support.source import StructuredSource
    from tests.support.worker_library import workers,pass_worker
    store,item,execution,tasks=worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title,published_known)
                SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,'Old title',0 FROM publications WHERE id=%s""",(item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
            store.processing.reconcile_accepted_item(item)
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks));asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        with transaction(store.db):
            row=store.db.execute("UPDATE jobs SET status='running',generation=generation+1 WHERE item=%s AND kind='theses' RETURNING id,generation",(item,)).fetchone()
            store.jobs.cancel(Job(row['id'],'theses',item,row['generation']))
            material=StructuredSource(None).describe(store.publication('linked'),Material('New title','Useful text'))
            _,effects=CollectionService(execution.require_store).collected('linked',material,manual=False)
            store.jobs.apply_collection(effects)
        row=store.db.execute('SELECT processing_metadata,summarized_content FROM publications WHERE id=%s',(item,)).fetchone()
        assert json.loads(row['processing_metadata'])['summary']['source_title_stale'] is True
        assert json.loads(row['summarized_content'])
        assert store.db.execute("SELECT status FROM jobs WHERE item=%s AND kind='theses'",(item,)).fetchone()[0]=='cancelled'
    finally:store.db.close()
