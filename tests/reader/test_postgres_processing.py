"""Worker invariants on PostgreSQL: accepted output, restart and late completion."""
import asyncio
import json

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
