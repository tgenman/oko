"""Cost and freshness observed through HTTP while the real reader loop runs."""
import time
from fastapi.testclient import TestClient
import pytest

from oko.collection.contracts import Material, Publication
from oko.storage.store import Store
from oko.subscriptions.subscriptions import AddFeed
from tests.support.app import create_app
from tests.support.reader import HEADERS


def library(root, count):
    store = Store(root, lambda _: None)
    for n in range(4):
        feed = store.subscriptions.add_feed(AddFeed(
            url='https://www.youtube.com/channel/UC' + str(n).zfill(22),
            title=f'Channel {n}', folder=f'Folder {n}', verification='synthetic'))['id']
        store.enqueue_discovery(feed)
        job = store.next_job(0, feeds=[feed])
        store.discovered([Publication(str(i), f'https://example.org/{n}/{i}', i,
            teaser=Material(f'Video {i}', '', captions='not_requested', description='Synthetic'))
            for i in range(count)], '{}', job, feed)
    store.db.close()


@pytest.mark.parametrize('count', [26, 100])
def test_idle_reader_does_not_reconcile_entire_library(tmp_path, count):
    library(tmp_path, count)
    app = create_app(tmp_path, token='test', api_only=True)
    with TestClient(app, headers=HEADERS) as client:
        assert client.get('/api/v3/reading-composition').json()['count'] == 4 * count
        time.sleep(.15)
        queries = []
        client.portal.call(lambda: app.state.runtime.require_store().db.set_trace_callback(queries.append))
        time.sleep(.65)
        client.portal.call(lambda: app.state.runtime.require_store().db.set_trace_callback(None))
        # Include background dispatch/presence work, independently of content size.
        assert len(queries) < 1000, len(queries)
        assert not any('SELECT id FROM items WHERE viewed=0' in q for q in queries)


def test_feed_settings_preserve_other_accepted_filters(tmp_path):
    from tests.support.filters import transport
    from tests.support.reader import wait_for

    library(tmp_path, 1)
    calls = []
    app = create_app(tmp_path, token='test', api_only=True, ai_transport=transport(calls))
    with TestClient(app, headers=HEADERS) as client:
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
            store.db.execute("INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport,telegram_peer,auto_read) VALUES (?,?,?,'telegram','folder','[]','connected','telegram',?,0)", (feed, feed, feed, -1000000-i))
            store.db.execute('INSERT INTO telegram_bindings VALUES (?,?)', (feed, json.dumps(dict(kind='client', role='personal', peer_id=str(-1000000-i)))))
            store.db.execute('INSERT INTO telegram_read_state(feed,since) VALUES (?,0)', (feed,))
    queries = []
    store.db.set_trace_callback(queries.append)
    store.read_sync.sync()
    store.db.set_trace_callback(None)
    assert len(queries) < 40 + count * 20, len(queries)
    store.db.close()


def test_pending_dispatch_ignores_completed_archive_and_keeps_priorities(tmp_path):
    store = Store(tmp_path, lambda _: None)
    with store.db:
        store.db.executemany("INSERT INTO jobs(kind,item,status) VALUES ('fetch',?,'done')", [(f'old-{i}',) for i in range(20000)])
        store.db.execute("INSERT INTO jobs(kind,item) VALUES ('discover','')")
        store.db.execute("INSERT INTO jobs(kind,item) VALUES ('filter','processing')")
        store.db.execute("INSERT INTO jobs(kind,item) VALUES ('delete','removed')")
        store.db.execute("INSERT INTO jobs(kind,item,next) VALUES ('filter','later',10000)")
        store.db.execute('ANALYZE')
    steps = []
    store.db.set_progress_handler(lambda: steps.append(1) or 0, 1)
    ai = store.next_job(0, ai_only=True)
    deletion = store.next_job(0)
    discovery = store.next_job(0)
    assert store.next_job(0, ai_only=True) is None
    store.db.set_progress_handler(None, 0)
    assert ai.kind == 'filter' and ai.item == 'processing'
    assert deletion.kind == 'delete' and discovery.kind == 'discover'
    assert len(steps) < 3000, len(steps)
    store.db.close()


@pytest.mark.parametrize('interrupted', [False, True])
def test_legacy_filter_upgrade_keeps_result_and_origin_without_model_call(tmp_path, interrupted):
    import hashlib
    import json
    from tests.support.filters import FilterSource, transport, setup_filters
    from tests.support.reader import wait_for

    calls = []
    options = dict(token='test', api_only=True, source=FilterSource(), ai_transport=transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        setup_filters(client)
        cards = wait_for(client, lambda: (v if len(v := client.get('/api/v3/cards').json()) == 2 and all(c['filter']['attempt']['state'] == 'ready' for c in v) else None))
        identity = cards[0]['id']
        accepted = client.get('/api/v3/materials/' + identity).json()['filter']['result']
    store = Store(tmp_path, lambda _: None)
    feed = store.item_feed(identity)
    settings = store.subscriptions.effective('feed', feed)
    settings['feed']['telegram_topic'] = None
    # Persisted filter format of base938018e, independent of the new v2 inputs.
    signature = hashlib.sha256(json.dumps([store.content_version(identity), settings,
        store.settings_version(), store.processor_config().model_dump()], sort_keys=True).encode()).hexdigest()
    path = store.path(identity).parent / 'filter.md'
    raw, text = path.read_text().split('\n\n', 1)
    header = json.loads(raw)
    header['signature'] = signature
    path.write_text(json.dumps(header) + '\n\n' + text)
    with store.db:
        store.db.execute('UPDATE filter_inputs SET signature=? WHERE item=?', (signature, identity))
    if interrupted:
        def fault(point):
            if point == 'after_filter_move':
                raise OSError('Interrupted signature upgrade')
        store.fault = fault
        with pytest.raises(OSError, match='Interrupted signature upgrade'):
            store.processing.recover()
    store.db.close()
    calls.clear()
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/materials/' + identity).json()['filter']['result'] == accepted
        assert not [r for r in calls if r.url.path == '/v1/chat/completions']
