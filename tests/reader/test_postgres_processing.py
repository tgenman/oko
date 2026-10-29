"""Worker invariants on PostgreSQL: accepted output, restart and late completion."""
from tests.support.cards import read_all_cards
import asyncio
import json
import pytest

from tests.support.storage import Store
from tests.support.worker_library import worker_library, workers, pass_worker, saved


def test_processing_result_job_and_revision_commit_together(postgres_url, tmp_path):
    def fault(point):
        if point == 'processing_after_commit':
            raise OSError('synthetic interrupted acceptance')
    store, item, execution, tasks = worker_library(tmp_path, fault=fault, database_url=postgres_url)
    try:
        asyncio.run(pass_worker(workers(execution, tasks)[0], tasks))
        assert saved(store, item)['filtered_content']
        assert store.db.execute("SELECT status FROM jobs WHERE kind = 'filter' AND item = %s", (item,)).fetchone()[0] == 'done'
    finally:
        store.db.close()
    restarted = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        assert saved(restarted, item)['filtered_content']
        execution._store = restarted
        asyncio.run(pass_worker(workers(execution, tasks)[1], tasks))
        result = saved(restarted, item)
        assert result['summarized_content']['points']
        assert restarted.db.execute("SELECT status FROM jobs WHERE kind = 'theses' AND item = %s", (item,)).fetchone()[0] == 'done'
    finally:
        restarted.db.close()


def test_folder_completion_includes_hidden_materials_and_is_repeatable(postgres_url, tmp_path):
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
        assert read_all_cards(client, '/api/v3/cards') == []
        assert [card['id'] for card in read_all_cards(client, '/api/v3/cards?show_hidden=true')] == [item]
        preview = client.post('/api/v3/folders/demo/completion/preview')
        assert preview.status_code == 200, preview.text
        assert preview.json()['publications_count'] == 1
        for _ in range(2):
            result = client.post('/api/v3/folders/demo/completion', json={'token': preview.json()['token']})
            assert result.status_code == 200, result.text
        assert read_all_cards(client, '/api/v3/cards?show_hidden=true') == []
        assert client.get('/api/v3/publication/' + item).status_code == 404


def test_codex_choice_changes_only_future_work_on_postgres(postgres_url, tmp_path):
    from fastapi.testclient import TestClient
    from oko.collection.demo import DemoSource
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
                              source=DemoSource(), codex_client=service), headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global', json={'theses': False, 'prompt_filters': ''}).status_code == 200
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        item = wait_cards(client, 3)[0]['id']
        assert client.put('/api/v3/processors/codex', json=configuration('test-small')).status_code == 200
        assert client.patch('/api/v3/feeds/demo', json={'public_ai': True}).status_code == 200
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


def test_filter_disabled_does_not_start_a_new_attempt(postgres_url, tmp_path):
    from oko.subscriptions.subscriptions import Overrides
    from tests.support.filters import transport

    store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
    calls = []
    try:
        store.subscriptions.update_settings('oko', 'global', Overrides(filter_enabled=False))
        store.settings_changed()
        asyncio.run(pass_worker(workers(execution, tasks, transport(calls))[0], tasks))
        assert calls == []
        assert store.subscriptions.effective('feed', 'demo', details=False)['effective']['prompt_filters']['value'] == 'Keep useful text'
        store.subscriptions.update_settings('oko', 'global', Overrides(filter_enabled=True))
        store.settings_changed()
        asyncio.run(pass_worker(workers(execution, tasks, transport(calls))[0], tasks))
        assert calls
        assert saved(store, item)['filtered_content']
    finally:
        store.db.close()


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


def test_disabling_filter_during_model_call_accepts_the_in_flight_result(postgres_url, tmp_path):
    import httpx
    from oko.subscriptions.subscriptions import Overrides
    from tests.support.filters import transport

    store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
    calls = []
    started, release = asyncio.Event(), asyncio.Event()
    upstream = transport(calls)
    async def respond(request):
        if request.url.path == '/v1/chat/completions':
            started.set()
            await release.wait()
        return upstream.handler(request)
    async def scenario():
        worker = workers(execution, tasks, httpx.MockTransport(respond))[0]
        attempt = asyncio.create_task(pass_worker(worker, tasks))
        await asyncio.wait_for(started.wait(), 5)
        store.subscriptions.update_settings('oko', 'global', Overrides(filter_enabled=False))
        store.settings_changed()
        release.set()
        await asyncio.wait_for(attempt, 5)
    try:
        asyncio.run(scenario())
        assert saved(store, item)['filtered_content']
        assert store.db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[0] == 'done'
    finally:
        store.db.close()


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


