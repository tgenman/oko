"""PostgreSQL receipt cleanup and scoped deletion through the reader HTTP boundary."""
import json
from fastapi.testclient import TestClient
from tests.support.storage import Store
from tests.support.reading_cost import library, traced_app, tracing, request_sql
from tests.support.reader import HEADERS
from tests.support.postgres_work import measure_reads


def test_postgres_restart_preserves_latest_receipts(tmp_path):
    _, items = library(tmp_path, [1])
    store = Store(tmp_path, lambda _: None)
    context = dict(operation='retry_material', material_id=items[0])
    other_context = dict(operation='power', enabled=False)
    with store.db:
        for name, saved_context, completed in [('old', context, 1), ('latest', context, 2), ('other', other_context, 3)]:
            identity = json.dumps(saved_context, sort_keys=True, separators=(',', ':'))
            body = json.dumps(dict(identity=identity, title='Synthetic', objects=[dict(kind='material', id=items[0])],
                                   action=None, discardable=False, command_context=saved_context))
            store.db.execute('INSERT INTO reader_action_results(id,identity,body,started,completed,result) VALUES (%s,%s,%s,%s,%s,%s)',
                             (name, identity, body, 0, completed, 'succeeded'))
    store.db.close()
    for _ in range(2):
        with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
            assert client.get('/api/v3/status/details').status_code == 200
            assert client.get('/api/v3/commands/old').json()['outcome'] == 'unknown'
            assert client.get('/api/v3/commands/latest').json()['outcome'] == 'succeeded'
            assert client.get('/api/v3/commands/other').json()['outcome'] == 'succeeded'
    with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
        assert client.post(f'/api/v3/materials/{items[0]}/viewed').status_code == 200
        assert client.get('/api/v3/commands/latest').json()['outcome'] == 'succeeded'
        problems = client.get('/api/v3/status/details').json()['problems']
        assert all(o.get('material_id') != items[0] for p in problems['current'] + problems['recent'] for o in p['affected'])


def test_viewing_does_not_scan_unrelated_receipt_bodies(tmp_path):
    _, items = library(tmp_path, [1])
    store = Store(tmp_path, lambda _: None)
    body = json.dumps(dict(identity='fixture', title='Synthetic', objects=[], action=None, discardable=False,
                           command_context=dict(operation='power', enabled=False)))
    with store.db:
        store.db.executemany('INSERT INTO reader_action_results(id,identity,body,started,completed,result) VALUES (%s,%s,%s,%s,%s,%s)',
                             [(f'old-{n}', f'identity-{n}', body, 0, 1, 'succeeded') for n in range(5000)])
    store.db.close()
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        with measure_reads(app.state.host.require_store().db, enabled=lambda: request_sql.get() is not None) as work:
            response = client.post(f'/api/v3/materials/{items[0]}/viewed')
        assert response.status_code == 200, response.text
        assert work.reads > 0
        assert work.rows < 2000, work


def test_receipt_cleanup_is_bounded_preserves_latest_and_rewinds_after_rollback(postgres_url, tmp_path):
    import pytest
    from oko.storage.transaction import transaction

    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    body = json.dumps({'objects':[]})
    try:
        rows = [('old', 'duplicate', body, 0, 0, 'succeeded')]
        rows += [(f'unique-{n}', f'unique-{n}', body, 0, n + 1, 'succeeded') for n in range(5000)]
        rows += [('new', 'duplicate', body, 0, 5001, 'succeeded')]
        with store.db:
            store.db.executemany('INSERT INTO reader_action_results(id,identity,body,started,completed,result) VALUES (%s,%s,%s,%s,%s,%s)', rows)
        from tests.support.storage import analyze
        from tests.support.postgres_work import scan_rows
        analyze(store.db)
        old_sequence = store.db.execute("SELECT sequence FROM reader_action_results WHERE id='old'").fetchone()[0]
        with pytest.raises(RuntimeError, match='plan rollback'), transaction(store.db):
            sql = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) DELETE FROM reader_action_results r WHERE r.sequence=ANY(%s) AND r.completed<=%s AND EXISTS (SELECT 1 FROM reader_action_results newer WHERE newer.identity=r.identity AND newer.sequence>r.sequence)"
            plan = store.db._db.execute(sql, ([old_sequence], 6000)).fetchone()[0][0]['Plan']
            assert scan_rows(plan) < 1000, plan
            raise RuntimeError('plan rollback')
        with pytest.raises(RuntimeError, match='rollback'), transaction(store.db):
            store.problems.cleanup_receipts(6000)
            assert store.db.execute("SELECT 1 FROM reader_action_results WHERE id='old'").fetchone() is None
            raise RuntimeError('rollback synthetic cleanup')
        assert store.db.execute("SELECT 1 FROM reader_action_results WHERE id='old'").fetchone() is not None
        for _ in range(26):
            with measure_reads(store.db) as work, transaction(store.db):
                store.problems.cleanup_receipts(6000)
            assert work.rows < 1000, work
        assert store.db.execute("SELECT 1 FROM reader_action_results WHERE id='old'").fetchone() is None
        assert store.db.execute("SELECT 1 FROM reader_action_results WHERE id='new'").fetchone() is not None
        assert store.db.execute("SELECT count(*) FROM reader_action_results").fetchone()[0] == 5001
    finally:
        store.db.close()


def test_status_get_does_not_run_receipt_retention(tmp_path, postgres_url):
    from oko.storage.transaction import transaction
    from tests.support.app import create_app

    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url)
    body = json.dumps({'objects':[]})
    with TestClient(app, headers=HEADERS) as client:
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        def seed():
            store = app.state.host.require_store()
            with transaction(store.db):
                store.read_versions._next_action_cleanup = float('inf')
                for name in ('old', 'new'):
                    store.db.execute("INSERT INTO reader_action_results(id,identity,body,started,completed,result) VALUES (%s,'fixture',%s,0,1,'succeeded')", (name,body))
        client.portal.call(seed)
        assert client.get('/api/v3/status/details').status_code == 200
        def exists():
            return app.state.host.require_store().db.execute("SELECT 1 FROM reader_action_results WHERE id='old'").fetchone() is not None
        assert client.portal.call(exists)
        client.portal.call(lambda: setattr(app.state.host.require_store().read_versions, '_next_action_cleanup', float('-inf')))
        from tests.support.reader import wait_for
        wait_for(client, lambda: not client.portal.call(exists))


def test_status_get_excludes_expired_problems_without_deleting_them(tmp_path, postgres_url):
    from oko.storage.transaction import transaction
    from tests.support.app import create_app
    from tests.support.reader import wait_for

    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url)
    body = json.dumps(dict(code='synthetic-error', title='Expired', consequence='Saved', next_step='None',
                           objects=[], action=None, state='current', next_at=None))
    with TestClient(app, headers=HEADERS) as client:
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        async def hold_housekeeping():
            await app.state.host.execution._actions.acquire()
        client.portal.call(hold_housekeeping)
        try:
            def seed():
                store = app.state.host.require_store()
                with transaction(store.db):
                    store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('expired','fixture','background',%s,0,1,'resolved')", (body,))
            client.portal.call(seed)
            response = client.get('/api/v3/status/details')
            assert response.status_code == 200, response.text
            assert not any(problem['id'] == 'expired' for problem in response.json()['problems']['recent'])
            def exists():
                return app.state.host.require_store().db.execute("SELECT 1 FROM reader_problems WHERE id='expired'").fetchone() is not None
            assert client.portal.call(exists)
        finally:
            client.portal.call(app.state.host.execution._actions.release)
        wait_for(client, lambda: not client.portal.call(exists))
