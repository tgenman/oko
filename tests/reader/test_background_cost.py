from tests.support.storage import analyze
from tests.support.postgres_work import measure_reads
from tests.support.collection_acceptance import discovered
from tests.support.source import publication_packet
"""Cost and freshness observed through HTTP while the real reader loop runs."""
import time
from fastapi.testclient import TestClient
import pytest

from oko.collection.contracts import Material, Publication
from tests.support.storage import Store
from oko.subscriptions.subscriptions import AddFeed
from tests.support.app import create_app
from tests.support.reader import HEADERS


def library(root, count):
    store = Store(root, lambda _: None)
    for n in range(4):
        feed = store.subscriptions.add_feed(AddFeed(
            url='https://www.youtube.com/channel/UC' + str(n).zfill(22),
            title=f'Channel {n}', folder=f'Folder {n}', verification='synthetic'))['id']
        store.jobs.enqueue_discovery(feed)
        job = store.jobs.next_job(0, feeds=[feed])
        discovered(store,[publication_packet(Publication(str(i), f'https://example.org/{n}/{i}', i,
            teaser=Material(f'Video {i}', 'Useful source text', description='Synthetic')))
            for i in range(count)], '{}', job, feed)
    store.db.close()


@pytest.mark.parametrize('count', [26, 100])
def test_idle_reader_does_not_reconcile_entire_library(tmp_path, count):
    library(tmp_path, count)
    app = create_app(tmp_path, token='test', api_only=True)
    with TestClient(app, headers=HEADERS) as client:
        assert client.get('/api/v3/reading-composition').json()['count'] == 4 * count
        deadline = time.monotonic() + 60
        while app.state.host.require_store().processing.recovering:
            assert time.monotonic() < deadline, 'Initial PostgreSQL recovery did not finish'
            time.sleep(.02)
        queries = []
        client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(queries.append))
        # HTTP is ready before the initial reconciliation finishes. Observe its
        # completion at the database boundary before measuring steady-state cost;
        # the readiness/latency contract is tested separately with slow SQL.
        for _ in range(10):
            queries.clear()
            time.sleep(.65)
            if len(queries) < 1000 and not any(any(f'SELECT id FROM {table} WHERE viewed=0' in q for table in ('items', 'items_read', 'items_by_feed', 'unviewed_items_by_feed')) and 'feed_id=ANY(' not in q for q in queries):
                break
        else:
            raise AssertionError(f'Reader never reached bounded idle work: {len(queries)} queries; {[q for q in queries if "SELECT id FROM items WHERE viewed=0" in q]}')
        queries.clear()
        time.sleep(.65)
        client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(None))
        # Include background dispatch/presence work, independently of content size.
        assert len(queries) < 1000, len(queries)
        assert not any(any(f'SELECT id FROM {table} WHERE viewed=0' in q for table in ('items', 'items_read', 'items_by_feed', 'unviewed_items_by_feed')) and 'feed_id=ANY(' not in q for q in queries)


def test_feed_settings_preserve_other_accepted_filters(tmp_path):
    from tests.support.filters import transport
    from tests.support.reader import wait_for

    library(tmp_path, 1)
    calls = []
    app = create_app(tmp_path, token='test', api_only=True, ai_transport=transport(calls))
    with TestClient(app, headers=HEADERS) as client:
        from tests.support.presence import presence
        presence(client,'reader')
        assert client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True}).status_code == 200
        assert client.patch('/api/v3/settings/oko/global', json={'theses': False, 'processor': 'llamacpp', 'prompt_filters': 'Keep useful text'}).status_code == 200
        cards = wait_for(client, lambda: (v if len(v := client.get('/api/v3/cards').json()) == 4 and all(c['filter']['attempt']['state'] == 'ready' for c in v) else None))
        selected = cards[0]['feed_id']
        others = {c['id'] for c in cards if c['feed_id'] != selected}
        calls.clear()
        assert client.patch('/api/v3/settings/feed/' + selected, json={'prompt_filters': 'Keep all useful text'}).status_code == 200
        cards = client.get('/api/v3/cards').json()
        assert all(c['filter']['attempt']['state'] == 'ready' for c in cards if c['id'] in others)
        wait_for(client, lambda: all(c['filter']['attempt']['state'] == 'ready' for c in client.get('/api/v3/cards').json()))
        generated = [r for r in calls if r.url.path == '/v1/chat/completions']
        assert len(generated) == 1


