"""History repair must invalidate status and use bounded SQL during reconciliation."""
import psycopg
from fastapi.testclient import TestClient
from tests.support.reading_cost import library, traced_app, wait_for_recovery
from tests.support.reader import HEADERS
from tests.support.storage import Store
from oko.storage.reading import reading_snapshot
from oko.storage.transaction import transaction


def test_invalid_member_outcome_changes_conditional_status_in_both_directions(tmp_path):
    feeds, _ = library(tmp_path, [1])
    dsn = (tmp_path / '.test-database-url').read_text().strip()
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        healthy = client.get('/api/v3/status/details')
        assert healthy.status_code == 200
        with psycopg.connect(dsn, autocommit=True) as other:
            other.execute("INSERT INTO history_members(feed,item,outcome) VALUES (%s,'invalid-item','garbage')", (feeds[0],))
        damaged = client.get('/api/v3/status/details', headers={'If-None-Match':healthy.headers['ETag']})
        assert damaged.status_code == 200, damaged.text
        assert any(p['code']=='history_invalid' for p in damaged.json()['problems']['current'])
        with psycopg.connect(dsn, autocommit=True) as other:
            other.execute("UPDATE history_members SET outcome='pending' WHERE feed=%s AND item='invalid-item'", (feeds[0],))
        repaired = client.get('/api/v3/status/details', headers={'If-None-Match':damaged.headers['ETag']})
        assert repaired.status_code == 200, repaired.text
        assert not any(p['code']=='history_invalid' for p in repaired.json()['problems']['current'])


def test_reconcile_batches_rechecks_and_keeps_newly_damaged_feed(tmp_path):
    feeds, _ = library(tmp_path, [1,1,1,1])
    store = Store(tmp_path, lambda _: None)
    try:
        with store.db:
            originals = {row['feed']:row['selection'] for row in store.db.execute(
                'SELECT feed,selection FROM initial_history WHERE feed=ANY(%s)', (feeds,))}
            store.db.execute("UPDATE initial_history SET selection='{}' WHERE feed=ANY(%s)", (feeds,))
        with reading_snapshot():
            candidates = store.problems.candidates(store.wall_clock())
        store.problems.reconcile(candidates, store.wall_clock())
        with store.db:
            for feed in feeds:
                store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', (originals[feed],feed))
        with reading_snapshot():
            candidates = store.problems.candidates(store.wall_clock())
        with store.db:
            store.db.execute("UPDATE initial_history SET selection='{}' WHERE feed=%s", (feeds[0],))
        queries = []
        store.db.set_trace_callback(queries.append)
        try:
            result = store.problems.reconcile(candidates, store.wall_clock())
        finally:
            store.db.set_trace_callback(None)
        affected = {obj['id'] for p in result['current'] if p['code']=='history-invalid' for obj in p['objects']}
        assert affected == {feeds[0]}
        assert sum('SELECT h.*,pg_input_is_valid' in sql for sql in queries) == 1
    finally:
        store.db.close()


def test_replay_attempts_one_feed_per_pass_and_rotates_past_conflicts(tmp_path):
    import json
    feeds, items = library(tmp_path, [1,1])
    store = Store(tmp_path, lambda _: None)
    try:
        feeds_by_order = sorted(feeds)
        item_by_feed = dict(zip(feeds, items))
        states = ('pending','retrying','unavailable','loaded','incomplete','viewed')
        transition = json.dumps({state:'loaded' for state in states})
        with store.db:
            for index, feed in enumerate(feeds_by_order):
                item = item_by_feed[feed]
                run = store.db.execute('SELECT initial_history_run FROM feeds WHERE id=%s', (feed,)).fetchone()[0]
                store.db.execute("INSERT INTO history_members(feed,item,outcome) VALUES (%s,%s,'pending') ON CONFLICT(feed,item) DO UPDATE SET outcome='pending'", (feed,item))
                store.db.execute("INSERT INTO history_deferred_events(feed,item,run_id,base_outcome,transitions) VALUES (%s,%s,%s,'pending',%s::jsonb)",
                                 (feed,item,'different-run' if index==0 else run,transition))
        with transaction(store.db):
            first = store.problems.replay_history_batch(set(feeds))
        assert first == (False, True)
        with transaction(store.db):
            second = store.problems.replay_history_batch(set(feeds))
        assert second == (True, False)
        assert store.db.execute('SELECT feed FROM history_deferred_events').fetchone()[0] == feeds_by_order[0]
        assert store.db.execute('SELECT outcome FROM history_members WHERE feed=%s AND item=%s',
                                (feeds_by_order[1], item_by_feed[feeds_by_order[1]])).fetchone()[0] == 'loaded'
    finally:
        store.db.close()


