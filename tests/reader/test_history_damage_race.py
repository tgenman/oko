"""A status ETag must not hide a concurrent history change."""
import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.support.reading_cost import library, traced_app, wait_for_recovery
from tests.support.reader import HEADERS


@pytest.mark.parametrize('initially_damaged', [False, True], ids=['damage', 'repair'])
def test_status_revalidates_after_history_changes_during_projection(tmp_path, initially_damaged):
    feeds, _ = library(tmp_path, [1])
    dsn = (tmp_path / '.test-database-url').read_text().strip()
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        def install():
            store = app.state.host.require_store()
            row = store.db.execute('SELECT selection FROM initial_history WHERE feed=%s', (feeds[0],)).fetchone()
            assert row is not None
            original = row[0]
            if initially_damaged:
                with store.db:
                    store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{}', feeds[0]))
            previous = store.problems.candidates
            fired = False
            def interleave(now):
                nonlocal fired
                candidates = previous(now)
                if not fired:
                    fired = True
                    with psycopg.connect(dsn, autocommit=True) as other:
                        other.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',
                                      (original if initially_damaged else '{}', feeds[0]))
                return candidates
            store.problems.candidates = interleave
            return original, previous
        original, previous = client.portal.call(install)
        try:
            first = client.get('/api/v3/status/details')
            assert first.status_code == 200, first.text
            def restore_candidates():
                app.state.host.require_store().problems.candidates = previous
            client.portal.call(restore_candidates)
            refreshed = client.get('/api/v3/status/details', headers={'If-None-Match': first.headers['ETag']})
            assert refreshed.status_code == 200, refreshed.text
            histories = {value['feed_id']: value['history']['state'] for value in refreshed.json()['initial_histories']}
            assert (histories[feeds[0]] == 'damaged') is (not initially_damaged)
            current = refreshed.json()['problems']['current']
            assert any(problem['code'] == 'history_invalid' for problem in current) is (not initially_damaged)
        finally:
            def restore():
                store = app.state.host.require_store()
                store.problems.candidates = previous
                with store.db:
                    store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', (original, feeds[0]))
            client.portal.call(restore)