@pytest.mark.parametrize('count', [5, 20, 50])
def test_telegram_reconciliation_scales_linearly(tmp_path, count):
    import json

    store = Store(tmp_path, lambda _: None)
    with store.db:
        store.db.execute("INSERT INTO folders VALUES ('folder','Synthetic',0)")
        for i in range(count):
            feed = f'{i:032x}'
            store.db.execute("INSERT INTO feeds ( id , identity , title , kind , folder_id , original_urls , connection , transport , telegram_peer , auto_read ) VALUES ( %s , %s , %s , 'telegram' , 'folder' , '[]' , 'connected' , 'telegram' , %s , 0 )", (feed, feed, feed, -1000000-i))
            store.db.execute('INSERT INTO telegram_bindings VALUES ( %s , %s )', (feed, json.dumps(dict(kind='client', role='personal', peer_id=str(-1000000-i)))))
            store.db.execute('INSERT INTO telegram_read_state ( feed , since ) VALUES ( %s , 0 )', (feed,))
    queries = []
    store.db.set_trace_callback(queries.append)
    store.jobs.sync_reads()
    store.db.set_trace_callback(None)
    assert len(queries) < 40 + count * 20, len(queries)
    store.db.close()


def test_pending_dispatch_ignores_completed_archive_and_keeps_priorities(tmp_path):
    store = Store(tmp_path, lambda _: None)
    with store.db:
        store.db.executemany("INSERT INTO jobs ( kind , item , status ) VALUES ( 'filter' , %s , 'done' )", [(f'old-{i}',) for i in range(20000)])
        store.db.execute("INSERT INTO jobs(kind,item) VALUES ('discover','')")
        store.db.execute("INSERT INTO jobs(kind,item) VALUES ('filter','processing')")
        store.db.execute("INSERT INTO jobs(kind,item) VALUES ('delete','removed')")
        store.db.execute("INSERT INTO jobs(kind,item,next) VALUES ('filter','later',10000)")
        analyze(store.db)
    with measure_reads(store.db) as work:
        ai = store.jobs.next_job(0, ai_only=True)
        deletion = store.jobs.next_job(0)
        discovery = store.jobs.next_job(0)
        assert store.jobs.next_job(0, ai_only=True) is None
    assert ai.kind == 'filter' and ai.item == 'processing'
    assert deletion.kind == 'delete' and discovery.kind == 'discover'
    assert work.rows < 3000, work
    store.db.close()


@pytest.mark.parametrize('saved_inputs', [0, 1])
def test_processing_backfill_does_not_scan_unrelated_completed_archive(tmp_path, saved_inputs):
    store = Store(tmp_path, lambda _: None)
    try:
        with store.db:
            store.db.executemany("INSERT INTO jobs ( kind , item , status ) VALUES ( 'fetch' , %s , 'done' )",
                                [(f'old-{n}',) for n in range(20000)])
            if saved_inputs:
                store.db.execute("INSERT INTO filter_inputs VALUES ('synthetic-saved-input','synthetic-signature')")
            analyze(store.db)
        with measure_reads(store.db) as work:
            store.jobs.backfill_processing_inputs()
        assert work.rows < 1000, 'Backfill scanned completed jobs without matching saved inputs'
    finally:
        store.db.close()




def test_progress_writes_do_not_rebuild_source_configuration(tmp_path):
    library(tmp_path, 1)
    store = Store(tmp_path, lambda _: None)
    feed = store.subscriptions.feeds()[0]['id']
    with store.db:
        store.db.execute("INSERT INTO telegram_profiles VALUES ('personal','synthetic',101,1,'ready',0,NULL)")
        store.db.execute("INSERT INTO initial_history(feed,selection) VALUES (%s,%s) ON CONFLICT(feed) DO UPDATE SET selection=excluded.selection,frozen=excluded.frozen,paused=excluded.paused,scan_complete=excluded.scan_complete", (feed, '{"mode":"count","count":20,"since":null,"date":null,"timezone":"UTC"}'))
    store.jobs.sync_reads()
    with store.db:
        store.db.execute("UPDATE feeds SET title=title")
        store.db.execute("UPDATE telegram_profiles SET checked=10,message='checked'")
        store.db.execute("UPDATE initial_history SET selection=jsonb_set(selection::jsonb,'{warnings}','[\"progress\"]'::jsonb)::text")
    queries = []
    store.db.set_trace_callback(queries.append)
    store.jobs.sync_reads()
    store.db.set_trace_callback(None)
    assert not any('FROM feeds' in q for q in queries), queries
    with store.db:
        store.db.execute("UPDATE telegram_profiles SET user_id=202,state='unavailable'")
    queries.clear()
    store.db.set_trace_callback(queries.append)
    store.jobs.sync_reads()
    store.db.set_trace_callback(None)
    assert any('FROM feeds' in q for q in queries)
    store.db.close()
