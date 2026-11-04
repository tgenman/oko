from tests.support.collection_acceptance import apply_simulated_collection, running_collection_job
from tests.support.worker_library import worker_library
from oko.workers.processing_service import processing_service
from oko.storage import publication_links
from oko.storage.transaction import transaction


def test_linked_title_change_rollback_restores_running_parent(tmp_path):
    import pytest
    from oko.workers.collection import collection_service
    from tests.support.synthetic_material import material as Material
    from tests.support.source import StructuredSource
    store,item,execution,tasks=worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute("""INSERT INTO publications(id,url,platform,external_id,complete,content,title,discovered_at)
                SELECT 'linked','https://example.org/linked','other_web','linked',1,content,'Old title',discovered_at FROM publications WHERE id=%s""",(item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
            store.processing.reconcile_accepted_item(item)
            job=store.jobs.claim_processing('filter',store.clock())
            prepared=processing_service(execution.require_store).prepare(item,'filter')
            assert job and prepared.input;store.jobs.save_input(job,prepared.input)
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                material=StructuredSource(None).describe(store.publication('linked'),Material('New title','Useful text'))
                job_for_collection=running_collection_job(store,'fetch','linked')
                _,effects=collection_service(execution.require_store).collected('linked',material,manual=False)
                apply_simulated_collection(store,effects,job=job_for_collection)
                assert not store.jobs.valid(job)
                raise RuntimeError('rollback')
        assert store.jobs.valid(job)
        assert store.db.execute("SELECT title FROM publications WHERE id='linked'").fetchone()[0]=='Old title'
    finally:store.db.close()

def test_linked_title_roundtrip_rejects_real_late_model_reply(tmp_path):
    import asyncio,httpx
    from oko.workers.collection import collection_service
    from tests.support.synthetic_material import material as Material
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
                store.db.execute("""INSERT INTO publications(id,url,platform,external_id,complete,content,title,discovered_at)
                    SELECT 'linked','https://example.org/linked','other_web','linked',1,content,'Old title',discovered_at FROM publications WHERE id=%s""",(item,))
                store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
                store.processing.reconcile_accepted_item(item)
            worker=workers(execution,tasks,httpx.MockTransport(model))[0]
            await worker.tick();await asyncio.wait_for(entered.wait(),2)
            old_job=tasks._ai_job
            for title in ('New title','Old title'):
                with transaction(store.db):
                    material=StructuredSource(None).describe(store.publication('linked'),Material(title,'Useful text'))
                    job_for_collection=running_collection_job(store,'fetch','linked')
                    _,effects=collection_service(execution.require_store).collected('linked',material,manual=False)
                    apply_simulated_collection(store,effects,job=job_for_collection)
            assert old_job and not store.jobs.valid(old_job)
            release.set();await tasks._ai
            assert saved(store,item)['filtered_content']=={}
        finally:
            release.set()
            if tasks._ai:await tasks._ai
            store.db.close()
    asyncio.run(scenario())
