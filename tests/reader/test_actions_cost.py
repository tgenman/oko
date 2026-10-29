from tests.support.cards import read_all_cards
from tests.support.storage import analyze
from tests.support.postgres_work import measure_reads
"""Scoped actions and status keep bounded work through the reader HTTP interface."""
from fastapi.testclient import TestClient
import pytest

from tests.support.reading_cost import library, traced_app, tracing
from tests.support.reader import HEADERS


def test_completion_preview_ignores_other_folders(tmp_path):
    costs = []
    for other in (26, 100):
        root = tmp_path / str(other)
        feeds, _ = library(root, [26, other, other, other])
        app = traced_app(root)
        with TestClient(app, headers=HEADERS) as client:
            tracing(client, app)
            nav = client.get('/api/v3/reading-composition').json()
            folder = next(f['feed']['folder_id'] for f in nav['feeds'] if f['feed']['id'] == feeds[0])
            response = client.post('/api/v3/folders/' + folder + '/completion/preview')
            assert response.status_code == 200
            assert response.json()['publications_count'] == 26
            assert response.json()['cards_count'] == 26
            costs.append(int(response.headers['X-Test-SQL']))
    assert costs[1] <= costs[0] + 2, costs
    assert costs[0] < 400, costs


@pytest.mark.parametrize('count', [26, 100])
def test_status_and_viewed_work_budgets(tmp_path, count):
    _, items = library(tmp_path, [count] * 4)
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        first = client.get('/api/v3/status')
        assert first.status_code == 200
        assert int(first.headers['X-Test-SQL']) < 100
        second = client.get('/api/v3/status')
        assert second.status_code == 200
        assert int(second.headers['X-Test-SQL']) < 100
        response = client.post('/api/v3/materials/' + items[0] + '/viewed')
        assert response.status_code == 200
        # PostgreSQL tracing counts statements issued by the application.
        assert int(response.headers['X-Test-SQL']) < 240
        assert client.get('/api/v3/materials/' + items[0]).status_code == 404
        assert len(read_all_cards(client, '/api/v3/cards')) == count * 4 - 1


def test_unchanged_status_does_not_rewrite_problems(tmp_path):
    _, items = library(tmp_path, [26] * 4)
    from tests.support.storage import Store
    seed = Store(tmp_path, lambda _: None)
    with seed.db:
        seed.db.execute("UPDATE items SET content = jsonb_set(jsonb_set(content::jsonb,'{extra,_reader,full}','false'::jsonb),'{extra,_reader,reason}',to_jsonb('Fixture incomplete'::text))::text WHERE id = %s",(items[0],))
    with seed.db:
        seed.db.execute("UPDATE jobs SET status = 'done' WHERE kind = 'fetch' AND item = %s", (items[0],))
    seed.db.close()
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        first = client.get('/api/v3/status/details')
        assert first.status_code == 200
        second = client.get('/api/v3/status/details')
        assert second.status_code == 200
        assert not any(q.startswith('UPDATE reader_problems') for q in app.state.last_sql)
        assert first.json()['problems']['current'] == second.json()['problems']['current']
        original_problem = next(p for p in first.json()['problems']['current'] if p['code'] == 'material_incomplete')

        def change_fixture():
            store = app.state.host.require_store()
            with store.db:
                store.db.execute("UPDATE items SET content = jsonb_set(jsonb_set(content::jsonb,'{extra,_reader,full}','true'::jsonb),'{extra,_reader,reason}',to_jsonb(''::text))::text WHERE id = %s",(items[0],))
                store.db.execute("UPDATE jobs SET status = 'done' WHERE kind = 'fetch' AND item = %s", (items[0],))
        client.portal.call(change_fixture)
        response = client.get('/api/v3/status/details')
        assert response.status_code == 200
        assert not any(p['code'] == 'material_incomplete' for p in response.json()['problems']['current'])
        assert any(p['id'] == original_problem['id'] and p['outcome'] == 'resolved' for p in response.json()['problems']['recent'])


