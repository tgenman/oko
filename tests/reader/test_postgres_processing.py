"""Worker invariants on PostgreSQL: accepted output, restart and late completion."""
from tests.support.cards import read_all_cards
import asyncio
import json
import pytest

from tests.support.storage import Store
from tests.support.worker_library import worker_library, workers, pass_worker, saved


def test_codex_choice_changes_only_future_work_on_postgres(postgres_url, tmp_path):
    from fastapi.testclient import TestClient
    from tests.support.synthetic_source import SyntheticSource
    from tests.support.app import create_app
    from tests.support.codex import CodexService
    from tests.support.reader import HEADERS, wait_for
    from tests.support.rss import wait_cards

    class Models(CodexService):
        async def catalog(self):
            return [dict(model=name, display_name=name, default=index == 0,
                         reasoning_efforts=['low', 'high'], default_reasoning_effort='low', fast_supported=True)
                    for index, name in enumerate(('test-small', 'test-large'))]
        async def complete(self, prompt, schema, directory, choice):
            self.inputs.append(choice.model)
            source = json.loads(prompt.split('\n', 1)[1])['sources'][0]
            return json.dumps({'points': [{'text': choice.model, 'sources': [{'id': source['id'], 'url': source['url']}]}]})
    def configuration(model, effort='low', fast=False):
        return {kind: dict(model=model, reasoning_effort=effort, fast=fast) for kind in ('filter', 'theses')}
    service = Models()
    with TestClient(create_app(tmp_path, token='test', api_only=True, database_url=postgres_url,
                              source=SyntheticSource(), codex_client=service), headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global', json={'theses': False, 'prompt_filters': ''}).status_code == 200
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        item = wait_cards(client, 3)[0]['id']
        assert client.put('/api/v3/processors/codex', json=configuration('test-small')).status_code == 200
        assert client.patch('/api/v3/feeds/fixture', json={'public_ai': True}).status_code == 200
        assert client.patch('/api/v3/settings/oko/global', json={'processor': 'codex', 'theses': True}).status_code == 200
        wait_for(client, lambda: all(client.get('/api/v3/publication/'+card['id']).json()['theses']['result'] for card in read_all_cards(client, '/api/v3/cards')))
        count = len(service.inputs)
        assert client.put('/api/v3/processors/codex', json=configuration('test-large', 'high', True)).status_code == 200
        material = client.get('/api/v3/publication/' + item).json()
        assert material['theses']['result']['points'][0] == 'test-small'
        assert material['theses']['result']['provenance']['model'] == 'test-small'
        assert len(service.inputs) == count
        assert client.post('/api/v3/materials/' + item + '/theses/retry').status_code == 200
        changed = wait_for(client, lambda: (value if (value := client.get('/api/v3/publication/' + item).json())['theses']['result'] and value['theses']['result']['provenance'].get('model') == 'test-large' else None))
        assert changed['theses']['result']['points'][0] == 'test-large'
        assert len(service.inputs) == count + 1


def test_disabling_filter_keeps_ready_result_and_hiddenness_over_http(postgres_url, tmp_path):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS

    store, item, execution, tasks = worker_library(tmp_path, text='Реклама: synthetic advertisement', database_url=postgres_url)
    try:
        asyncio.run(pass_worker(workers(execution, tasks)[0], tasks))
        assert saved(store, item)['filtered_content']['hidden'] is True
        store.set_work_enabled(False)
    finally:
        store.db.close()
    with TestClient(create_app(tmp_path, token='test', api_only=True, database_url=postgres_url), headers=HEADERS) as client:
        before = client.get('/api/v3/publication/' + item).json()['filter']
        assert before['state'] == 'ready' and before['result']['hidden'] is True
        reply = client.patch('/api/v3/settings/oko/global', json={'filter_enabled': False})
        assert reply.status_code == 200, reply.text
        assert client.get('/api/v3/publication/' + item).json()['filter'] == before
        assert read_all_cards(client, '/api/v3/cards') == []
        assert [card['id'] for card in read_all_cards(client, '/api/v3/cards?show_hidden=true')] == [item]


def test_http_disabling_filter_does_not_cancel_started_model_call(postgres_url, tmp_path):
    import threading
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.filters import transport
    from tests.support.reader import HEADERS, wait_for

    store, item, _, _ = worker_library(tmp_path, database_url=postgres_url)
    store.set_work_enabled(False)
    store.db.close()
    started, release = threading.Event(), threading.Event()
    calls = []
    upstream = transport(calls)
    async def respond(request):
        if request.url.path == '/v1/chat/completions':
            started.set()
            await asyncio.to_thread(release.wait, 10)
        return upstream.handler(request)
    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url, ai_transport=httpx.MockTransport(respond))
    with TestClient(app, headers=HEADERS) as client:
        try:
            assert client.patch('/api/v3/settings/oko/global', json={'filter_enabled': True}).status_code == 200
            assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
            assert started.wait(5)
            response = client.patch('/api/v3/settings/oko/global', json={'filter_enabled': False})
            assert response.status_code == 200, response.text
            release.set()
            result = wait_for(client, lambda: (value if (value := client.get('/api/v3/publication/' + item).json())['filter']['state'] == 'ready' else None))
            assert result['filter']['result'] is not None
        finally:
            release.set()


