import asyncio,threading
from fastapi.testclient import TestClient
import httpx
from oko.collection.adapters import ExternalKey,ExternalReference,Platform
from oko.collection.demo import DemoSource
from oko.storage.transaction import transaction
from tests.support.app import create_app
from tests.support.models import model_transport
from tests.support.reader import HEADERS,setup_reader

class GatedModel(httpx.AsyncBaseTransport):
    def __init__(self):
        self.started=threading.Event();self.old_done=threading.Event();self.release_old=threading.Event();self.release_new=threading.Event();self.count=0
        self.native=model_transport([])
    async def handle_async_request(self,request):
        if request.url.path!='/v1/chat/completions':return self.native.handler(request)
        self.count+=1;first=self.count==1;self.started.set()
        gate=self.release_old if first else self.release_new
        while not gate.is_set():await asyncio.sleep(.01)
        response=self.native.handler(request)
        if first:self.old_done.set()
        return response

def test_http_does_not_publish_late_summary_after_url_roundtrip(tmp_path):
    model=GatedModel()
    app=create_app(tmp_path,source=DemoSource(),token='test',ai_transport=model)
    try:
        with TestClient(app,headers=HEADERS) as client:
            item=setup_reader(client,theses=True)
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://127.0.0.1:8080','model':'local:latest','local_execution':True})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True})
            assert model.started.wait(3)
            old_task=client.portal.call(lambda:[app.state.host.jobs._ai])[0]
            assert old_task is not None
            def change():
                store=app.state.host.require_store()
                row=store.db.execute('SELECT platform,external_id,url FROM publications WHERE id=%s',(item,)).fetchone()
                key=ExternalKey(Platform(row['platform']),row['external_id'])
                for url in ('https://example.test/changed',row['url']):
                    with transaction(store.db):store.publications.identify(item,ExternalReference(key,url))
            client.portal.call(change)
            model.release_old.set();assert model.old_done.wait(3)
            async def wait_old():await asyncio.shield(old_task)
            client.portal.call(wait_old)
            response=client.get('/api/v3/publication/'+item)
            assert response.status_code==200 and response.json()['theses']['result'] is None
    finally:model.release_old.set();model.release_new.set()
