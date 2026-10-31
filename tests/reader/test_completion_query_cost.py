"""Completion behavior and database work observed through the reader HTTP seam."""
from tests.support.cards import read_all_cards
import contextvars
import time
import json
import pytest

import psycopg
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import HEADERS
from tests.support.completion_library import library



def test_completion_removes_captured_library_with_one_reachability_pass(postgres_url, tmp_path, monkeypatch, record_property):
    library(tmp_path, postgres_url, 10)
    recording = contextvars.ContextVar('completion_queries', default=None)
    queries = []
    execute = psycopg.Connection.execute

    def observed(connection, query, *args, **kwargs):
        captured = recording.get()
        if captured is not None and isinstance(query, str):
            captured.append(query)
        return execute(connection, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, 'execute', observed)
    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url)

    @app.middleware('http')
    async def capture(request, call_next):
        token = recording.set(queries)
        try:
            return await call_next(request)
        finally:
            recording.reset(token)

    with TestClient(app, headers=HEADERS) as client:
        for _ in range(1000):
            store = app.state.host.execution._store
            if store is not None and not store.processing.recovering:
                break
            time.sleep(.01)
        preview = client.post('/api/v3/folders/demo/completion/preview')
        assert preview.status_code == 200
        assert preview.json()['cards_count'] == preview.json()['publications_count'] == 10
        queries.clear()
        completed = client.post('/api/v3/folders/demo/completion', json={'token': preview.json()['token']})
        assert completed.status_code == 200, completed.text
        passes = sum('WITH RECURSIVE component' in query for query in queries)
        completion_calls = len(queries)
        assert read_all_cards(client, '/api/v3/cards') == []
        assert client.post('/api/v3/folders/demo/completion', json={'token': preview.json()['token']}).status_code == 200
        assert passes == 1
        record_property('completion_sql_calls', completion_calls)
        record_property('completion_sql_budget_deferred', 350)
        print(f'Deferred completion SQL budget: {completion_calls} calls (previous limit 350)')


def test_preview_counts_cards_without_projecting_each_material(postgres_url, tmp_path, monkeypatch):
    library(tmp_path, postgres_url, 10)
    queries = []
    execute = psycopg.Connection.execute

    def observed(connection, query, *args, **kwargs):
        if isinstance(query, str):
            queries.append(query)
        return execute(connection, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, 'execute', observed)
    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url)
    with TestClient(app, headers=HEADERS) as client:
        for _ in range(1000):
            store = app.state.host.execution._store
            if store is not None and not store.processing.recovering:
                break
            time.sleep(.01)
        queries.clear()
        preview = client.post('/api/v3/folders/demo/completion/preview')
        captured = list(queries)
        assert preview.status_code == 200
        assert preview.json()['cards_count'] == preview.json()['publications_count'] == 10
        assert len(captured) <= 40


@pytest.mark.parametrize('corruption', ['content', 'attachment', 'media_ids', 'processing_metadata'])
def test_completion_preserves_unreadable_and_pending_members(tmp_path, corruption):
    library(tmp_path, None, 3)
    app = create_app(tmp_path, token='test', api_only=True)
    with TestClient(app, headers=HEADERS) as client:
        for _ in range(1000):
            store = app.state.host.execution._store
            if store is not None and not store.processing.recovering:
                break
            time.sleep(.01)
        initial=client.get('/api/v3/cards').json()
        assert initial['total']==3 and len(initial['items'])==3
        original={card['id']:card for card in initial['items']}
        # Model an unreadable saved copy and a discovered, not-yet-loaded
        # publication; fixture writes precede all observations under test.
        def damage_fixture():
            with store.db:
                identity = f'{1:032x}'
                if corruption == 'attachment':
                    content = json.loads(store.db.execute('SELECT content FROM publications WHERE id = %s', (identity,)).fetchone()[0])
                    content['extra']['_reader']['attachments'] = [{'name':'missing-mime', 'kind':'image'}]
                    store.db.execute('UPDATE publications SET content = %s WHERE id = %s', (json.dumps(content), identity))
                elif corruption in ('media_ids', 'processing_metadata'):
                    store.db.execute("UPDATE publications SET " + corruption + "='invalid-json' WHERE id=%s", (identity,))
                else:
                    store.db.execute("UPDATE publications SET content = '{}' WHERE id = %s", (identity,))
                store.db.execute('UPDATE publications SET complete = 0 WHERE id = %s', (f'{2:032x}',))
        client.portal.call(damage_fixture)
        result=client.get('/api/v3/cards')
        assert result.status_code==200, result.text
        page=result.json()
        assert page['total']==3 and len(page['items'])==3
        cards={card['id']:card for card in page['items']}
        damaged=f'{1:032x}'
        pending=f'{2:032x}'
        placeholder=cards[damaged]
        assert placeholder['title']=='Публикация недоступна'
        assert all(placeholder[key]==original[damaged][key] for key in ('id','feed_id','published_at','source_url'))
        assert placeholder['preview_url'] is None and not placeholder['loading'] and not placeholder['changed']
        assert not placeholder['post_web_unavailable'] and placeholder['external_attachment_kinds']==[]
        assert cards[pending]['loading']
        healthy=next(card for identity,card in cards.items() if identity not in (damaged,pending))
        assert healthy['title']!='Публикация недоступна'
        opened=client.get('/api/v3/publication/'+healthy['id']).json()
        assert opened['text']
        preview = client.post('/api/v3/feeds/demo/completion/preview').json()
        assert preview['cards_count'] == 3
        assert preview['publications_count'] == 3
        assert client.post('/api/v3/feeds/demo/completion', json={'token': preview['token']}).status_code == 200
        assert read_all_cards(client, '/api/v3/cards') == []
        assert client.post('/api/v3/feeds/demo/completion/preview').json()['publications_count'] == 0
