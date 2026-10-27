"""Observe database traffic at the HTTP boundary without replacing database behavior."""
import pytest
import psycopg
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import HEADERS, Clock


def observe_queries(monkeypatch):
    queries = []
    original = psycopg.Connection.execute

    def execute(connection, query, *args, **kwargs):
        if isinstance(query, str):
            queries.append(query)
        return original(connection, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, 'execute', execute)
    return queries


def request_queries(monkeypatch):
    from tests.support.reading_cost import request_sql
    queries = []
    original = psycopg.Connection.execute
    def execute(connection, query, *args, **kwargs):
        if request_sql.get() is not None and isinstance(query, str):
            queries.append(query)
        return original(connection, query, *args, **kwargs)
    monkeypatch.setattr(psycopg.Connection, 'execute', execute)
    return queries


def test_reading_does_not_write_trigger_clocks(postgres_url, tmp_path, monkeypatch):
    queries = observe_queries(monkeypatch)
    clock = Clock()
    with TestClient(create_app(tmp_path, token='test', api_only=True,
                    database_url=postgres_url, **clock.options()), headers=HEADERS) as client:
        assert client.get('/api/v3/status').status_code == 200
        assert client.patch('/api/v3/settings/oko/global', json={'prompt_filters': 'Synthetic prompt'}).status_code == 200
        assert client.get('/api/v3/cards').json() == []
        queries.clear()
        for _ in range(4):
            response = client.get('/api/v3/settings/oko/global')
            assert response.status_code == 200
            assert response.json()['effective']['prompt_filters']['value'] == 'Synthetic prompt'
            assert client.get('/api/v3/cards').json() == []
        assert not [query for query in queries if "set_config('oko.clock'" in query]


def test_write_sets_trigger_clocks_once_per_transaction(postgres_url, tmp_path, monkeypatch):
    queries = observe_queries(monkeypatch)
    clock = Clock()
    with TestClient(create_app(tmp_path, token='test', api_only=True,
                    database_url=postgres_url, **clock.options()), headers=HEADERS) as client:
        assert client.get('/api/v3/status').status_code == 200
        queries.clear()
        clock.now += 10
        assert client.patch('/api/v3/settings/oko/global', json={'prompt_filters': 'Changed rules'}).status_code == 200
        setups = [query for query in queries if "set_config('oko.clock'" in query]
        transactions = [query for query in queries if query.strip().upper() == 'BEGIN']
        assert setups and len(setups) == len(transactions)
        assert client.get('/api/v3/settings/oko/global').json()['effective']['prompt_filters']['value'] == 'Changed rules'