def test_deferred_presence_invalidates_status_when_added_and_removed(tmp_path):
    import json
    feeds, items = library(tmp_path, [1])
    dsn = (tmp_path / '.test-database-url').read_text().strip()
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        healthy = client.get('/api/v3/status/details')
        assert healthy.status_code == 200
        with psycopg.connect(dsn, autocommit=True) as other:
            other.execute("INSERT INTO history_deferred_events(feed,item,run_id,base_outcome,transitions) VALUES (%s,%s,%s,'pending',%s::jsonb)",
                          (feeds[0],items[0],'conflict',json.dumps({'pending':'loaded'})))
        pending = client.get('/api/v3/status/details', headers={'If-None-Match':healthy.headers['ETag']})
        assert pending.status_code == 200, pending.text
        assert any(p['code']=='history_invalid' for p in pending.json()['problems']['current'])
        with psycopg.connect(dsn, autocommit=True) as other:
            other.execute('DELETE FROM history_deferred_events WHERE feed=%s', (feeds[0],))
        cleared = client.get('/api/v3/status/details', headers={'If-None-Match':pending.headers['ETag']})
        assert cleared.status_code == 200, cleared.text
        assert not any(p['code']=='history_invalid' for p in cleared.json()['problems']['current'])


def test_readding_deleted_feed_with_damaged_history_refuses_without_reset(tmp_path):
    feeds, _ = library(tmp_path, [1])
    app = traced_app(tmp_path)
    url = 'https://fixture.test/feed/0'
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        assert client.delete('/api/v3/feeds/' + feeds[0]).status_code == 200
        def damage_and_allow_check():
            store = app.state.host.require_store()
            with store.db:
                store.db.execute("UPDATE initial_history SET selection='{broken' WHERE feed=%s", (feeds[0],))
            app.state.application._feed_checks['synthetic'] = url
        client.portal.call(damage_and_allow_check)
        assert client.put('/api/v3/power', json={'enabled':True}).status_code == 200
        response = client.post('/api/v3/feeds', json={'url':url,'verification':'synthetic','title':'restored','folder':'Folder 0',
                                                      'initial_history':{'mode':'count','count':'20'}})
        assert response.status_code == 400, response.text
        assert 'Повреждена сохранённая история' in response.json()['message']
        def saved():
            store = app.state.host.require_store()
            return store.db.execute('SELECT f.deleted,h.selection FROM feeds f JOIN initial_history h ON h.feed=f.id WHERE f.id=%s',(feeds[0],)).fetchone()
        deleted, selection = client.portal.call(saved)
        assert deleted and selection == '{broken'


def test_deferred_replay_progresses_on_status_timer_while_work_disabled(tmp_path):
    import json
    from tests.support.reader import Clock
    feeds, items = library(tmp_path, [1])
    clock = Clock()
    app = traced_app(tmp_path, **clock.options())
    states = ('pending','retrying','unavailable','loaded','incomplete','viewed')
    transition = json.dumps({state:'loaded' for state in states})
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        def seed():
            store = app.state.host.require_store()
            with store.db:
                run = store.db.execute('SELECT initial_history_run FROM feeds WHERE id=%s',(feeds[0],)).fetchone()[0]
                store.db.execute("INSERT INTO history_members(feed,item,outcome) SELECT %s,'batch-'||i,'pending' FROM generate_series(1,205) i",(feeds[0],))
                store.db.execute("INSERT INTO history_deferred_events(feed,item,run_id,base_outcome,transitions) SELECT %s,'batch-'||i,%s,'pending',%s::jsonb FROM generate_series(1,205) i",(feeds[0],run,transition))
        client.portal.call(seed)
        first = client.get('/api/v3/status/details')
        assert first.status_code == 200, first.text
        count = lambda: app.state.host.require_store().db.execute('SELECT count(*) FROM history_deferred_events').fetchone()[0]
        assert client.portal.call(count) == 5
        unchanged = client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['ETag']})
        assert unchanged.status_code == 304
        clock.now += 5
        next_page = client.get('/api/v3/status/details',headers={'If-None-Match':unchanged.headers['ETag']})
        assert next_page.status_code == 200, next_page.text
        assert client.portal.call(count) == 0


