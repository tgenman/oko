"""A late model response cannot become current after A → B → A."""
import asyncio
from fastapi.testclient import TestClient
from oko.storage.transaction import transaction
from oko.workers.collection import collection_service
from tests.processing.test_url_input_http_independent import GatedModel
from tests.support.app import create_app
from tests.support.collection_acceptance import apply_simulated_collection,running_collection_job
from tests.support.reader import HEADERS,setup_reader
from tests.support.source import StructuredSource
from tests.support.storage import legacy_publication
from tests.support.synthetic_material import material as synthetic_material
from tests.support.synthetic_source import SyntheticSource


def test_http_rejects_late_summary_after_text_roundtrip(tmp_path):
    model=GatedModel()
    app=create_app(tmp_path,source=SyntheticSource(),token='test',ai_transport=model)
    try:
        with TestClient(app,headers=HEADERS) as client:
            item=setup_reader(client,theses=True)
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://127.0.0.1:8080','model':'local:latest','local_execution':True})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True})
            assert model.started.wait(10)
            old_task=client.portal.call(lambda:[app.state.host.jobs._ai])[0]
            active_job=client.portal.call(lambda:app.state.host.jobs._ai_job)
            assert old_task is not None and active_job is not None
            item=active_job.item
            def change():
                store=app.state.host.require_store()
                header,original=store.original(item)
                for text in ('Changed input',original):
                    with transaction(store.db):
                        material=StructuredSource(None).describe(legacy_publication(store,item),synthetic_material(header['title'],text))
                        job=running_collection_job(store,'fetch',item)
                        _,effects=collection_service(lambda:store).collected_content(item,material.adapter_content)
                        apply_simulated_collection(store,effects,job=job)
            try:
                client.portal.call(change)
                model.release_old.set()
                assert model.old_done.wait(10)
                async def wait_old():await asyncio.shield(old_task)
                client.portal.call(wait_old)
                assert client.get('/api/v3/publication/'+item).json()['theses']['result'] is None
            finally:
                model.release_old.set()
                model.release_new.set()
    finally:
        model.release_old.set()
        model.release_new.set()
