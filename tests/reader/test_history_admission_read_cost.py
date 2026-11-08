"""History outcomes reuse the locked admission facts during reader actions."""
from fastapi.testclient import TestClient

from oko.storage.transaction import transaction
from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app, tracing, wait_for_recovery
from tests.support.storage import Store


def test_viewing_history_member_locks_owner_and_reads_selection_once(tmp_path):
    feeds, items = library(tmp_path, [1])
    store = Store(tmp_path, lambda _: None)
    try:
        with transaction(store.db):
            store.subscriptions.history.enroll(feeds[0], items, True)
    finally:
        store.db.close()
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        tracing(client, app)
        response = client.post('/api/v3/cards/read', json={'ids': items})
        assert response.status_code == 204, response.text
        statements = list(app.state.last_sql)
        owner_locks = [q for q in statements if 'SELECT id FROM feeds WHERE id=ANY(%s) ORDER BY id FOR UPDATE' in q]
        admission_reads = [q for q in statements if 'FROM initial_history h LEFT JOIN feeds f ON f.id=h.feed' in q and 'FOR UPDATE OF h' in q]
        assert len(owner_locks) == 1
        assert len(admission_reads) == 1
        assert statements.index(owner_locks[0]) < statements.index(admission_reads[0])
        assert not any("SELECT feed,selection,pg_input_is_valid(selection,'jsonb')" in q for q in statements)
        def outcome():
            return app.state.host.require_store().db.execute(
                'SELECT outcome FROM history_members WHERE feed=%s AND item=%s', (feeds[0],items[0])).fetchone()[0]
        assert client.portal.call(outcome) == 'viewed'
