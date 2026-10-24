"""Legacy receipt cleanup and scoped deletion through the reader HTTP boundary."""
import json
from fastapi.testclient import TestClient
from oko.storage.store import Store
from tests.support.reading_cost import library, traced_app, tracing, request_sql
from tests.support.reader import HEADERS


def test_legacy_journal_migration_preserves_latest_receipts(tmp_path):
    _, items = library(tmp_path, [1])
    store = Store(tmp_path, lambda _: None)
    context = dict(operation='retry_material', material_id=items[0])
    body = json.dumps(dict(identity='fixture', title='Synthetic', objects=[dict(kind='material', id=items[0])],
                           action=None, discardable=False, command_context=context))
    with store.db:
        for name, identity, completed in [('old', 'fixture', 1), ('latest', 'fixture', 2), ('other', 'other', 3)]:
            store.db.execute('INSERT INTO reader_action_results(id,identity,body,started,completed,result) VALUES (?,?,?,?,?,?)',
                             (name, identity, body, 0, completed, 'succeeded'))
        # Recreate the pre-migration journal; only authoritative rows survive.
        for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'journal_%'").fetchall():
            store.db.execute('DROP TRIGGER "' + row[0] + '"')
        store.db.execute('DROP TABLE reader_material_links')
        store.db.execute('DROP TABLE reader_action_superseded')
        store.db.execute("DELETE FROM state WHERE key='journal-index-version'")
    store.db.close()
    for _ in range(2):
        with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
            assert client.get('/api/v3/status').status_code == 200
            assert client.get('/api/v3/commands/old').json()['outcome'] == 'unknown'
            assert client.get('/api/v3/commands/latest').json()['outcome'] == 'succeeded'
            assert client.get('/api/v3/commands/other').json()['outcome'] == 'succeeded'
    with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
        assert client.post(f'/api/v3/materials/{items[0]}/viewed').status_code == 200
        assert client.get('/api/v3/commands/latest').json()['outcome'] == 'succeeded'
        problems = client.get('/api/v3/status').json()['problems']
        assert all(o.get('material_id') != items[0] for p in problems['current'] + problems['recent'] for o in p['affected'])


def test_viewing_does_not_scan_unrelated_receipt_bodies(tmp_path):
    _, items = library(tmp_path, [1])
    store = Store(tmp_path, lambda _: None)
    body = json.dumps(dict(identity='fixture', title='Synthetic', objects=[], action=None, discardable=False,
                           command_context=dict(operation='power', enabled=False)))
    with store.db:
        store.db.executemany('INSERT INTO reader_action_results(id,identity,body,started,completed,result) VALUES (?,?,?,?,?,?)',
                             [(f'old-{n}', f'identity-{n}', body, 0, 1, 'succeeded') for n in range(5000)])
    store.db.close()
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        ticks = []
        def progress():
            if request_sql.get() is not None:
                ticks.append(1)
            return 0
        client.portal.call(lambda: app.state.runtime.require_store().db.set_progress_handler(progress, 100))
        response = client.post(f'/api/v3/materials/{items[0]}/viewed')
        assert response.status_code == 200, response.text
        assert len(ticks) * 100 < 50000, len(ticks) * 100
