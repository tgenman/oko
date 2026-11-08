"""Accepted outcomes cannot be attributed to a different history run."""
import json

import psycopg
import pytest

from oko.storage.transaction import transaction
from tests.support.reading_cost import library
from tests.support.storage import Store, database_url


@pytest.fixture
def prepared(tmp_path):
    feeds, items = library(tmp_path, [1])
    store = Store(tmp_path, lambda _:None)
    feed, item = feeds[0], items[0]
    with transaction(store.db):
        store.db.execute("INSERT INTO history_members(feed,item,outcome) VALUES (%s,%s,'pending') ON CONFLICT(feed,item) DO UPDATE SET outcome='pending'", (feed,item))
    selection = store.db.execute('SELECT selection FROM initial_history WHERE feed=%s',(feed,)).fetchone()[0]
    yield store, feed, item, selection, database_url(tmp_path)
    store.db.close()


def events(store, feed):
    return [dict(row) for row in store.db.execute(
        'SELECT id,run_id,base_outcome,transitions FROM history_deferred_events WHERE feed=%s ORDER BY id', (feed,))]


def set_selection(dsn, feed, value):
    # Separate connection models operator repair; no application callback resets
    # or assigns a new history identity on the user's behalf.
    with psycopg.connect(dsn, autocommit=True) as external:
        external.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', (value,feed))


