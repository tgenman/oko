"""HTTP navigation needs feed facts, not initial-history progress."""
import pytest
from fastapi.testclient import TestClient

from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app, tracing, wait_for_recovery


def test_navigation_avoids_history_and_preserves_full_subscription_history(tmp_path):
    feeds, _ = library(tmp_path, [2, 1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        tracing(client, app)
        response = client.get('/api/v3/navigation')
        assert response.status_code == 200
        queries = list(app.state.last_sql)
        assert not any('initial_history' in query.lower() or 'history_members' in query.lower() for query in queries)
        unchanged = client.get('/api/v3/navigation', headers={'If-None-Match': response.headers['ETag']})
        assert unchanged.status_code == 304
        assert not any('initial_history' in query.lower() or 'history_members' in query.lower() for query in app.state.last_sql)
        composition = client.get('/api/v3/reading-composition')
        assert composition.status_code == 200
        histories = {entry['feed']['id']: entry['feed']['history']['state'] for entry in composition.json()['feeds']}
        assert set(histories) == set(feeds)
        assert all(state != 'absent' for state in histories.values())
        subscriptions = client.get('/api/v3/subscriptions')
        assert subscriptions.status_code == 200
        assert all(feed['history']['state'] != 'absent' for feed in subscriptions.json()['feeds'])


@pytest.mark.parametrize('broken', ['{}', '{broken',
    '{"mode":"count","count":20,"since":null,"requests":-1}',
    '{"mode":"count","count":20,"since":null,"discovery_error":"failed","discovery_retry_at":1e300}',
    '{"mode":"count","count":20,"since":null,"warnings":["\\u0000"]}',
    '{"mode":"count","count":20,"since":null,"warnings":["\\ud800"]}',
    pytest.param('{"mode":"count","count":20,"since":null,"warnings":' + '['*1200 + '0' + ']'*1200 + '}', id='deep-json')])
def test_navigation_can_read_feed_when_its_history_is_damaged(tmp_path, broken):
    feeds, _ = library(tmp_path, [2, 1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        def set_history(value):
            store = app.state.host.require_store()
            with store.db:
                row = store.db.execute('SELECT selection FROM initial_history WHERE feed=%s', (feeds[0],)).fetchone()
                assert row is not None
                store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', (value, feeds[0]))
                return row[0]
        original = client.portal.call(set_history, broken)
        try:
            response = client.get('/api/v3/navigation')
            assert response.status_code == 200
            assert {feed['id'] for folder in response.json()['folders'] for feed in folder['feeds']} == set(feeds)
            status = client.get('/api/v3/status')
            assert status.status_code == 200, status.text
            assert any(problem['feed_id'] == feeds[0] and 'историю' in problem['message'] for problem in status.json()['problems'])
            details = client.get('/api/v3/status/details')
            assert details.status_code == 200, details.text
            damaged = [problem for problem in details.json()['problems']['current'] if problem['code'] == 'history_invalid']
            assert len(damaged) == 1
            history_states = {entry['feed_id']: entry['history']['state'] for entry in details.json()['initial_histories']}
            assert history_states[feeds[0]] == 'damaged'
            assert history_states[feeds[1]] != 'damaged'
            assert client.get('/api/v3/status/details', headers={'If-None-Match': details.headers['ETag']}).status_code == 304
            catalog = client.get('/api/v3/subscriptions')
            assert catalog.status_code == 200, catalog.text
            histories = {feed['id']: feed['history']['state'] for feed in catalog.json()['feeds']}
            assert histories[feeds[0]] == 'damaged'
            assert histories[feeds[1]] != 'damaged'
            settings = client.get('/api/v3/settings/feed/' + feeds[0])
            assert settings.status_code == 200, settings.text
            assert settings.json()['feed']['history']['state'] == 'damaged'
            collection = client.get('/api/v3/collection-status')
            assert collection.status_code == 200, collection.text
            assert next(source['history'] for source in collection.json()['sources'] if source['feed_id'] == feeds[0]) == 'Saved history needs attention'
            def saved_selection():
                store = app.state.host.require_store()
                return store.db.execute('SELECT selection FROM initial_history WHERE feed=%s', (feeds[0],)).fetchone()[0]
            assert client.portal.call(saved_selection) == broken
        finally:
            client.portal.call(set_history, original)
        restored = client.get('/api/v3/status/details')
        assert restored.status_code == 200, restored.text
        assert not any(problem['code'] == 'history_invalid' for problem in restored.json()['problems']['current'])
        assert any(problem['code'] == 'history_invalid' and problem['outcome'] == 'resolved' for problem in restored.json()['problems']['recent'])


def test_restore_unread_is_preserved_and_not_shown_as_damage(tmp_path):
    import json

    feeds, _ = library(tmp_path, [1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        def set_restore():
            store = app.state.host.require_store()
            with store.db:
                row = store.db.execute('SELECT selection FROM initial_history WHERE feed=%s', (feeds[0],)).fetchone()
                selection = json.loads(row[0])
                selection['restore_unread'] = True
                value = json.dumps(selection)
                store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', (value, feeds[0]))
                return value
        saved = client.portal.call(set_restore)
        response = client.get('/api/v3/settings/feed/' + feeds[0])
        assert response.status_code == 200, response.text
        assert response.json()['feed']['history']['state'] not in ('damaged', 'absent')
        details = client.get('/api/v3/status/details')
        assert details.status_code == 200, details.text
        assert not any(p['code'] == 'history_invalid' for p in details.json()['problems']['current'])
        assert client.portal.call(lambda: app.state.host.require_store().db.execute(
            'SELECT selection FROM initial_history WHERE feed=%s', (feeds[0],)).fetchone()[0]) == saved

def test_missing_previously_damaged_history_is_not_silently_resolved(tmp_path):
    feeds, _ = library(tmp_path, [1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        def damage():
            store = app.state.host.require_store()
            with store.db:
                original = dict(store.db.execute('SELECT * FROM initial_history WHERE feed=%s', (feeds[0],)).fetchone())
                store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{}', feeds[0]))
                return original
        original = client.portal.call(damage)
        try:
            details = client.get('/api/v3/status/details')
            assert details.status_code == 200, details.text
            assert any(p['code'] == 'history_invalid' for p in details.json()['problems']['current'])
            def remove():
                store = app.state.host.require_store()
                with store.db:
                    store.db.execute('DELETE FROM initial_history WHERE feed=%s', (feeds[0],))
            client.portal.call(remove)
            details = client.get('/api/v3/status/details')
            assert details.status_code == 200, details.text
            assert any(p['code'] == 'history_invalid' for p in details.json()['problems']['current'])
            catalog = client.get('/api/v3/subscriptions')
            assert catalog.status_code == 200, catalog.text
            assert catalog.json()['feeds'][0]['history']['state'] == 'damaged'
        finally:
            def restore():
                store = app.state.host.require_store()
                with store.db:
                    store.db.execute('''INSERT INTO initial_history(feed,selection,frozen,paused,scan_complete)
                        VALUES (%s,%s,%s,%s,%s) ON CONFLICT(feed) DO UPDATE SET
                        selection=excluded.selection,frozen=excluded.frozen,paused=excluded.paused,scan_complete=excluded.scan_complete''',
                        (original['feed'], original['selection'], original['frozen'], original['paused'], original['scan_complete']))
            client.portal.call(restore)


def test_history_repair_between_problem_snapshot_and_reconcile_does_not_create_stale_problem(tmp_path):
    from oko.storage.reading import reading_snapshot
    from tests.support.storage import Store

    feeds, _ = library(tmp_path, [1])
    store = Store(tmp_path, lambda _: None)
    try:
        with store.db:
            original = store.db.execute('SELECT selection FROM initial_history WHERE feed=%s', (feeds[0],)).fetchone()[0]
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{}', feeds[0]))
        with reading_snapshot():
            candidates = store.problems.candidates(store.wall_clock())
        assert any(candidate['code'] == 'history-invalid' for candidate in candidates)
        with store.db:
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', (original, feeds[0]))
        snapshot = store.problems.reconcile(candidates, store.wall_clock())
        assert not any(problem['code'] == 'history-invalid' for problem in snapshot['current'])
    finally:
        store.db.close()
