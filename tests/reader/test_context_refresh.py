"""Manual refresh through the reader HTTP boundary and synthetic sources."""
import httpx
import pytest
from tests.support.app import create_app
from tests.support.v3 import TestClient
from tests.support.reader import wait_for

HEADERS={'Authorization':'Bearer test'}


def test_folder_refresh_only_collects_its_feeds_and_records_the_target(tmp_path):
    calls=[]
    def reply(request):
        calls.append(request.url.host)
        return httpx.Response(200,text='<rss><channel><title>Fixture</title></channel></rss>')
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(reply)),headers=HEADERS) as client:
        for host,folder in [('one.test','Science'),('two.test','Science'),('three.test','Other')]:
            checked=client.post('/api/v3/feeds/verify',json={'url':'https://'+host+'/feed'}).json()
            result=client.post('/api/v3/feeds',json={'url':checked['candidate']['url'],'verification':checked['verification'],'title':host,'folder':folder})
            assert result.status_code==200,result.text
        wait_for(client,lambda:all(f['history']['state']=='complete' for f in client.get('/api/v3/subscriptions').json()['feeds']))
        folder=next(f['id'] for f in client.get('/api/v3/subscriptions').json()['folders'] if f['title']=='Science')
        calls.clear()
        response=client.post('/api/v3/collect',json={'scope':'folder','folder_id':folder},headers={'X-Oko-Action':'refresh-folder'})
        assert response.status_code==200,response.text
        wait_for(client,lambda:len(calls)>=2)
        assert set(calls)=={'one.test','two.test'}
        assert client.get('/api/v3/commands/refresh-folder').json()['context']=={'operation':'collect','target':{'scope':'folder','folder_id':folder}}
        assert client.post('/api/v3/collect',json={'scope':'folder','folder_id':'missing'}).status_code==404


