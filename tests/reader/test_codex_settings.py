import pytest
from fastapi.testclient import TestClient
from tests.support.app import create_app
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
            for kind in ('filter', 'theses')}



def load_materials(client):
    from tests.support.reader import wait_for
    client.patch('/api/settings/oko/global', json={'theses': False, 'prompt_filters': ''})
    client.post('/api/tabs/reader/heartbeat')
    cards = wait_for(client, lambda: (cards if len(cards := client.get('/api/cards').json()) == 3 and all(c['complete'] for c in cards) else None))
    return cards[0]['id']


def test_reader_saves_independent_execution_choices_and_restores_them(tmp_path):
    choices = configuration()
    choices['filter'] = dict(model='test-large', reasoning_effort='high', fast=False)
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
