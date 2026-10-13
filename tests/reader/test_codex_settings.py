import pytest
from fastapi.testclient import TestClient
from oko.reader.app import create_app
from tests.support.reader import HEADERS
from tests.support.codex import CodexService


class Models(CodexService):
    async def catalog(self):
        return [dict(model='test-small', display_name='Small', default=True,
                     reasoning_efforts=['low', 'high'], default_reasoning_effort='low', fast_supported=True),
                dict(model='test-large', display_name='Large', default=False,
                     reasoning_efforts=['high'], default_reasoning_effort='high', fast_supported=False)]


def configuration(model='test-small', effort='low', fast=False):
    return {kind: dict(model=model, reasoning_effort=effort, fast=fast)
            for kind in ('filter', 'theses', 'events', 'digest')}



def load_materials(client):
    from tests.support.reader import wait_for
    client.patch('/api/settings/oko/global', json={'events': False, 'theses': False, 'digests': False, 'prompt_filters': ''})
    client.post('/api/tabs/reader/heartbeat')
    cards = wait_for(client, lambda: (cards if len(cards := client.get('/api/cards').json()) == 3 and all(c['complete'] for c in cards) else None))
    return cards[0]['id']


def test_reader_saves_independent_execution_choices_and_restores_them(tmp_path):
    choices = configuration()
    choices['events'] = dict(model='test-large', reasoning_effort='high', fast=False)
    with TestClient(create_app(tmp_path, token='test', codex_client=Models()), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        assert client.get('/api/processors/codex').json().get('config') is None
        assert client.get('/api/processors/codex/models').json()['models'][0]['model'] == 'test-small'
        response = client.put('/api/processors/codex', json=choices)
        assert response.status_code == 200, response.text
        assert client.get('/api/processors/codex').json()['config'] == choices
    with TestClient(create_app(tmp_path, token='test', codex_client=Models()), headers=HEADERS) as client:
        assert client.get('/api/processors/codex').json()['config'] == choices


def test_new_choice_keeps_ready_theses_and_explicit_retry_uses_new_model(tmp_path):
    import json
    from oko.collection.demo import DemoSource
    from tests.support.reader import wait_for
    class Service(Models):
        async def complete(self, prompt, schema, directory, choice):
            self.inputs.append(choice.model_dump())
            source = json.loads(prompt.split('\n', 1)[1])['sources'][0]
            return json.dumps({'points': [{'text': choice.model, 'sources': [{'id': source['id'], 'url': source['url']}]}]})
    service = Service()
    with TestClient(create_app(tmp_path, token='test', source=DemoSource(), codex_client=service), headers=HEADERS) as client:
        item = load_materials(client)
        assert client.put('/api/processors/codex', json=configuration()).status_code == 200
        client.patch('/api/feeds/demo', json={'public_ai': True})
        client.patch('/api/settings/oko/global', json={'theses': True})
        wait_for(client, lambda: all(c.get('theses') for c in client.get('/api/cards').json()))
        count = len(service.inputs)
        assert client.put('/api/processors/codex', json=configuration('test-large', 'high')).status_code == 200
        material = client.get('/api/materials/' + item).json()
        assert material['theses']['points'][0]['text'] == 'test-small'
        assert material['provenance']['model'] == 'test-small'
        assert len(service.inputs) == count
        assert client.post('/api/materials/' + item + '/theses/retry').status_code == 200
        changed = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('provenance') and m['provenance'].get('model') == 'test-large' else None))
        assert changed['theses']['points'][0]['text'] == 'test-large'
        assert len(service.inputs) == count + 1


