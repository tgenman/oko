"""Preserve ordered history outcomes while the stored selection is damaged."""
import json
import pytest
from tests.support.reading_cost import library
from tests.support.storage import Store
from oko.storage.transaction import transaction


def prepared(tmp_path):
    feeds, items = library(tmp_path, [1])
    store = Store(tmp_path, lambda _: None)
    feed, item = feeds[0], items[0]
    with transaction(store.db):
        store.db.execute("INSERT INTO history_members(feed,item,outcome) VALUES (%s,%s,'pending') ON CONFLICT(feed,item) DO UPDATE SET outcome='pending'", (feed,item))
    selection = store.db.execute('SELECT selection FROM initial_history WHERE feed=%s',(feed,)).fetchone()[0]
    return store, feed, item, selection


def value(store, query, params=()):
    return store.db.execute(query,params).fetchone()[0]


def test_deferred_outcomes_survive_rollback_restart_and_repair(tmp_path):
    store, feed, item, selection = prepared(tmp_path)
    with transaction(store.db):
        store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{bad',feed))
    with pytest.raises(RuntimeError):
        with transaction(store.db):
            store.subscriptions.history.saved(item,False)
            raise RuntimeError('rollback')
    assert value(store,'SELECT count(*) FROM history_deferred_events') == 0
    with transaction(store.db):
        store.subscriptions.history.saved(item,False)
        store.subscriptions.history.viewed(item)
        store.subscriptions.history.saved(item,True)
        store.subscriptions.history.viewed(item)
    assert value(store,'SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item)) == 'pending'
    assert value(store,'SELECT count(*) FROM history_deferred_events') == 1
    store.db.close()
    store = Store(tmp_path, lambda _: None)
    try:
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',(selection,feed))
            from oko.subscriptions.history import InvalidHistory, InvalidHistoryError
            assert isinstance(store.subscriptions.history.statuses([feed])[feed],InvalidHistory)
            with pytest.raises(InvalidHistoryError):
                store.subscriptions.history.admission(feed)
            progressed, pending = store.subscriptions.history.replay_deferred(feed)
        assert progressed and not pending
        assert value(store,'SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item)) == 'loaded'
    finally:
        store.db.close()


@pytest.mark.parametrize(('first','last','expected'), [
    (False, True, 'loaded'),
    (True, False, 'incomplete'),
])
def test_last_accepted_save_determines_deferred_outcome(tmp_path, first, last, expected):
    store, feed, item, selection = prepared(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{bad',feed))
            store.subscriptions.history.saved(item,first)
            store.subscriptions.history.viewed(item)
            store.subscriptions.history.saved(item,last)
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',(selection,feed))
            progressed, pending = store.subscriptions.history.replay_deferred(feed)
        assert progressed and not pending
        assert value(store,'SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item)) == expected
    finally:
        store.db.close()


@pytest.mark.parametrize(('initial','expected'), [
    ('retrying','viewed'),
    ('unavailable','unavailable'),
])
def test_view_preserves_existing_retry_and_unavailable_semantics(tmp_path, initial, expected):
    store, feed, item, selection = prepared(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute('UPDATE history_members SET outcome=%s WHERE feed=%s AND item=%s',(initial,feed,item))
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{bad',feed))
            assert store.viewed_set([item])
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',(selection,feed))
            progressed, pending = store.subscriptions.history.replay_deferred(feed)
        assert progressed and not pending
        assert value(store,'SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item)) == expected
    finally:
        store.db.close()


def test_replay_preserves_preexisting_failed_transition(tmp_path):
    store, feed, item, selection = prepared(tmp_path)
    try:
        run = json.loads(selection)['run']
        previous = dict(pending='retrying', retrying='retrying', unavailable='unavailable',
                        loaded='loaded', incomplete='incomplete', viewed='viewed')
        with transaction(store.db):
            store.db.execute('''INSERT INTO history_deferred_events(feed,item,run_id,base_outcome,transitions)
                VALUES (%s,%s,%s,'pending',%s::jsonb)''', (feed,item,run,json.dumps(previous)))
            progressed, pending = store.subscriptions.history.replay_deferred(feed)
        assert progressed and not pending
        assert value(store,'SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item)) == 'retrying'
    finally:
        store.db.close()


@pytest.mark.parametrize('identity', ['missing', 'mismatch'])
def test_unknown_or_changed_run_never_consumes_event(tmp_path, identity):
    store, feed, item, selection = prepared(tmp_path)
    try:
        with transaction(store.db):
            if identity=='missing':
                store.db.execute('UPDATE feeds SET initial_history_run=NULL WHERE id=%s',(feed,))
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{bad',feed))
            store.subscriptions.history.saved(item,True)
        fixed=json.loads(selection)
        if identity=='mismatch':fixed['run']='different-run'
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',(json.dumps(fixed),feed))
            progressed, pending=store.subscriptions.history.replay_deferred(feed)
        assert not progressed and pending
        assert value(store,'SELECT count(*) FROM history_deferred_events')==1
        assert value(store,'SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item))=='pending'
    finally:
        store.db.close()


def test_conflict_does_not_block_other_items_across_pages(tmp_path):
    store, feed, item, selection = prepared(tmp_path)
    try:
        run=json.loads(selection)['run']
        states=('pending','retrying','unavailable','loaded','incomplete','viewed')
        transition=json.dumps({state:'loaded' for state in states})
        with transaction(store.db):
            store.db.execute("INSERT INTO history_members(feed,item,outcome) SELECT %s,'bulk-'||i,'pending' FROM generate_series(1,205) i",(feed,))
            store.db.execute("INSERT INTO history_deferred_events(feed,item,run_id,base_outcome,transitions) VALUES (%s,%s,%s,'pending',%s::jsonb)",(feed,item,'wrong-run',transition))
            store.db.execute("INSERT INTO history_deferred_events(feed,item,run_id,base_outcome,transitions) SELECT %s,'bulk-'||i,%s,'pending',%s::jsonb FROM generate_series(1,205) i",(feed,run,transition))
            store.db.execute("INSERT INTO history_deferred_events(feed,item,run_id,base_outcome,transitions) VALUES (%s,%s,%s,'pending',%s::jsonb)",(feed,item,run,transition))
        with transaction(store.db):
            progressed,pending=store.subscriptions.history.replay_deferred(feed)
        assert progressed and pending
        store.db.close()
        store=Store(tmp_path,lambda _:None)
        with transaction(store.db):
            progressed,pending=store.subscriptions.history.replay_deferred(feed)
        assert progressed and pending
        assert value(store,"SELECT count(*) FROM history_members WHERE feed=%s AND item LIKE 'bulk-%%' AND outcome='loaded'",(feed,))==205
        assert value(store,'SELECT count(*) FROM history_deferred_events WHERE feed=%s',(feed,))==2
        assert value(store,'SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item))=='pending'
        with transaction(store.db):
            progressed,pending=store.subscriptions.history.replay_deferred(feed)
        assert not progressed and pending
    finally:
        store.db.close()


def test_viewed_publication_commits_while_damaged_history_waits(tmp_path):
    store, feed, item, selection = prepared(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{bad',feed))
        with transaction(store.db):
            assert store.viewed_set([item])
        assert value(store,'SELECT viewed FROM publications WHERE id=%s',(item,)) is True
        assert value(store,'SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item))=='pending'
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',(selection,feed))
            progressed,pending=store.subscriptions.history.replay_deferred(feed)
        assert progressed and not pending
        assert value(store,'SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item))=='viewed'
    finally:
        store.db.close()


@pytest.mark.parametrize('manual,expected_pending,expected', [
    ('retrying', True, 'retrying'),
    ('viewed', False, 'viewed'),
    ('loaded', False, 'loaded'),
])
def test_replay_never_overwrites_manual_member_repair(tmp_path, manual, expected_pending, expected):
    store, feed, item, selection = prepared(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{bad',feed))
            store.subscriptions.history.saved(item,True)
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',(selection,feed))
            store.db.execute('UPDATE history_members SET outcome=%s WHERE feed=%s AND item=%s',(manual,feed,item))
            progressed,pending=store.subscriptions.history.replay_deferred(feed)
        assert pending is expected_pending
        assert progressed is not expected_pending
        assert value(store,'SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item))==expected
    finally:
        store.db.close()


def test_hidden_deleted_feed_does_not_replay_or_discard_events(tmp_path):
    store, feed, item, selection = prepared(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{bad',feed))
            store.subscriptions.history.saved(item,True)
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',(selection,feed))
            store.db.execute('UPDATE feeds SET deleted=true WHERE id=%s',(feed,))
            assert store.subscriptions.history.replay_deferred(feed)==(False,False)
        assert value(store,'SELECT count(*) FROM history_deferred_events WHERE feed=%s',(feed,))==1
        with transaction(store.db):
            store.db.execute('UPDATE feeds SET deleted=false,initial_history_run=%s WHERE id=%s',('new-run',feed))
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',(json.dumps({**json.loads(selection),'run':'new-run'}),feed))
            assert store.subscriptions.history.replay_deferred(feed)==(False,True)
        assert value(store,'SELECT count(*) FROM history_deferred_events WHERE feed=%s',(feed,))==1
    finally:
        store.db.close()


def test_invalid_original_member_outcome_cannot_be_guessed_after_repair(tmp_path):
    store, feed, item, _ = prepared(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute('UPDATE history_members SET outcome=%s WHERE feed=%s AND item=%s',('broken',feed,item))
            store.subscriptions.history.saved(item,True)
        with transaction(store.db):
            store.db.execute('UPDATE history_members SET outcome=%s WHERE feed=%s AND item=%s',('pending',feed,item))
            assert store.subscriptions.history.replay_deferred(feed)==(False,True)
        assert value(store,'SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item))=='pending'
        assert value(store,'SELECT count(*) FROM history_deferred_events WHERE feed=%s',(feed,))==1
    finally:
        store.db.close()