@pytest.mark.parametrize('replacement', ['different-run', 'missing-run'])
def test_later_outcomes_do_not_merge_into_old_run(prepared, replacement):
    store, feed, item, raw_a, dsn = prepared
    selection_a = json.loads(raw_a)
    run_a = selection_a['run']
    set_selection(dsn, feed, '{broken')
    with transaction(store.db):
        store.subscriptions.history.failed(item)
    first = events(store, feed)
    assert len(first) == 1 and first[0]['run_id'] == run_a
    assert first[0]['transitions']['pending'] == 'retrying'

    selection_b = dict(selection_a)
    if replacement == 'different-run':
        selection_b['run'] = 'synthetic-new-run'
    else:
        selection_b.pop('run')
    set_selection(dsn, feed, json.dumps(selection_b))
    with transaction(store.db):
        store.subscriptions.history.saved(item, True)
    separated = events(store, feed)
    assert len(separated) == 2
    assert separated[0] == first[0]
    assert separated[1]['run_id'] is None

    # Explicit viewing remains accepted and does not erase or mutate A's event.
    with transaction(store.db):
        assert store.viewed_set([item])
    assert store.db.execute('SELECT viewed FROM publications WHERE id=%s',(item,)).fetchone()[0] is True
    assert events(store, feed)[0] == first[0]

    set_selection(dsn, feed, raw_a)
    with transaction(store.db):
        progressed, pending = store.subscriptions.history.replay_deferred(feed)
    assert progressed and pending
    # B's saved and Viewed actions are retained unresolved, not misapplied to A.
    assert store.db.execute('SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item)).fetchone()[0] == 'retrying'
    remaining = events(store, feed)
    assert remaining and all(row['run_id'] is None for row in remaining)
    assert store.db.execute('SELECT viewed FROM publications WHERE id=%s',(item,)).fetchone()[0] is True


def test_missing_trusted_run_never_merges_unattributed_outcomes(prepared):
    store, feed, item, raw_a, dsn = prepared
    with transaction(store.db):
        store.db.execute('UPDATE feeds SET initial_history_run=NULL WHERE id=%s', (feed,))
        store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{broken',feed))
        store.subscriptions.history.failed(item)
        store.subscriptions.history.saved(item, True)
    retained = events(store, feed)
    assert len(retained) == 2 and all(row['run_id'] is None for row in retained)
    set_selection(dsn, feed, raw_a)
    with transaction(store.db):
        progressed, pending = store.subscriptions.history.replay_deferred(feed)
    assert not progressed and pending
    assert events(store, feed) == retained
    assert store.db.execute('SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item)).fetchone()[0] == 'pending'


def test_invalid_json_with_trusted_run_preserves_normal_coalescing(prepared):
    store, feed, item, raw_a, dsn = prepared
    run_a = json.loads(raw_a)['run']
    set_selection(dsn, feed, '{broken')
    with transaction(store.db):
        store.subscriptions.history.failed(item)
        store.subscriptions.history.saved(item, True)
    retained = events(store, feed)
    assert len(retained) == 1 and retained[0]['run_id'] == run_a
    set_selection(dsn, feed, raw_a)
    with transaction(store.db):
        progressed, pending = store.subscriptions.history.replay_deferred(feed)
    assert progressed and not pending
    assert store.db.execute('SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item)).fetchone()[0] == 'loaded'


@pytest.mark.parametrize('mode', ['selection-mismatch', 'marker-missing', 'selection-missing', 'both-missing'])
def test_http_detects_run_mismatch_without_backlog(prepared, tmp_path, mode):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from tests.support.reading_cost import wait_for_recovery

    store, feed, item, raw_a, dsn = prepared
    store.db.close()
    app = create_app(tmp_path, token='test', api_only=True, rss_enabled=False)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        first = client.get('/api/v3/status/details')
        assert first.status_code == 200, first.text
        assert not any(p['code']=='history_invalid' for p in first.json()['problems']['current'])
        altered = json.loads(raw_a)
        with psycopg.connect(dsn, autocommit=False) as external:
            if mode == 'selection-mismatch':
                altered['run'] = 'different-synthetic-run'
                external.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', (json.dumps(altered),feed))
            elif mode == 'marker-missing':
                # Only the owner marker changes: the history row's xmin remains
                # the same and an old ETag must still be invalidated.
                external.execute('UPDATE feeds SET initial_history_run=NULL WHERE id=%s', (feed,))
            else:
                altered.pop('run')
                external.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', (json.dumps(altered),feed))
                if mode == 'both-missing':
                    external.execute('UPDATE feeds SET initial_history_run=NULL WHERE id=%s', (feed,))
        response = client.get('/api/v3/status/details', headers={'If-None-Match':first.headers['ETag']})
        assert response.status_code == 200, response.text
        problems = [p for p in response.json()['problems']['current'] if p['code']=='history_invalid']
        if mode == 'both-missing':
            assert not problems
        else:
            assert len(problems) == 1
            assert any(obj.get('feed_id') == feed for obj in problems[0]['affected'])
        navigation = client.get('/api/v3/navigation')
        assert navigation.status_code == 200, navigation.text
        assert any(entry['id']==feed for folder in navigation.json()['folders'] for entry in folder['feeds'])


def test_invalid_error_member_is_rejected_by_every_history_reader(prepared):
    from oko.subscriptions.history import InvalidHistory, InvalidHistoryError
    store, feed, item, raw_a, dsn = prepared
    with transaction(store.db):
        store.db.execute("INSERT INTO history_members(feed,item,outcome) VALUES (%s,'','retrying')", (feed,))
        store.db.execute("INSERT INTO jobs(kind,item,status,reason_message) VALUES ('fetch','','failed','Synthetic error')")
    assert isinstance(store.subscriptions.history.statuses([feed])[feed], InvalidHistory)
    assert feed in store.subscriptions.history.invalid_feeds([feed])
    with pytest.raises(InvalidHistoryError):
        store.subscriptions.history.admission(feed)
    with pytest.raises(InvalidHistoryError):
        store.subscriptions.history.status(feed)
    # An otherwise valid item from the damaged history must retain its outcome.
    with transaction(store.db):
        store.subscriptions.history.saved(item, True)
    assert store.db.execute('SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item)).fetchone()[0] == 'pending'
    assert len(events(store,feed)) == 1


def test_mismatch_without_backlog_defers_new_progress_instead_of_mutating_history(prepared):
    store, feed, item, raw_a, dsn = prepared
    selection_b = json.loads(raw_a)
    selection_b['run'] = 'synthetic-new-run'
    set_selection(dsn, feed, json.dumps(selection_b))
    assert events(store, feed) == []
    with transaction(store.db):
        store.subscriptions.history.saved(item, True)
    assert store.db.execute('SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item)).fetchone()[0] == 'pending'
    deferred = events(store, feed)
    assert len(deferred) == 1 and deferred[0]['run_id'] is None
    assert store.db.execute('SELECT selection FROM initial_history WHERE feed=%s',(feed,)).fetchone()[0] == json.dumps(selection_b)


@pytest.mark.parametrize('method', ['admission', 'statuses'])
def test_locked_history_holds_owner_marker_until_commit(prepared, method):
    from oko.storage.memberships import lock_graph
    store, feed, item, raw_a, dsn = prepared
    with transaction(store.db):
        lock_graph(store.db)
        if method == 'admission':
            store.subscriptions.history.admission(feed, lock=True)
        else:
            store.subscriptions.history.statuses([feed], lock=True)
        with psycopg.connect(dsn, autocommit=True) as other:
            other.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                other.execute('UPDATE feeds SET initial_history_run=%s WHERE id=%s', ('other-run', feed))
    # The second connection is not permanently blocked after the owner commits.
    with psycopg.connect(dsn, autocommit=True) as other:
        assert other.execute('UPDATE feeds SET initial_history_run=%s WHERE id=%s', ('other-run', feed)).rowcount == 1


def test_locked_batch_takes_one_owner_lock_statement(tmp_path):
    from oko.storage.memberships import lock_graph
    feeds, _ = library(tmp_path, [1, 1, 1])
    store = Store(tmp_path, lambda _:None)
    queries = []
    try:
        store.db.set_trace_callback(queries.append)
        with transaction(store.db):
            lock_graph(store.db)
            result = store.subscriptions.history.statuses(feeds, lock=True)
        assert set(result) == set(feeds)
        assert sum('SELECT id FROM feeds WHERE id=ANY(%s) ORDER BY id FOR UPDATE' in q for q in queries) == 1
    finally:
        store.db.set_trace_callback(None)
        store.db.close()


def test_orphan_history_without_owner_is_never_treated_as_legacy(prepared):
    from oko.subscriptions.history import InvalidHistory, InvalidHistoryError
    store, feed, item, raw_a, _ = prepared
    orphan = 'synthetic-missing-feed'
    legacy = json.loads(raw_a)
    legacy.pop('run')
    with transaction(store.db):
        store.db.execute('INSERT INTO initial_history(feed,selection) VALUES (%s,%s)', (orphan,json.dumps(legacy)))
        store.db.execute("INSERT INTO history_members(feed,item,outcome) VALUES (%s,%s,'pending')", (orphan,item))
    assert isinstance(store.subscriptions.history.statuses([orphan])[orphan], InvalidHistory)
    assert orphan in store.subscriptions.history.invalid_feeds([orphan], require_rows=True)
    with pytest.raises(InvalidHistoryError):
        store.subscriptions.history.admission(orphan)
    with pytest.raises(InvalidHistoryError):
        store.subscriptions.history.status(orphan)
    with transaction(store.db):
        store.subscriptions.history.saved(item, True)
    assert store.db.execute('SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(orphan,item)).fetchone()[0] == 'pending'
    retained = events(store,orphan)
    assert len(retained) == 1 and retained[0]['run_id'] is None
    assert store.db.execute('SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item)).fetchone()[0] == 'loaded'


def test_conditional_status_changes_when_legacy_history_gets_its_owner(prepared, tmp_path):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from tests.support.reading_cost import wait_for_recovery
    store, feed, item, raw_a, dsn = prepared
    orphan = 'synthetic-missing-owner'
    legacy = json.loads(raw_a)
    legacy.pop('run')
    with transaction(store.db):
        store.db.execute('INSERT INTO initial_history(feed,selection) VALUES (%s,%s)', (orphan,json.dumps(legacy)))
    store.db.close()
    app = create_app(tmp_path, token='test', api_only=True, rss_enabled=False)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        first = client.get('/api/v3/status/details')
        assert first.status_code == 200, first.text
        assert all(entry['feed_id'] != orphan for entry in first.json()['initial_histories'])
        # Restore only an owner: the selection row's xmin and the normalized
        # legacy run marker (NULL) remain unchanged.
        with psycopg.connect(dsn, autocommit=True) as external:
            external.execute("""INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport)
                VALUES (%s,%s,'Synthetic restored owner','rss',(SELECT id FROM folders LIMIT 1),%s,'connected','rss')""",
                (orphan,'rss:https://fixture.invalid/restored',['https://fixture.invalid/restored']))
        updated = client.get('/api/v3/status/details', headers={'If-None-Match':first.headers['ETag']})
        assert updated.status_code == 200, updated.text
        assert any(entry['feed_id'] == orphan and entry['history']['state'] != 'damaged' for entry in updated.json()['initial_histories'])