def test_disabled_filter_without_a_result_is_reported_disabled_over_http(postgres_url, tmp_path):
    from fastapi.testclient import TestClient
    from oko.subscriptions.subscriptions import Overrides
    from tests.support.app import create_app
    from tests.support.reader import HEADERS

    store, item, _, _ = worker_library(tmp_path, database_url=postgres_url)
    try:
        store.subscriptions.update_settings('oko', 'global', Overrides(filter_enabled=False))
        store.settings_changed()
        store.set_work_enabled(False)
    finally:
        store.db.close()
    with TestClient(create_app(tmp_path, token='test', api_only=True, database_url=postgres_url), headers=HEADERS) as client:
        response = client.get('/api/v3/publication/' + item)
        assert response.status_code == 200, response.text
        assert response.json()['filter']['state'] == 'disabled'
        assert response.json()['filter']['result'] is None


def test_disabling_theses_keeps_ready_result_over_http(postgres_url, tmp_path):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS

    store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
    try:
        asyncio.run(pass_worker(workers(execution, tasks)[0], tasks))
        asyncio.run(pass_worker(workers(execution, tasks)[1], tasks))
        assert saved(store, item)['summarized_content']['points']
        store.set_work_enabled(False)
    finally:
        store.db.close()
    with TestClient(create_app(tmp_path, token='test', api_only=True, database_url=postgres_url), headers=HEADERS) as client:
        before = client.get('/api/v3/publication/' + item).json()['theses']
        assert before['state'] == 'ready' and before['result'] is not None
        response = client.patch('/api/v3/settings/oko/global', json={'theses': False})
        assert response.status_code == 200, response.text
        assert client.get('/api/v3/publication/' + item).json()['theses'] == before
        assert client.patch('/api/v3/settings/oko/global', json={'theses': True}).status_code == 200
        assert client.get('/api/v3/publication/' + item).json()['theses'] == before


def test_disabled_theses_without_result_are_reported_disabled_over_http(postgres_url, tmp_path):
    from fastapi.testclient import TestClient
    from oko.subscriptions.subscriptions import Overrides
    from tests.support.app import create_app
    from tests.support.reader import HEADERS

    store, item, _, _ = worker_library(tmp_path, database_url=postgres_url)
    try:
        store.subscriptions.update_settings('oko', 'global', Overrides(theses=False, filter_enabled=False))
        store.settings_changed()
        store.set_work_enabled(False)
    finally:
        store.db.close()
    with TestClient(create_app(tmp_path, token='test', api_only=True, database_url=postgres_url), headers=HEADERS) as client:
        response = client.get('/api/v3/publication/' + item)
        assert response.status_code == 200, response.text
        assert response.json()['theses']['state'] == 'disabled'
        assert response.json()['theses']['result'] is None