def test_restart_after_disabling_running_filter_does_not_restart_attempt(postgres_url, tmp_path):
    import httpx
    from oko.execution import Execution
    from oko.subscriptions.subscriptions import Overrides
    from oko.workers.control import JobControl
    from tests.support.filters import transport

    store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
    started = asyncio.Event()
    calls = []
    upstream = transport(calls)
    async def stalled(request):
        if request.url.path == '/v1/chat/completions':
            started.set()
            await asyncio.Event().wait()
        return upstream.handler(request)
    async def interrupt():
        worker = workers(execution, tasks, httpx.MockTransport(stalled))[0]
        await worker.tick()
        await asyncio.wait_for(started.wait(), 5)
        store.subscriptions.update_settings('oko', 'global', Overrides(filter_enabled=False))
        store.settings_changed()
        assert tasks._ai is not None
        tasks._ai.cancel()
        await asyncio.gather(tasks._ai, return_exceptions=True)
    try:
        asyncio.run(interrupt())
        assert store.db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[0] == 'running'
    finally:
        store.db.close()
    restarted = Store(tmp_path, lambda _: None, database_url=postgres_url)
    execution = Execution(lambda: True)
    execution._store = restarted
    tasks = JobControl(execution, {}, None, restarted.clock)
    calls.clear()
    try:
        asyncio.run(pass_worker(workers(execution, tasks, transport(calls))[0], tasks))
        assert calls == []
        assert restarted.db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[0] == 'blocked'
    finally:
        restarted.db.close()


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


def test_http_disabling_theses_does_not_cancel_started_model_call(postgres_url, tmp_path):
    import json
    import threading
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.filters import transport
    from tests.support.reader import HEADERS, wait_for

    store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
    try:
        asyncio.run(pass_worker(workers(execution, tasks)[0], tasks))
        store.set_work_enabled(False)
    finally:
        store.db.close()
    started, release = threading.Event(), threading.Event()
    calls = []
    upstream = transport(calls, theses=True)
    async def respond(request):
        if request.url.path == '/v1/chat/completions':
            task = json.loads(json.loads(request.content)['messages'][1]['content'])
            if task['operation'] == 'theses':
                started.set()
                await asyncio.to_thread(release.wait, 10)
        return upstream.handler(request)
    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url, ai_transport=httpx.MockTransport(respond))
    with TestClient(app, headers=HEADERS) as client:
        try:
            assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
            assert started.wait(5)
            response = client.patch('/api/v3/settings/oko/global', json={'theses': False})
            assert response.status_code == 200, response.text
            release.set()
            result = wait_for(client, lambda: (value if (value := client.get('/api/v3/publication/' + item).json())['theses']['state'] == 'ready' else None))
            assert result['theses']['result'] is not None
        finally:
            release.set()


def test_disabled_theses_wait_until_reenabled(postgres_url, tmp_path):
    from oko.subscriptions.subscriptions import Overrides
    from tests.support.filters import transport

    store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
    calls = []
    try:
        asyncio.run(pass_worker(workers(execution, tasks)[0], tasks))
        store.subscriptions.update_settings('oko', 'global', Overrides(theses=False))
        store.settings_changed()
        asyncio.run(pass_worker(workers(execution, tasks, transport(calls, theses=True))[1], tasks))
        assert calls == []
        assert store.db.execute("SELECT status FROM jobs WHERE kind='theses' AND item=%s", (item,)).fetchone()[0] == 'blocked'
        store.subscriptions.update_settings('oko', 'global', Overrides(theses=True))
        store.settings_changed()
        asyncio.run(pass_worker(workers(execution, tasks, transport(calls, theses=True))[1], tasks))
        assert calls and saved(store, item)['summarized_content']['points']
    finally:
        store.db.close()


@pytest.mark.parametrize(('kind', 'setting'), [('filter', 'filter_enabled'), ('theses', 'theses')])
def test_failed_in_flight_stage_is_blocked_after_disabling(postgres_url, tmp_path, kind, setting):
    import threading
    import time
    import httpx
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.filters import transport
    from tests.support.reader import HEADERS

    store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
    if kind == 'theses':
        asyncio.run(pass_worker(workers(execution, tasks)[0], tasks))
    store.set_work_enabled(False)
    store.db.close()
    started, release = threading.Event(), threading.Event()
    calls = []
    upstream = transport(calls, theses=True)
    async def respond(request):
        if request.url.path == '/v1/chat/completions':
            started.set()
            await asyncio.to_thread(release.wait, 10)
            return httpx.Response(503, text='synthetic temporary error')
        return upstream.handler(request)
    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url, ai_transport=httpx.MockTransport(respond))
    with TestClient(app, headers=HEADERS) as client, psycopg.connect(postgres_url, autocommit=True) as observer:
        try:
            assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
            assert started.wait(5)
            assert client.patch('/api/v3/settings/oko/global', json={setting: False}).status_code == 200
            release.set()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                row = observer.execute("SELECT status, error FROM jobs WHERE kind=%s AND item=%s", (kind, item)).fetchone()
                if row and row[0] != 'running':
                    break
                time.sleep(.02)
            assert row is not None and row[0] == 'blocked'
            assert row[1]
            stage = 'filter' if kind == 'filter' else 'theses'
            publication = client.get('/api/v3/publication/' + item)
            assert publication.status_code == 200, publication.text
            assert publication.json()[stage]['state'] == 'error'
            details = client.get('/api/v3/status/details')
            assert details.status_code == 200, details.text
            assert any({'kind': 'material', 'material_id': item} in problem['affected']
                       for problem in details.json()['problems']['current'])
        finally:
            release.set()


