import asyncio
from fastapi.testclient import TestClient
from oko.collection.contracts import Material
from oko.collection.demo import DemoSource
from oko.services.collection import CollectionService
from oko.storage.transaction import transaction
from tests.support.app import create_app
from tests.support.reader import HEADERS,setup_reader
from tests.support.source import StructuredSource
from tests.processing.test_url_input_http_independent import GatedModel

def test_http_does_not_publish_late_summary_after_linked_title_roundtrip(tmp_path):
    model=GatedModel();app=create_app(tmp_path,source=DemoSource(),token='test',ai_transport=model)
    try:
        with TestClient(app,headers=HEADERS) as client:
            item=setup_reader(client,theses=True)
            def link():
                store=app.state.host.require_store()
                with transaction(store.db):
                    store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title,published_known)
                        SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,'Old title',0 FROM publications WHERE id=%s""",(item,))
                    store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
                    store.processing.reconcile_accepted_item(item)
            client.portal.call(link)
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://127.0.0.1:8080','model':'local:latest','local_execution':True})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True})
            assert model.started.wait(3)
            old_task=client.portal.call(lambda:[app.state.host.jobs._ai])[0]
            assert old_task is not None
            def change():
                store=app.state.host.require_store()
                service=CollectionService(lambda:store)
                old=store.publication('linked')
                assert old and old.teaser
                text=old.teaser.text
                for title in ('New title','Old title'):
                    with transaction(store.db):
                        material=StructuredSource(None).describe(store.publication('linked'),Material(title,text))
                        _,effects=service.collected('linked',material,manual=False)
                        store.jobs.apply_collection(effects)
            client.portal.call(change)
            model.release_old.set();assert model.old_done.wait(3)
            async def wait_old():await asyncio.shield(old_task)
            client.portal.call(wait_old)
            response=client.get('/api/v3/publication/'+item)
            assert response.status_code==200 and response.json()['theses']['result'] is None
    finally:model.release_old.set();model.release_new.set()