def test_http_rule_change_rejects_old_model_response_and_starts_replacement(postgres_url, tmp_path):
    import threading
    import psycopg
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.filters import transport
    from tests.support.reader import HEADERS, wait_for

    store, item, _, _ = worker_library(tmp_path, database_url=postgres_url)
    store.set_work_enabled(False)
    store.db.close()
    started, release = threading.Event(), threading.Event()
    requests = []
    upstream = transport([],theses=True)
    async def respond(request):
        if request.url.path == '/v1/chat/completions':
            task = json.loads(json.loads(request.content)['messages'][1]['content'])
            if task['operation'] == 'filter':
                requests.append(task)
                if len(requests) == 1:
                    started.set()
                    try:
                        await asyncio.to_thread(release.wait,10)
                    except asyncio.CancelledError:
                        await asyncio.to_thread(release.wait,10)
                    payload = json.dumps(dict(hidden=True,reason='Old rules',removed=[]))
                    return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':payload}}]})
        return upstream.handler(request)
    app = create_app(tmp_path,token='test',api_only=True,database_url=postgres_url,ai_transport=httpx.MockTransport(respond))
    with TestClient(app,headers=HEADERS) as client:
        try:
            assert client.put('/api/v3/power',json={'enabled':True}).status_code == 200
            assert started.wait(5)
            response = client.patch('/api/v3/settings/oko/global',json={'prompt_filters':'New rules'})
            assert response.status_code == 200, response.text
            release.set()
            wait_for(client,lambda: len(requests)>=2 and client.get('/api/v3/publication/'+item).json()['filter']['state']=='ready')
            with psycopg.connect(postgres_url) as db:
                filtered = json.loads(db.execute('SELECT filtered_content FROM publications WHERE id=%s',(item,)).fetchone()[0])
            assert filtered['hidden'] is False
            assert requests[0]['prompt'] != requests[1]['prompt']
        finally:
            release.set()


def test_http_processor_model_change_keeps_started_filter(postgres_url, tmp_path):
    import threading
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.filters import transport
    from tests.support.reader import HEADERS, wait_for

    store, item, _, _ = worker_library(tmp_path,database_url=postgres_url)
    store.set_work_enabled(False)
    store.db.close()
    started, release = threading.Event(), threading.Event()
    attempts = []
    upstream = transport([],theses=True)
    async def respond(request):
        if request.url.path == '/v1/chat/completions' and json.loads(json.loads(request.content)['messages'][1]['content'])['operation']=='filter':
            attempts.append(request)
            started.set()
            await asyncio.to_thread(release.wait,10)
        return upstream.handler(request)
    app = create_app(tmp_path,token='test',api_only=True,database_url=postgres_url,ai_transport=httpx.MockTransport(respond))
    with TestClient(app,headers=HEADERS) as client:
        try:
            assert client.put('/api/v3/power',json={'enabled':True}).status_code==200
            assert started.wait(5)
            response=client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:new','local_execution':True})
            assert response.status_code==200, response.text
            release.set()
            result=wait_for(client,lambda:(value if (value:=client.get('/api/v3/publication/'+item).json())['filter']['state']=='ready' else None))
            assert result['filter']['result'] is not None
            assert len(attempts)==1
        finally:
            release.set()


def test_interrupted_attempt_uses_new_model_on_next_attempt(postgres_url, tmp_path):
    import json
    from oko.execution import Execution
    from oko.processing.execution import CodexSettings
    from oko.subscriptions.subscriptions import FeedPatch, Overrides
    from oko.workers.control import JobControl
    from oko.workers.processing import FilterWorker
    from tests.reader.test_codex_settings import Models, configuration

    class Recording(Models):
        async def complete(self, prompt, schema, directory, choice):
            self.inputs.append(choice.model)
            return json.dumps({'hidden':False, 'reason':'', 'removed':[]})

    store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
    try:
        store.subscriptions.update_feed('fixture', FeedPatch(public_ai=True))
        store.subscriptions.update_settings('oko', 'global', Overrides(processor='codex'))
        store.settings_changed()
        store.configure_codex(CodexSettings.model_validate(configuration()))
        worker = workers(execution, tasks)[0]
        with store.jobs.transaction(lambda: True):
            worker.service.reconcile()
            job = store.jobs.claim_processing('filter', store.clock())
            assert job is not None
            prepared = worker.service.prepare(item, 'filter')
            assert prepared.input is not None
            store.jobs.save_execution(job, prepared.input.signature,
                {'processor':'codex','model':'test-small','reasoning_effort':'low','fast':False})
        store.configure_codex(CodexSettings.model_validate(configuration('test-large', 'high')))
    finally:
        store.db.close()
    restarted = Store(tmp_path, lambda _: None, database_url=postgres_url)
    execution = Execution(lambda: True)
    execution._store = restarted
    tasks = JobControl(execution, {}, restarted.clock)
    model = Recording()
    worker = FilterWorker(execution, tasks, lambda: restarted.clock() + 3601, model, None)
    try:
        asyncio.run(pass_worker(worker, tasks))
        assert model.inputs == ['test-large']
        assert saved(restarted, item)['filtered_content']
    finally:
        restarted.db.close()