def test_refresh_retries_incomplete_and_unavailable_once_preserving_saved_copy(tmp_path):
    import time
    from collections import Counter
    from oko.collection.demo import DemoSource
    from oko.collection.contracts import DiscoveryResult, Publication, FetchResult, Material, SourceDeleted, SourceError
    class Source(DemoSource):
        def __init__(self):
            self.calls=Counter()
            self.cursors=[]
        async def discover(self,cursor):
            self.cursors.append(cursor)
            return DiscoveryResult(tuple(Publication(str(i),'https://fixture.test/'+str(i),1800000000) for i in range(1,3)) if cursor is None else (), '2')
        async def fetch(self,publication):
            self.calls[publication.id]+=1
            if publication.id=='2' and self.calls['2']>2:
                raise SourceError('Still unavailable',delay=.03)
            if publication.id=='2' and self.calls['2']>1:
                raise SourceDeleted()
            return FetchResult(Material('Item '+publication.id,'Saved copy',full=False,reason='Incomplete source'))
    source=Source()
    with TestClient(create_app(tmp_path,token='test',api_only=True,source=source),headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        cards=wait_for(client,lambda:(cards if len(cards:=client.get('/api/v3/cards').json())==2 and all(c['availability']=='available' for c in cards) else None))
        copies={c['id']:client.get('/api/v3/materials/'+c['id']).json()['content']['text'] for c in cards}
        assert client.post('/api/v3/collect',json={'scope':'feed','feed_id':'demo'}).status_code==200
        wait_for(client,lambda:source.calls['1']==2 and source.calls['2']==2)
        time.sleep(.12)
        assert source.calls=={'1':2,'2':2}
        assert source.cursors==[None,'2']
        for item,text in copies.items():
            assert client.get('/api/v3/materials/'+item).json()['content']['text']==text
        assert client.post('/api/v3/collect',json={'scope':'feed','feed_id':'demo'}).status_code==200
        wait_for(client,lambda:source.calls['2']==3)
        time.sleep(.12)
        assert source.calls['2']==3


def test_refresh_retries_failed_theses_but_keeps_ready_results(tmp_path):
    import json
    from oko.collection.demo import DemoSource
    from oko.processing.contracts import ProcessingError
    from tests.support.codex import CodexService, configure_codex
    class Model(CodexService):
        failed=True
        calls=[]
        async def complete(self,prompt,schema,directory,choice):
            item=json.loads(prompt.split('\n',1)[1])['sources'][0]['id']
            self.calls.append(item)
            if self.failed and len(set(self.calls))==1:
                raise ProcessingError('Temporary model failure')
            return await super().complete(prompt,schema,directory,choice)
    model=Model()
    with TestClient(create_app(tmp_path,token='test',api_only=True,source=DemoSource(),codex_client=model),headers=HEADERS) as client:
        configure_codex(client)
        assert client.patch('/api/v3/feeds/demo',json={'public_ai':True}).status_code==200
        client.patch('/api/v3/settings/oko/global',json={'theses':True,'prompt_filters':''})
        cards=wait_for(client,lambda:(c if len(c:=client.get('/api/v3/cards').json())==3 else None))
        wait_for(client,lambda:len(set(model.calls))==3)
        wait_for(client,lambda:sum(client.get('/api/v3/materials/'+c['id']).json()['theses']['attempt']['state']=='ready' for c in cards)==2)
        old=list(model.calls)
        model.failed=False
        assert client.post('/api/v3/collect',json={'scope':'feed','feed_id':'demo'}).status_code==200
        wait_for(client,lambda:all(client.get('/api/v3/materials/'+c['id']).json()['theses']['attempt']['state']=='ready' for c in cards))
        assert len(model.calls)==len(old)+1
        assert model.calls[-1]==old[0]




def test_refresh_does_not_cancel_or_duplicate_running_collection(tmp_path):
    import asyncio
    import threading
    from oko.collection.demo import DemoSource
    class Held(DemoSource):
        started=threading.Event()
        release=threading.Event()
        cancelled=False
        calls=0
        async def discover(self,cursor):
            self.calls+=1
            self.started.set()
            try:
                while not self.release.is_set():
                    await asyncio.sleep(.01)
            except asyncio.CancelledError:
                self.cancelled=True
                raise
            return await super().discover(cursor)
    source=Held()
    with TestClient(create_app(tmp_path,token='test',api_only=True,source=source),headers=HEADERS) as client:
        assert source.started.wait(2)
        try:
            for _ in range(3):
                assert client.post('/api/v3/collect',json={'scope':'feed','feed_id':'demo'}).status_code==200
            assert source.calls==1 and not source.cancelled
        finally:
            source.release.set()
        wait_for(client,lambda:len(client.get('/api/v3/cards').json())==3)


def test_refresh_restores_previously_unavailable_publication(tmp_path):
    from oko.collection.demo import DemoSource
    from oko.collection.contracts import SourceDeleted
    class Source(DemoSource):
        unavailable=False
        async def fetch(self,publication):
            if self.unavailable:
                raise SourceDeleted()
            return await super().fetch(publication)
    source=Source()
    with TestClient(create_app(tmp_path,token='test',api_only=True,source=source),headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        cards=wait_for(client,lambda:(c if len(c:=client.get('/api/v3/cards').json())==3 and all(x['availability']=='available' for x in c) else None))
        item=cards[0]['id']
        source.unavailable=True
        assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
        wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['content']['source_deleted'])
        source.unavailable=False
        assert client.post('/api/v3/collect',json={'scope':'feed','feed_id':'demo'}).status_code==200
        wait_for(client,lambda:not client.get('/api/v3/materials/'+item).json()['content']['source_deleted'])


def test_refresh_keeps_forced_source_wait_but_advances_ordinary_retries(tmp_path):
    from oko.collection.demo import DemoSource
    from oko.collection.contracts import DiscoveryResult, Publication, SourceError
    from tests.support.reader import Clock
    from collections import Counter
    class Source(DemoSource):
        calls=Counter()
        async def discover(self,cursor):
            return DiscoveryResult(tuple(Publication(str(i),'https://fixture.test/'+str(i),1800000000) for i in (1,2)) if cursor is None else (), '2')
        async def fetch(self,p):
            self.calls[p.id]+=1
            if self.calls[p.id]==1:
                error=SourceError('Wait for source' if p.id=='1' else 'Network error',delay=30,code='source-unavailable')
                if p.id=='1':
                    from oko.collection.adapters import AdapterIssue,AdapterErrorKind
                    from datetime import datetime,timezone,timedelta
                    error.adapter_issue=AdapterIssue('source-wait',error.label,AdapterErrorKind.source_wait,
                        scope='fixture:source',retry_at=datetime.now(timezone.utc)+timedelta(seconds=30))
                raise error
            return await super().fetch(p)
    clock,source=Clock(),Source()
    with TestClient(create_app(tmp_path,token='test',api_only=True,source=source,clock=clock),headers=HEADERS) as client:
        wait_for(client,lambda:source.calls=={'1':1,'2':1})
        wait_for(client,lambda:any(p['message']=='Network error' for p in client.get('/api/v3/status').json()['problems']['current']))
        assert client.post('/api/v3/collect',json={'scope':'feed','feed_id':'demo'}).status_code==200
        wait_for(client,lambda:source.calls['2']==2)
        assert source.calls['1']==1
        status=client.get('/api/v3/status').json()
        assert any(w['phase']=='waiting' and w['retry_at'] for w in status['work'])
        clock.now+=61
        wait_for(client,lambda:source.calls['1']==2)


def test_automatic_discovery_does_not_repeat_terminally_unavailable_material(tmp_path):
    from oko.collection.demo import DemoSource
    from oko.collection.contracts import DiscoveryResult, Publication, SourceDeleted
    from tests.support.reader import Clock
    import time
    class Source(DemoSource):
        calls=0
        discoveries=0
        async def discover(self,cursor):
            self.discoveries+=1
            return DiscoveryResult((Publication('1','https://fixture.test/one',1800000000),),'1')
        async def fetch(self,p):
            self.calls+=1
            raise SourceDeleted()
    clock,source=Clock(),Source()
    with TestClient(create_app(tmp_path,token='test',api_only=True,source=source,clock=clock),headers=HEADERS) as client:
        wait_for(client,lambda:source.calls==1)
        wait_for(client,lambda:any('удалена' in p['message'] for p in client.get('/api/v3/status').json()['problems']['current']))
        assert client.post('/api/v3/collect',json={'scope':'feed','feed_id':'demo'}).status_code==200
        wait_for(client,lambda:source.calls==2)
        clock.now+=901
        wait_for(client,lambda:source.discoveries>=3)
        time.sleep(.12)
        assert source.calls==2


def test_refresh_retries_failed_filter_without_repeating_successful_filters(tmp_path):
    import json
    from oko.collection.demo import DemoSource
    from oko.processing.contracts import ProcessingError
    from tests.support.codex import CodexService, configure_codex
    class Model(CodexService):
        failed=True
        calls=[]
        async def complete(self,prompt,schema,directory,choice):
            item=json.loads(prompt.split('\n',1)[1])['sources'][0]['id']
            self.calls.append(item)
            if self.failed and len(set(self.calls))==1:
                raise ProcessingError('Temporary filter failure')
            return json.dumps({'hidden':False,'reason':'','removed':[]})
    model=Model()
    with TestClient(create_app(tmp_path,token='test',api_only=True,source=DemoSource(),codex_client=model),headers=HEADERS) as client:
        configure_codex(client)
        client.patch('/api/v3/feeds/demo',json={'public_ai':True})
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':'Keep all text'})
        cards=wait_for(client,lambda:(c if len(c:=client.get('/api/v3/cards').json())==3 else None))
        wait_for(client,lambda:sum(client.get('/api/v3/materials/'+c['id']).json()['filter']['attempt']['state']=='ready' for c in cards)==2)
        old=list(model.calls)
        model.failed=False
        assert client.post('/api/v3/collect',json={'scope':'feed','feed_id':'demo'}).status_code==200
        wait_for(client,lambda:all(client.get('/api/v3/materials/'+c['id']).json()['filter']['attempt']['state']=='ready' for c in cards))
        assert len(model.calls)==len(old)+1 and model.calls[-1]==old[0]