def test_deferred_conflict_rechecks_after_member_repair_with_work_disabled(tmp_path):
    import json
    from tests.support.reader import Clock
    feeds, items = library(tmp_path, [1])
    clock = Clock()
    app = traced_app(tmp_path, **clock.options())
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        def seed():
            store = app.state.host.require_store()
            with store.db:
                run = store.db.execute('SELECT initial_history_run FROM feeds WHERE id=%s',(feeds[0],)).fetchone()[0]
                store.db.execute("INSERT INTO history_members(feed,item,outcome) VALUES (%s,%s,'retrying') ON CONFLICT(feed,item) DO UPDATE SET outcome='retrying'",(feeds[0],items[0]))
                store.db.execute("INSERT INTO history_deferred_events(feed,item,run_id,base_outcome,transitions) VALUES (%s,%s,%s,'pending',%s::jsonb)",
                                 (feeds[0],items[0],run,json.dumps({state:'loaded' for state in ('pending','retrying','unavailable','loaded','incomplete','viewed')})))
        client.portal.call(seed)
        pending = client.get('/api/v3/status/details')
        assert pending.status_code == 200, pending.text
        assert any(p['code']=='history_invalid' for p in pending.json()['problems']['current'])
        def repair():
            store=app.state.host.require_store()
            with store.db:
                store.db.execute("UPDATE history_members SET outcome='pending' WHERE feed=%s AND item=%s", (feeds[0],items[0]))
        client.portal.call(repair)
        assert client.get('/api/v3/status/details',headers={'If-None-Match':pending.headers['ETag']}).status_code == 304
        clock.now += 5
        fixed = client.get('/api/v3/status/details',headers={'If-None-Match':pending.headers['ETag']})
        assert fixed.status_code == 200, fixed.text
        assert not any(p['code']=='history_invalid' for p in fixed.json()['problems']['current'])


def test_deleted_feed_backlog_does_not_force_periodic_status_refresh(tmp_path):
    import json
    from tests.support.reader import Clock
    feeds, items = library(tmp_path, [1])
    clock = Clock()
    app = traced_app(tmp_path, **clock.options())
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        assert client.delete('/api/v3/feeds/' + feeds[0]).status_code == 200
        def seed():
            store = app.state.host.require_store()
            with store.db:
                store.db.execute("INSERT INTO history_deferred_events(feed,item,run_id,base_outcome,transitions) VALUES (%s,%s,'old','pending',%s::jsonb)",
                                 (feeds[0],items[0],json.dumps({state:state for state in ('pending','retrying','unavailable','loaded','incomplete','viewed')})))
        client.portal.call(seed)
        initial = client.get('/api/v3/status/details')
        assert initial.status_code == 200
        clock.now += 5
        refreshed = client.get('/api/v3/status/details', headers={'If-None-Match':initial.headers['ETag']})
        assert refreshed.status_code == 304
        def reactivate():
            store = app.state.host.require_store()
            with store.db:
                store.db.execute('UPDATE feeds SET deleted=false WHERE id=%s', (feeds[0],))
        client.portal.call(reactivate)
        active = client.get('/api/v3/status/details', headers={'If-None-Match':refreshed.headers['ETag']})
        assert active.status_code == 200, active.text
        assert any(p['code']=='history_invalid' for p in active.json()['problems']['current'])
