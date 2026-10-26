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
    from oko.storage.store import Store
    from oko.subscriptions.subscriptions import AddFeed
    from tests.support.reading_cost import traced_app, request_sql

    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    store.set_work_enabled(False)
    with store.db:
        for n in range(count):
            store.subscriptions.add_feed(AddFeed(url=f'https://synthetic.test/{n}', title=f'Feed {n}', folder='Batch', verification='synthetic'))
    store.db.close()
    queries = []
    original = psycopg.Connection.execute
    def execute(connection, query, *args, **kwargs):
        if request_sql.get() is not None and isinstance(query, str):
            queries.append(query)
        return original(connection, query, *args, **kwargs)
    monkeypatch.setattr(psycopg.Connection, 'execute', execute)
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
    from tests.support.reading_cost import traced_app, request_sql

    store, first, _, _ = worker_library(tmp_path, database_url=postgres_url)
    store.set_work_enabled(False)
    store.processing.background = True
    with store.db:
        template = dict(store.db.execute('SELECT * FROM items WHERE id=?', (first,)).fetchone())
        fields = list(template)
        for n in range(1, count):
            identity = f'{n:032x}'
            row = dict(template, id=identity, source_id=identity, external_id=identity)
            store.db.execute('INSERT INTO items('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')', tuple(row[f] for f in fields))
            store.db.execute('INSERT INTO feed_publications VALUES (?,?)', ('demo', identity))
    store.db.close()
    queries = []
    original = psycopg.Connection.execute
    def execute(connection, query, *args, **kwargs):
        if request_sql.get() is not None and isinstance(query, str):
            queries.append(query)
        return original(connection, query, *args, **kwargs)
    monkeypatch.setattr(psycopg.Connection, 'execute', execute)
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