def test_delayed_theses_finish_with_old_choice_after_settings_save(tmp_path):
    import asyncio, json, threading
    from oko.collection.demo import DemoSource
    from tests.support.reader import wait_for
    entered, released = threading.Event(), threading.Event()
    class Held(Models):
        async def complete(self, prompt, schema, directory, choice):
            self.inputs.append(choice.model)
            entered.set()
            while not released.is_set():
                await asyncio.sleep(.01)
            source = json.loads(prompt.split('\n',1)[1])['sources'][0]
            return json.dumps({'points':[{'text':choice.model,'sources':[{'id':source['id'],'url':source['url']}]}]})
    service = Held()
    with TestClient(create_app(tmp_path,token='test',source=DemoSource(),codex_client=service),headers=HEADERS) as client:
        item = load_materials(client)
        client.put('/api/processors/codex',json=configuration())
        client.patch('/api/feeds/demo',json={'public_ai':True})
        client.patch('/api/settings/oko/global',json={'theses':True})
        assert entered.wait(2)
        try:
            assert client.put('/api/processors/codex',json=configuration('test-large','high')).status_code==200
        finally:
            released.set()
        material = wait_for(client,lambda: (m if (m:=client.get('/api/materials/'+item).json()).get('theses') else None))
        assert material['provenance']['model']=='test-small'
        wait_for(client,lambda: all(c.get('theses') for c in client.get('/api/cards').json()))
        assert service.inputs[0]=='test-small' and service.inputs[1:]==['test-large','test-large']


@pytest.mark.parametrize('legacy', [False, True])
def test_partial_digest_retains_choice_after_restart_and_explicit_recreation(tmp_path, legacy):
    import json
    from oko.collection.demo import DemoSource
    from oko.collection.contracts import Material
    from oko.processing.contracts import ProcessingError
    from tests.support.reader import wait_for
    class Big(DemoSource):
        async def fetch(self, publication):
            return Material('Большой исходник', 'Текст ' * 10000)
    class Review(Models):
        blocked = True
        async def complete(self, prompt, schema, directory, choice):
            if self.inputs and self.blocked:
                raise ProcessingError('Синтетическая пауза')
            task = json.loads(prompt.split('\n',1)[1])
            self.inputs.append((task['operation'], choice.model))
            source = task['sources'][0]
            return json.dumps({'points':[{'text':'Тема','sources':[{'id':source['id'],'url':source['url']}]}]})
    service=Review()
    options=dict(source=Big(),token='test',codex_client=service)
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        load_materials(client)
        client.put('/api/processors/codex',json=configuration())
        client.patch('/api/feeds/demo',json={'public_ai':True})
        identity=client.post('/api/digests/folder/demo',json={}).json()['id']
        try:
            wait_for(client,lambda: 'Синтетическая пауза' in client.get('/api/status').json()['errors'])
        except AssertionError:
            raise AssertionError((client.get('/api/status').json(), client.get('/api/debug').json(),service.inputs))
        assert len(service.inputs)==1
        client.put('/api/processors/codex',json=configuration('test-large','high'))
    if legacy:
        import sqlite3
        with sqlite3.connect(tmp_path / 'state.sqlite') as db:
            db.execute('DELETE FROM execution_settings')
        path = tmp_path / 'digests' / (identity + '.md')
        header, text = path.read_text().split('\n\n',1)
        old = json.loads(header); old.pop('provenance',None); old.pop('event_origins',None)
        path.write_text(json.dumps(old)+'\n\n'+text)
    service.blocked=False
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        if legacy:
            state=wait_for(client,lambda: (state if (state:=client.get('/api/status').json()).get('review_restarts') else None))
            assert state['review_restarts'][0]['id']==identity
            assert len(service.inputs)==1
            assert client.post('/api/materials/'+identity+'/regenerate').status_code==200
            material=wait_for(client,lambda: (m if (m:=client.get('/api/materials/'+identity).json()).get('digest') else None))
            assert material['provenance']['model']=='test-large'
            assert len(service.inputs)==8
            return
        material=wait_for(client,lambda: (m if (m:=client.get('/api/materials/'+identity).json()).get('digest') else None))
        assert material['provenance']['model']=='test-small'
        assert len(service.inputs)==7
        assert {model for _,model in service.inputs}=={'test-small'}
        assert service.inputs[-1][0]=='digest_final'
        before={s['id'] for s in material['sources']}
        assert client.post('/api/materials/'+identity+'/regenerate').status_code==200
        changed=wait_for(client,lambda: (m if (m:=client.get('/api/materials/'+identity).json()).get('provenance') and m['provenance'].get('model')=='test-large' else None))
        assert {s['id'] for s in changed['sources']}==before
        assert len(service.inputs)==14