def test_latest_outcomes_scale_and_survive_restart(tmp_path):
    import json
    from tests.support.storage import Store
    from tests.support.reading_cost import request_sql

    store = Store(tmp_path, lambda _: None)
    rows = []
    for n in range(3000):
        context = json.dumps(dict(identity=f'fixture-{n}', title='Synthetic failure', objects=[], action=None,command_context={'operation':'retry_material','material_id':f'fixture-{n}'}))
        rows.append((f'old-{n:032x}', f'fixture-{n}', context, store.wall_clock(), 'failed'))
        rows.append((f'new-{n:032x}', f'fixture-{n}', context, store.wall_clock(), {0: 'succeeded', 1: 'unknown', 2: 'pending'}.get(n, 'failed')))
    with store.db:
        store.db.executemany('INSERT INTO reader_action_results ( id , identity , body , started , result ) VALUES ( %s , %s , %s , %s , %s )', rows)
        assert store.db.execute("SELECT 1 FROM pg_indexes WHERE schemaname=current_schema() AND indexname='reader_action_identity_sequence'").fetchone()
    store.set_work_enabled(False)
    store.db.close()
    for _ in range(2):
        app = traced_app(tmp_path)
        with TestClient(app, headers=HEADERS) as client:
            tracing(client, app)
            with measure_reads(app.state.host.require_store().db, enabled=lambda: request_sql.get() is not None) as work:
                response = client.get('/api/v3/status/details')
            assert response.status_code == 200
            problems = response.json()['problems']['current']
            assert len(problems) == 2999
            assert all(p['command_id'].startswith('new-') for p in problems)
            assert sum(p['title'] == 'Результат действия пока неизвестен' for p in problems) == 2
            assert work.rows < 60000, work
            again = client.get('/api/v3/status/details')
            assert len(again.json()['problems']['current']) == 2999
            assert not any(q.startswith('UPDATE reader_problems') for q in app.state.last_sql)


def test_viewed_uses_point_updates_in_large_service_archive(tmp_path):
    from tests.support.storage import Store
    from tests.support.reading_cost import request_sql

    feeds, items = library(tmp_path, [1])
    store = Store(tmp_path, lambda _: None)
    with store.db:
        store.db.executemany('INSERT INTO history_members ( feed , item ) VALUES ( %s , %s )', [(feeds[0], f'old-{n}') for n in range(20000)] + [(feeds[0], items[0])])
        store.db.executemany("INSERT INTO jobs ( kind , item , status ) VALUES ( 'fetch' , %s , 'done' )", [(f'old-{n}',) for n in range(20000)])
        assert store.db.execute("SELECT 1 FROM pg_indexes WHERE schemaname=current_schema() AND indexname='history_members_item'").fetchone()
        assert store.db.execute("SELECT 1 FROM pg_indexes WHERE schemaname=current_schema() AND indexname='jobs_item'").fetchone()
    store.db.close()
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        with measure_reads(app.state.host.require_store().db, enabled=lambda: request_sql.get() is not None) as work:
            response = client.post('/api/v3/materials/' + items[0] + '/viewed')
        assert response.status_code == 200
        assert client.get('/api/v3/materials/' + items[0]).status_code == 404
        assert work.rows < 5000, work


def test_status_does_not_scan_completed_job_archive(tmp_path):
    from tests.support.storage import Store
    from tests.support.reading_cost import request_sql

    library(tmp_path, [1])
    store = Store(tmp_path, lambda _: None)
    with store.db:
        store.db.executemany("INSERT INTO jobs ( kind , item , status ) VALUES ( 'fetch' , %s , 'done' )", [(f'old-{n}',) for n in range(20000)])
        analyze(store.db)
    store.db.close()
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        with measure_reads(app.state.host.require_store().db, enabled=lambda: request_sql.get() is not None) as work:
            response = client.get('/api/v3/status')
        assert response.status_code == 200
        assert work.rows < 10000, work
