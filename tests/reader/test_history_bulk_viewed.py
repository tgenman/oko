"""Large read actions update every feed history atomically."""
import pytest
from fastapi.testclient import TestClient

from oko.storage.transaction import transaction
from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app, wait_for_recovery
from tests.support.storage import Store


@pytest.mark.parametrize('reject_second_batch', [False, True], ids=['commits', 'rolls-back'])
def test_bulk_viewed_across_history_batches_and_feeds(tmp_path, reject_second_batch):
    feeds, _ = library(tmp_path, [1, 1])
    store = Store(tmp_path, lambda _: None)
    try:
        with transaction(store.db):
            store.db.execute("""INSERT INTO publications(id,url,platform,external_id,feed_id)
                SELECT 'bulk-'||lpad(n::text,4,'0'), 'https://fixture.test/'||n,
                       'other_web', 'bulk-'||n, CASE WHEN n%%2=0 THEN %s ELSE %s END
                FROM generate_series(1,410) n""", (feeds[0],feeds[1]))
            store.db.execute("""INSERT INTO history_members(feed,item,outcome)
                SELECT feed_id,id,'pending' FROM publications WHERE id LIKE 'bulk-%%'""")
    finally:
        store.db.close()
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        wait_for_recovery(app)
        def setup_failure():
            current=app.state.host.require_store()
            with current.db.access_lock:
                current.db._db.execute("""CREATE FUNCTION pg_temp.reject_second_history_batch() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN IF NEW.item='bulk-0405' THEN
                    RAISE EXCEPTION 'synthetic second-batch failure'; END IF; RETURN NEW; END $$""")
                current.db._db.execute('CREATE TRIGGER synthetic_history_failure BEFORE UPDATE ON history_members FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_second_history_batch()')
        def snapshot():
            current=app.state.host.require_store()
            rows=current.db.execute("""SELECT p.feed_id,p.viewed,h.outcome,count(*) AS n FROM publications p
                JOIN history_members h ON h.feed=p.feed_id AND h.item=p.id WHERE p.id LIKE 'bulk-%%'
                GROUP BY p.feed_id,p.viewed,h.outcome ORDER BY p.feed_id""").fetchall()
            return [(row['feed_id'],row['viewed'],row['outcome'],row['n']) for row in rows]
        if reject_second_batch:
            client.portal.call(setup_failure)
        response=client.post('/api/v3/cards/read',json={'ids':[f'bulk-{n:04d}' for n in range(1,411)]})
        assert response.status_code == (500 if reject_second_batch else 204), response.text
        if reject_second_batch:
            assert 'synthetic second-batch failure' in client.get('/api/v3/diagnostics').text
        rows=client.portal.call(snapshot)
        expected_viewed=not reject_second_batch
        expected_outcome='pending' if reject_second_batch else 'viewed'
        assert rows==[(feed,expected_viewed,expected_outcome,205) for feed in sorted(feeds)]


def test_bulk_viewed_defers_only_damaged_history_and_replays_in_two_batches(tmp_path):
    feeds, _ = library(tmp_path, [1, 1])
    damaged, healthy = feeds
    store = Store(tmp_path, lambda _: None)
    try:
        with transaction(store.db):
            store.db.execute("""INSERT INTO publications(id,url,platform,external_id,feed_id)
                SELECT 'deferred-'||lpad(n::text,4,'0'), 'https://fixture.test/deferred/'||n,
                       'other_web', 'deferred-'||n, CASE WHEN n%%2=0 THEN %s ELSE %s END
                FROM generate_series(1,410) n""", (damaged,healthy))
            store.db.execute("""INSERT INTO history_members(feed,item,outcome)
                SELECT feed_id,id,'pending' FROM publications WHERE id LIKE 'deferred-%%'""")
            original=store.db.execute('SELECT selection FROM initial_history WHERE feed=%s',(damaged,)).fetchone()[0]
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',('{broken',damaged))
        before_row=dict(store.db.execute('SELECT * FROM initial_history WHERE feed=%s',(damaged,)).fetchone())
        before_members=[tuple(row) for row in store.db.execute(
            'SELECT item,outcome FROM history_members WHERE feed=%s ORDER BY item',(damaged,))]
        assert len(before_members)==205
    finally:
        store.db.close()

    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        wait_for_recovery(app)
        response=client.post('/api/v3/cards/read',json={'ids':[f'deferred-{n:04d}' for n in range(1,411)]})
        assert response.status_code==204, response.text

        def verify_and_replay():
            current=app.state.host.require_store()
            row=current.db.execute('SELECT * FROM initial_history WHERE feed=%s',(damaged,)).fetchone()
            members=current.db.execute('SELECT item,outcome FROM history_members WHERE feed=%s ORDER BY item',(damaged,)).fetchall()
            assert dict(row)==before_row
            assert [tuple(member) for member in members]==before_members
            assert current.db.execute("SELECT count(*) FROM publications WHERE id LIKE 'deferred-%%' AND viewed").fetchone()[0]==410
            assert current.db.execute("SELECT count(*) FROM history_members WHERE feed=%s AND outcome='viewed'",(healthy,)).fetchone()[0]==205
            assert current.db.execute('SELECT count(*) FROM history_deferred_events WHERE feed=%s',(damaged,)).fetchone()[0]==205
            with transaction(current.db):
                current.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',(original,damaged))
                progressed,pending=current.subscriptions.history.replay_deferred(damaged)
            assert progressed and pending
            assert current.db.execute("SELECT count(*) FROM history_members WHERE feed=%s AND outcome='viewed'",(damaged,)).fetchone()[0]==200
            assert current.db.execute('SELECT count(*) FROM history_deferred_events WHERE feed=%s',(damaged,)).fetchone()[0]==5
            with transaction(current.db):
                progressed,pending=current.subscriptions.history.replay_deferred(damaged)
            assert progressed and not pending
            assert current.db.execute("SELECT count(*) FROM history_members WHERE feed=%s AND outcome='viewed'",(damaged,)).fetchone()[0]==205
            assert current.db.execute('SELECT count(*) FROM history_deferred_events WHERE feed=%s',(damaged,)).fetchone()[0]==0
        client.portal.call(verify_and_replay)