def test_interrupted_attempt_provenance_is_not_reused_by_next_claim(postgres_url, tmp_path):
    from oko.storage.job_repository import JobRepository

    store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
    origin = {'processor':'codex','model':'synthetic','reasoning_effort':'low','fast':False}
    try:
        worker = workers(execution, tasks)[0]
        with store.jobs.transaction(lambda: True):
            worker.service.reconcile()
            store.jobs.reconcile('filter', worker.service.candidates('filter'))
            job = store.jobs.claim_processing('filter', store.clock())
            assert job is not None
            prepared = worker.service.prepare(item, 'filter')
            assert prepared.input is not None
            signature = prepared.input.signature
            store.jobs.save_execution(job, signature, origin)
        assert JobRepository(store).saved_execution(job, signature) == origin
    finally:
        store.db.close()
    restarted = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        job = restarted.jobs.next_job(restarted.clock() + 3601, ai_only=True, processing_kind='filter')
        assert job is not None
        assert JobRepository(restarted).saved_execution(job, signature) is None
        previous = restarted.db.execute('SELECT execution_settings FROM jobs WHERE id=%s', (job.id,)).fetchone()[0]
        assert previous['parameters'] == origin and previous['generation'] < job.generation
    finally:
        restarted.db.close()


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
        store.subscriptions.update_feed('demo', FeedPatch(public_ai=True))
        store.subscriptions.update_settings('oko', 'global', Overrides(processor='codex'))
        store.settings_changed()
        store.configure_codex(CodexSettings.model_validate(configuration()))
        worker = workers(execution, tasks)[0]
        with store.jobs.transaction(lambda: True):
            worker.service.reconcile()
            store.jobs.reconcile('filter', worker.service.candidates('filter'))
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
    tasks = JobControl(execution, {}, None, restarted.clock)
    model = Recording()
    worker = FilterWorker(execution, tasks, lambda: restarted.clock() + 3601, model, None)
    try:
        asyncio.run(pass_worker(worker, tasks))
        assert model.inputs == ['test-large']
        assert saved(restarted, item)['filtered_content']
    finally:
        restarted.db.close()


def test_retry_after_failure_uses_model_selected_for_next_attempt(postgres_url, tmp_path):
    import json
    from oko.processing.contracts import ProcessingError
    from oko.processing.execution import CodexSettings
    from oko.subscriptions.subscriptions import FeedPatch, Overrides
    from oko.workers.processing import FilterWorker
    from tests.reader.test_codex_settings import Models, configuration

    class FailingOnce(Models):
        async def complete(self, prompt, schema, directory, choice):
            self.inputs.append(choice.model)
            if len(self.inputs) == 1:
                raise ProcessingError('synthetic temporary failure')
            return json.dumps({'hidden':False, 'reason':'', 'removed':[]})

    store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
    try:
        store.subscriptions.update_feed('demo', FeedPatch(public_ai=True))
        store.subscriptions.update_settings('oko', 'global', Overrides(processor='codex'))
        store.settings_changed()
        store.configure_codex(CodexSettings.model_validate(configuration()))
        model = FailingOnce()
        worker = FilterWorker(execution, tasks, lambda: store.clock() + 3601, model, None)
        asyncio.run(pass_worker(worker, tasks))
        assert model.inputs == ['test-small']
        store.configure_codex(CodexSettings.model_validate(configuration('test-large', 'high')))
        asyncio.run(pass_worker(worker, tasks))
        assert model.inputs == ['test-small', 'test-large']
        assert saved(store, item)['filtered_content']
    finally:
        store.db.close()


def test_processing_input_snapshot_is_atomic_and_does_not_copy_publication_text(postgres_url, tmp_path):
    store, item, execution, tasks = worker_library(tmp_path, text='Private synthetic publication text', database_url=postgres_url)
    try:
        worker = workers(execution, tasks)[0]
        with store.jobs.transaction(lambda: True):
            worker.service.reconcile()
            store.jobs.reconcile('filter', worker.service.candidates('filter'))
        before = store.db.execute("SELECT status,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()
        with pytest.raises(RuntimeError, match='rollback'), store.jobs.transaction(lambda: True):
            job = store.jobs.claim_processing('filter', store.clock())
            assert job is not None
            prepared = worker.service.prepare(item, 'filter')
            assert prepared.input is not None
            store.jobs.save_input(job, prepared.input)
            snapshot = store.db.execute('SELECT input_snapshot FROM jobs WHERE id=%s', (job.id,)).fetchone()[0]
            assert snapshot['generation'] == job.generation
            assert snapshot['feed_id'] == 'demo'
            assert snapshot['signature'] == prepared.input.signature
            assert snapshot['sources'][0]['id'] == item
            assert 'Private synthetic publication text' not in json.dumps(snapshot)
            raise RuntimeError('rollback synthetic claim')
        after = store.db.execute("SELECT status,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()
        assert tuple(after) == tuple(before)
    finally:
        store.db.close()
