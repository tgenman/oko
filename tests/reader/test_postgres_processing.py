"""Worker invariants on PostgreSQL: accepted output, restart and late completion."""
import asyncio
import json

import httpx
import pytest

from oko.collection.contracts import Material
from oko.services.collection import CollectionService
from tests.support.storage import Store
from tests.support.filters import transport
from tests.support.source import StructuredSource
from tests.support.worker_library import worker_library, workers, pass_worker, saved


@pytest.mark.parametrize('phase', ['processing_after_result', 'processing_after_finish', 'processing_after_commit'])
def test_processing_result_job_and_revision_commit_together(postgres_url, tmp_path, phase):
    def fault(point):
        if point == phase:
            raise OSError('synthetic interrupted acceptance')
    store, item, execution, tasks = worker_library(tmp_path, fault=fault, database_url=postgres_url)
    try:
        asyncio.run(pass_worker(workers(execution, tasks)[0], tasks))
        committed = phase == 'processing_after_commit'
        assert bool(saved(store, item)['filtered_content']) == committed
        assert (store.db.execute("SELECT status FROM jobs WHERE kind = 'filter' AND item = %s", (item,)).fetchone()[0] == 'done') == committed
    finally:
        store.db.close()
    restarted = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        assert bool(saved(restarted, item)['filtered_content']) == committed
        execution._store = restarted
        if committed:
            asyncio.run(pass_worker(workers(execution, tasks)[1], tasks))
            result = saved(restarted, item)
            assert result['summarized_content']['points']
            assert restarted.db.execute("SELECT status FROM jobs WHERE kind = 'theses' AND item = %s", (item,)).fetchone()[0] == 'done'
    finally:
        restarted.db.close()


@pytest.mark.parametrize('state', ['failed', 'unavailable', 'cancelled', 'source_wait'])
def test_restart_preserves_terminal_work_and_external_wait(postgres_url, tmp_path, state):
    store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
    worker = workers(execution, tasks)[0]
    with store.jobs.transaction(lambda: True):
        worker.service.reconcile()
        store.jobs.reconcile('filter', worker.service.candidates('filter'))
        job = store.jobs.claim_processing('filter', store.clock())
        assert job
        worker.service.prepare(item, 'filter')
        store.db.execute("UPDATE jobs SET status = %s , wait_scope = %s , retry_at = %s , next = %s , error = 'saved failure' WHERE id = %s",
            ('pending' if state == 'source_wait' else state, 'source:synthetic' if state == 'source_wait' else None,
             store.wall_clock() + 120 if state == 'source_wait' else None, store.clock() + 120 if state == 'source_wait' else 0, job.id))
    store.db.close()
    restarted = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        execution._store = restarted
        calls = []
        asyncio.run(pass_worker(workers(execution, tasks, transport(calls, theses=True))[0], tasks))
        row = restarted.db.execute('SELECT status , wait_scope , retry_at , error FROM jobs WHERE id = %s', (job.id,)).fetchone()
        assert row['status'] == ('pending' if state == 'source_wait' else state)
        assert row['error'] == 'saved failure' and not calls
        if state == 'source_wait':
            assert row['retry_at'] > restarted.wall_clock() and row['wait_scope'] == 'source:synthetic'
    finally:
        restarted.db.close()


@pytest.mark.parametrize('error', [False, True])
def test_old_success_or_error_cannot_replace_new_input(postgres_url, tmp_path, error):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        native = transport([], theses=True)
        async def model(request):
            if request.url.path == '/v1/chat/completions':
                entered.set()
                await release.wait()
                if error:
                    return httpx.Response(503, text='old failure')
            return native.handler(request)
        store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
        try:
            worker = workers(execution, tasks, httpx.MockTransport(model))[0]
            await worker.tick()
            await asyncio.wait_for(entered.wait(), 3)
            old = dict(store.db.execute("SELECT * FROM jobs WHERE kind='filter'").fetchone())
            with store.jobs.transaction(execution._live):
                source = StructuredSource(None)
                CollectionService(execution.require_store).collected(item, source.describe(store.publication(item), Material('Title', 'New input')), manual=False)
            with store.jobs.transaction(execution._live):
                worker.service.reconcile()
                store.jobs.reconcile('filter', worker.service.candidates('filter'))
            replacement = dict(store.db.execute("SELECT * FROM jobs WHERE kind='filter'").fetchone())
            assert replacement['generation'] > old['generation']
            release.set()
            await tasks._ai
            assert saved(store, item)['filtered_content'] == {}
            latest = dict(store.db.execute("SELECT * FROM jobs WHERE kind='filter'").fetchone())
            assert latest['generation'] == replacement['generation'] and latest['error'] is None and latest['status'] == 'pending'
        finally:
            release.set()
            if tasks._ai:
                await tasks._ai
            store.db.close()
    asyncio.run(scenario())


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
        assert client.get('/api/v3/cards').json() == []
        assert [card['id'] for card in client.get('/api/v3/cards?show_hidden=true').json()] == [item]
        preview = client.post('/api/v3/folders/demo/completion/preview')
        assert preview.status_code == 200, preview.text
        assert preview.json()['publications_count'] == 1
        for _ in range(2):
            result = client.post('/api/v3/folders/demo/completion', json={'token': preview.json()['token']})
            assert result.status_code == 200, result.text
        assert client.get('/api/v3/cards?show_hidden=true').json() == []
        assert client.get('/api/v3/materials/' + item).status_code == 404


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
        wait_for(client, lambda: all(card['theses']['result'] for card in client.get('/api/v3/cards').json()))
        count = len(service.inputs)
        assert client.put('/api/v3/processors/codex', json=configuration('test-large', 'high', True)).status_code == 200
        material = client.get('/api/v3/materials/' + item).json()
        assert material['theses']['result']['points'][0]['text'] == 'test-small'
        assert material['theses']['result']['provenance']['model'] == 'test-small'
        assert len(service.inputs) == count
        assert client.post('/api/v3/materials/' + item + '/theses/retry').status_code == 200
        changed = wait_for(client, lambda: (value if (value := client.get('/api/v3/materials/' + item).json())['theses']['result'] and value['theses']['result']['provenance'].get('model') == 'test-large' else None))
        assert changed['theses']['result']['points'][0]['text'] == 'test-large'
        assert len(service.inputs) == count + 1


def test_interrupted_attempt_recovers_its_captured_execution_choice(postgres_url, tmp_path):
    store, item, execution, tasks = worker_library(tmp_path, database_url=postgres_url)
    try:
        worker = workers(execution, tasks)[0]
        with store.jobs.transaction(lambda: True):
            worker.service.reconcile()
            store.jobs.reconcile('filter', worker.service.candidates('filter'))
            old = store.jobs.claim_processing('filter', store.clock())
            prepared = worker.service.prepare(item, 'filter').input
            chosen = dict(processor='codex', model='saved-model', reasoning_effort='low', fast=False)
            store.jobs.save_origin(old, prepared.signature, chosen)
    finally:
        store.db.close()
    restarted = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        with restarted.jobs.transaction(lambda: True):
            current = restarted.jobs.claim_processing('filter', restarted.clock() + 3601)
            assert current and current.generation > old.generation
            assert restarted.jobs.origin(current, prepared.signature) == chosen
    finally:
        restarted.db.close()