@pytest.mark.parametrize("count", [10, 100, 1000])
def test_navigation_batches_requested_feed_facts(postgres_url, tmp_path, monkeypatch, count):
    from tests.support.storage import Store
    from oko.subscriptions.subscriptions import AddFeed
    from tests.support.reading_cost import traced_app

    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    store.set_work_enabled(False)
    with store.db:
        for n in range(count):
            store.subscriptions.add_feed(AddFeed(url=f'https://synthetic.test/{n}', title=f'Feed {n}', folder='Batch', verification='synthetic'))
    store.db.close()
    queries = request_queries(monkeypatch)
    app = traced_app(tmp_path, database_url=postgres_url)
    with TestClient(app, headers=HEADERS) as client:
        assert client.get('/api/v3/status').status_code == 200
        queries.clear()
        response = client.post('/api/v3/reading/check', json=dict(storage_id=None, resources=[dict(kind='navigation', known_version=None)]))
        assert response.status_code == 200
        resources = response.json()['resources']
        feeds = [r for r in resources if r['kind'] == 'feed']
        assert len(feeds) == count
        assert all(r['state'] == 'changed' for r in feeds)
        assert len(queries) < 20 + 12 * ((count + 399) // 400), len(queries)
        queries.clear()
        assert len(client.get('/api/v3/subscriptions').json()['feeds']) == count
        assert len(queries) < 12 + 8 * ((count + 399) // 400), len(queries)


@pytest.mark.parametrize("count", [10, 100, 1000])
def test_compact_cards_batch_links_and_processing_permissions(postgres_url, tmp_path, monkeypatch, count):
    from tests.support.worker_library import worker_library
    from tests.support.reading_cost import traced_app

    store, first, _, _ = worker_library(tmp_path, database_url=postgres_url)
    store.set_work_enabled(False)
    store.processing.background = True
    with store.db:
        template = dict(store.db.execute('SELECT * FROM items WHERE id = %s', (first,)).fetchone())
        fields = list(template)
        for n in range(1, count):
            identity = f'{n:032x}'
            row = dict(template, id=identity, source_id=identity, external_id=identity)
            store.db.execute('INSERT INTO items('+','.join(fields)+') VALUES ('+','.join('%s' for _ in fields)+')', tuple(row[f] for f in fields))
            store.db.execute('INSERT INTO feed_publications VALUES ( %s , %s )', ('demo', identity))
    store.db.close()
    queries = request_queries(monkeypatch)
    app = traced_app(tmp_path, database_url=postgres_url)
    with TestClient(app, headers=HEADERS) as client:
        assert client.get('/api/v3/status').status_code == 200
        queries.clear()
        response = client.post('/api/v3/reading/check', json=dict(storage_id=None, resources=[dict(kind='list', known_version=None, known_cards={}, options=dict(target=dict(scope='all'),sort='newest',show_hidden=False))]))
        assert response.status_code == 200
        cards = [r for r in response.json()['resources'] if r['kind'] == 'card']
        assert len(cards) == count
        assert all(r['state'] == 'changed' and r['data']['snippet'] == 'Useful text' for r in cards)
        assert len(queries) < 20 + 8 * ((count + 399) // 400), len(queries)


@pytest.mark.parametrize('linked_count', [10, 100, 1000])
def test_saved_summary_sources_reuse_batched_related_rows(postgres_url, tmp_path, monkeypatch, linked_count):
    import time
    from oko.subscriptions.subscriptions import Overrides
    from tests.support.worker_library import worker_library
    from tests.support.reading_cost import traced_app

    store, parent, _, _ = worker_library(tmp_path, database_url=postgres_url)
    store.set_work_enabled(False)
    store.processing.background = True
    store.subscriptions.update_settings('oko', 'global', Overrides(prompt_filters=''))
    with store.db:
        template = dict(store.db.execute('SELECT * FROM items WHERE id = %s', (parent,)).fetchone())
        fields = list(template)
        children = []
        for n in range(linked_count):
            identity = f'{n+1:032x}'
            children.append(identity)
            row = dict(template, id=identity, source_id=identity, external_id=identity)
            store.db.execute('INSERT INTO items('+','.join(fields)+') VALUES ('+','.join('%s' for _ in fields)+')', [row[f] for f in fields])
            store.db.execute('INSERT INTO publication_links VALUES ( %s , %s )', (parent, identity))
        signature = store.processing.theses.signature(parent)
        assert signature
        store.publications.accept_result(parent, 'theses', signature, dict(points=[dict(text='Saved summary', sources=[dict(id=i) for i in children])]), None)
    store.db.close()
    queries = request_queries(monkeypatch)
    app = traced_app(tmp_path, database_url=postgres_url)
    with TestClient(app, headers=HEADERS) as client:
        for _ in range(2000):
            saved = app.state.host.execution._store
            if saved is not None and not saved.processing.recovering:
                break
            time.sleep(.01)
        queries.clear()
        response = client.get('/api/v3/cards')
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert len(response.json()[0]['theses']['result']['points'][0]['references']) == linked_count
        assert len(queries) < 25, len(queries)
        queries.clear()
        preview = client.post('/api/v3/folders/demo/completion/preview')
        assert preview.status_code == 200
        assert preview.json()['cards_count'] == preview.json()['publications_count'] == 1
        assert len(queries) < 20, len(queries)


@pytest.mark.parametrize('count', [10, 100, 1000])
def test_saved_subtitles_batch_paths_but_recheck_files(postgres_url, tmp_path, monkeypatch, count):
    import json
    import time
    from oko.subscriptions.subscriptions import Overrides
    from tests.support.worker_library import worker_library
    from tests.support.reading_cost import traced_app

    store, parent, _, _ = worker_library(tmp_path, database_url=postgres_url)
    store.set_work_enabled(False)
    store.processing.background = True
    store.subscriptions.update_settings('oko', 'global', Overrides(prompt_filters='', theses=False))
    with store.db:
        template = dict(store.db.execute('SELECT * FROM items WHERE id = %s', (parent,)).fetchone())
        fields = list(template)
        for n in range(count):
            identity = parent if n == 0 else f'{n+1:032x}'
            media = f'{n+10000:032x}'
            content = json.loads(template['content'])
            content['extra']['youtube'] = dict(captions='available', captions_digest='test')
            content['blocks'].append(dict(type='media', role='subtitles', media_id=media))
            if n:
                row = dict(template, id=identity, source_id=identity, external_id=identity, platform='youtube', content=json.dumps(content))
                store.db.execute('INSERT INTO items('+','.join(fields)+') VALUES ('+','.join('%s' for _ in fields)+')', [row[f] for f in fields])
                store.db.execute('INSERT INTO feed_publications VALUES ( %s , %s )', ('demo', identity))
            else:
                store.db.execute('UPDATE items SET content = %s , platform = %s WHERE id = %s', (json.dumps(content), 'youtube', identity))
            (tmp_path / 'media' / media).write_text('Synthetic captions')
            store.db.execute("INSERT INTO media_files ( id , publication_id , resource_key , source_locator , name , storage_key , status ) VALUES ( %s , %s , %s , %s , %s , %s , 'ready' )", (media, identity, 'captions', 'synthetic:'+media, 'captions.txt', 'media/'+media))
    store.db.close()
    queries = request_queries(monkeypatch)
    app = traced_app(tmp_path, database_url=postgres_url)
    with TestClient(app, headers=HEADERS) as client:
        for _ in range(2000):
            saved = app.state.host.execution._store
            if saved is not None and not saved.processing.recovering:
                break
            time.sleep(.01)
        queries.clear()
        response = client.get('/api/v3/cards')
        assert response.status_code == 200
        assert len(response.json()) == count
        assert all('Synthetic captions' in card['snippet'] for card in response.json())
        assert len(queries) < 20 + 8 * ((count + 399) // 400), len(queries)
        (tmp_path / 'media' / f'{10000:032x}').unlink()
        response = client.get('/api/v3/reading/materials/'+parent)
        material = next(resource['data'] for resource in response.json()['resources'] if resource['kind'] == 'material')
        assert 'Synthetic captions' not in material['content']['text']
