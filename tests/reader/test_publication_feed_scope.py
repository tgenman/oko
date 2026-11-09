"""Publication visibility does not depend on the owner's saved history."""
from fastapi.testclient import TestClient

from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app, tracing, wait_for_recovery


def test_publication_and_conditional_read_skip_history_but_hide_deleted_feed(tmp_path):
    feeds, items = library(tmp_path, [1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        tracing(client, app)

        # Fault injection affects only the disposable synthetic schema.
        def damage():
            store = app.state.host.require_store()
            with store.db:
                store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{broken', feeds[0]))
        client.portal.call(damage)

        first = client.get('/api/v3/publication/' + items[0])
        assert first.status_code == 200, first.text
        assert first.json()['id'] == items[0]
        queries = list(app.state.last_sql)
        assert not any('initial_history' in query.lower() or 'history_members' in query.lower() for query in queries)
        assert len(queries) <= 12, queries

        unchanged = client.get('/api/v3/publication/' + items[0], headers={'If-None-Match': first.headers['ETag']})
        assert unchanged.status_code == 304, unchanged.text
        queries = list(app.state.last_sql)
        assert not any('initial_history' in query.lower() or 'history_members' in query.lower() for query in queries)
        assert len(queries) <= 12, queries

        assert client.delete('/api/v3/feeds/' + feeds[0]).status_code == 200
        hidden = client.get('/api/v3/publication/' + items[0], headers={'If-None-Match': first.headers['ETag']})
        assert hidden.status_code == 404, hidden.text
