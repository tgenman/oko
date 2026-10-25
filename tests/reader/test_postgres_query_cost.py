"""Observe database traffic at the HTTP boundary without replacing database behavior."""
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