def test_filter_events_choices_and_recreation_only_selected_event(tmp_path):
    import json
    from oko.collection.contracts import Publication, Material
    from tests.support.reader import wait_for
    class Source:
        async def discover(self,cursor):
            return ([Publication(str(i),'https://example.org/'+str(i),1800000000+i) for i in range(4)] if cursor is None else []),'4'
        async def fetch(self,publication):
            return Material(publication.id,'Содержимое '+publication.id)
    class Actions(Models):
        failed=False
        async def complete(self,prompt,schema,directory,choice):
            task=json.loads(prompt.split('\n',1)[1]); self.inputs.append((task['operation'],choice.model,[s['id'] for s in task['sources']]))
            if task['operation']=='filter':
                return json.dumps(dict(hidden=False,reason='',removed=[]))
            assert task['operation']=='events'
            if self.failed:
                from oko.processing.contracts import ProcessingError
                raise ProcessingError('Синтетическая ошибка пересоздания')
            sources=task['sources'];groups=[]
            for offset in range(0,len(sources),2):
                part=sources[offset:offset+2]
                groups.append(dict(members=[s['id'] for s in part],title='Событие '+part[0]['title'],points=[dict(text=choice.model,sources=[dict(id=part[0]['id'],url=part[0]['url'])])],updates=[],conflicts=[]))
            return json.dumps(dict(groups=groups))
    service=Actions()
    with TestClient(create_app(tmp_path,source=Source(),token='test',codex_client=service),headers=HEADERS) as client:
        client.patch('/api/settings/oko/global',json={'theses':False,'events':False,'digests':False})
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client,lambda: len(c:=client.get('/api/cards').json())==4 and all(m['complete'] for m in c))
        choices=configuration();choices['filter']=dict(model='test-large',reasoning_effort='high',fast=False)
        assert client.put('/api/processors/codex',json=choices).status_code==200
        client.patch('/api/feeds/demo',json={'public_ai':True})
        client.patch('/api/settings/oko/global',json={'events':True,'prompt_filters':'Убери рекламу'})
        views=wait_for(client,lambda: (c if len(c:=client.get('/api/cards').json())==2 and all(m.get('event') for m in c) else None))
        assert [model for op,model,_ in service.inputs if op=='filter']==['test-large']*4
        assert [model for op,model,_ in service.inputs if op=='events']==['test-small']
        selected,untouched=views
        selected_members={s['id'] for s in selected['sources']}
        choices['events']=dict(model='test-large',reasoning_effort='high',fast=False)
        client.put('/api/processors/codex',json=choices)
        service.failed=True
        assert client.post('/api/materials/'+selected['id']+'/regenerate').status_code==200
        wait_for(client,lambda: 'Синтетическая ошибка пересоздания' in client.get('/api/status').json()['errors'])
        other=client.get('/api/materials/'+untouched['id'])
        assert other.status_code==200
        assert other.json()['provenance']['model']=='test-small'
        service.failed=False
        assert client.post('/api/materials/'+selected['id']+'/regenerate').status_code==200
        changed=wait_for(client,lambda: (m if (m:=client.get('/api/materials/'+selected['id']).json()).get('provenance',{}).get('model')=='test-large' else None))
        assert set(service.inputs[-1][2])==selected_members
        assert changed['event']['points'][0]['text']=='test-large'
        other=client.get('/api/materials/'+untouched['id']).json()
        assert other['event']['points'][0]['text']=='test-small'
        assert other['provenance']['model']=='test-small'


def test_invalid_choices_and_catalog_failure_preserve_saved_settings(tmp_path):
    from oko.processing.contracts import ProcessingError
    class Broken(Models):
        failed=False
        async def catalog(self):
            if self.failed:
                raise ProcessingError('Каталог недоступен')
            return await super().catalog()
    service=Broken()
    with TestClient(create_app(tmp_path,token='test',codex_client=service),headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        initial=configuration()
        assert client.put('/api/processors/codex',json=initial).status_code==200
        for model, effort, fast in [('unknown','low',False),('test-large','low',False),('test-large','high',True)]:
            assert client.put('/api/processors/codex',json=configuration(model,effort,fast)).status_code==400
            assert client.get('/api/processors/codex').json()['config']==initial
        service.failed=True
        assert client.get('/api/processors/codex/models').status_code==409
        assert client.put('/api/processors/codex',json=configuration('test-large','high')).status_code==409
        assert client.get('/api/processors/codex').json()['config']==initial
