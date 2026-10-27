"""Collection progress and original request diagnostics through the reader HTTP interface."""
from tests.support.cards import read_all_cards
import threading
import asyncio
from fastapi.testclient import TestClient
from oko.collection.demo import DemoSource
from tests.support.app import create_app
from tests.support.presence import presence
from tests.support.reader import wait_for, HEADERS


def test_collection_attempt_is_shared_and_refresh_does_not_restart_it(tmp_path):
    started, release = threading.Event(), threading.Event()

    class SlowSource(DemoSource):
        async def discover(self, cursor):
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return await super().discover(cursor)

    with TestClient(create_app(tmp_path, token='test', source=SlowSource(), api_only=True), headers=HEADERS) as client:
        presence(client, 'reader')
        assert started.wait(3)
        response = client.get('/api/v3/collection-status')
        assert response.status_code == 200, response.text
        state = response.json()
        source = next(s for s in state['sources'] if s['feed_id'] == 'demo')
        assert source['state'] == 'collecting'
        assert source['operation']['kind'] == 'discover'
        attempt = source['operation']['attempt_id']
        assert attempt
        assert source['operation']['elapsed_seconds'] >= 0
        assert client.post('/api/v3/collect').status_code == 200
        again = client.get('/api/v3/collection-status').json()
        assert again['sources'][0]['operation']['attempt_id'] == attempt
        details = client.get('/api/v3/collection-status/demo').json()
        assert details['operations'][0]['attempt_id'] == attempt
        assert details['instance_id'] == state['instance_id']
        assert details['revision'] >= state['revision']
        release.set()
        wait_for(client, lambda: all(s['state'] == 'complete' for s in client.get('/api/v3/collection-status').json()['sources']))
        assert client.get('/api/v3/collection-status/missing').status_code == 404


def test_collection_wait_keeps_error_across_power_restart_and_resolves_on_success(tmp_path):
    from oko.collection.contracts import SourceError
    from tests.support.reader import Clock
    clock = Clock()
    failed = threading.Event()

    class RetryingSource(DemoSource):
        async def discover(self, cursor):
            if not failed.is_set():
                failed.set()
                raise SourceError('temporary source failure', delay=60)
            return await super().discover(cursor)

    options = dict(token='test', source=RetryingSource(), clock=clock, wall_clock=lambda:1800000000+clock.now, api_only=True)
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        source = wait_for(client, lambda: next((s for s in client.get('/api/v3/collection-status').json()['sources'] if s['state']=='waiting'), None))
        attempt = source['operation']['attempt_id']
        details = client.get('/api/v3/collection-status/demo').json()
        assert details['operations'][0]['last_error']['message'] == 'temporary source failure'
        assert details['operations'][0]['last_error']['requests'] == []
        assert client.put('/api/v3/power', json={'enabled':False}).status_code == 200
        paused = client.get('/api/v3/collection-status').json()['sources'][0]
        assert paused['state'] == 'paused'
        value=client.get('/api/v3/collection-status/demo').json()
        assert (value['last_error'] or value['operations'][0]['last_error'])['message'] == 'temporary source failure'
    clock.now += 100
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/collection-status').json()['sources'][0]['state'] == 'paused'
        value=client.get('/api/v3/collection-status/demo').json()
        assert (value['last_error'] or value['operations'][0]['last_error'])['message'] == 'temporary source failure'
        assert client.put('/api/v3/power', json={'enabled':True}).status_code == 200
        wait_for(client, lambda: all(s['state']=='complete' for s in client.get('/api/v3/collection-status').json()['sources']))
        details = client.get('/api/v3/collection-status/demo').json()
        assert details['last_error']['resolved'] is True
        assert details['last_error']['attempt_id'] == attempt
        assert details['source']['last_success_at']


def test_collection_cancelled_download_freezes_attempt_and_start_has_new_identity(tmp_path):
    from tests.support.reader import Clock
    clock = Clock()
    started = threading.Event()

    class SlowSource(DemoSource):
        async def discover(self, cursor):
            started.set()
            await asyncio.Event().wait()

    with TestClient(create_app(tmp_path, token='test', source=SlowSource(),clock=clock,api_only=True), headers=HEADERS) as client:
        assert started.wait(3)
        first = client.get('/api/v3/collection-status').json()['sources'][0]['operation']
        clock.now += 5
        assert client.put('/api/v3/power',json={'enabled':False}).status_code == 200
        paused = client.get('/api/v3/collection-status').json()['sources'][0]
        assert paused['state'] == 'paused'
        assert paused['operation']['elapsed_seconds'] == 5
        clock.now += 100
        assert client.get('/api/v3/collection-status').json()['sources'][0]['operation']['elapsed_seconds'] == 5
        started.clear()
        assert client.put('/api/v3/power',json={'enabled':True}).status_code == 200
        assert started.wait(3)
        second = client.get('/api/v3/collection-status').json()['sources'][0]['operation']
        assert second['attempt_id'] != first['attempt_id']
        assert second['elapsed_seconds'] == 0


def test_bare_session_secret_never_enters_collection_diagnostics(tmp_path):
    from oko.collection.contracts import SourceError
    secret = 'opaque-reader-session-value'

    class FailedSource(DemoSource):
        async def discover(self, cursor):
            raise SourceError('transport failed ' + secret, delay=60)

    with TestClient(create_app(tmp_path, token=secret, source=FailedSource(),api_only=True), headers={'Authorization':'Bearer '+secret}) as client:
        wait_for(client, lambda: any(s['state']=='waiting' for s in client.get('/api/v3/collection-status').json()['sources']))
        details = client.get('/api/v3/collection-status/demo')
        assert secret not in details.text
        assert '[session]' in details.text
    from tests.support.storage import database
    from psycopg import sql
    with database(tmp_path) as db:
        tables = [r[0] for r in db.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()')]
        for table in tables:
            rows = db._db.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t').format(sql.Identifier(table)))
            assert all(secret not in row[0] for row in rows), table


def test_confirmed_publication_unavailability_is_terminal_and_visible(tmp_path):
    from oko.collection.contracts import SourceDeleted

    class RemovedSource(DemoSource):
        async def fetch(self, publication):
            raise SourceDeleted()

    with TestClient(create_app(tmp_path,token='test',source=RemovedSource(),api_only=True),headers=HEADERS) as client:
        wait_for(client,lambda: len(read_all_cards(client, '/api/v3/cards')) == 3)
        def complete():
            value = client.get('/api/v3/collection-status').json()
            return value if all(s['state']=='error' for s in value['sources']) else None
        state = wait_for(client,complete)
        assert state['counts']['error'] == 1


def test_collection_summary_batches_attempts_and_details_ignore_other_feeds(tmp_path):
    import json
    from tests.support.storage import Store
    from tests.support.reading_cost import library, traced_app
    from tests.support.reading_measurements import ReadingMeasurements

    costs = []
    for count in (10, 100, 1000):
        root = tmp_path / str(count)
        feeds, _ = library(root, [1] * count)
        store = Store(root, lambda _: None)
        with store.db:
            jobs = store.db.execute("SELECT id,item,generation FROM jobs WHERE kind='fetch'").fetchall()
            for job in jobs:
                feed = store.db.execute('SELECT feed_id FROM items_read WHERE id = %s', (job['item'],)).fetchone()[0]
                error = dict(attempt_id=f"{job['id']}:{job['generation']}", operation_id='fetch:'+job['item'],
                             occurred_at='2026-01-01T00:00:00Z', error_type='SourceError', message='Synthetic failure',
                             traceback='Synthetic traceback\n' * 1000, source='server', message_truncated=False,
                             traceback_truncated=False, resolved=False, requests=[], request_count=0)
                store.db.execute("UPDATE jobs SET status = 'failed' , error = 'Synthetic failure' WHERE id = %s", (job['id'],))
                store.db.execute('INSERT INTO collection_attempts ( job , feed , data ) VALUES ( %s , %s , %s )',
                    (job['id'], feed, json.dumps(dict(attempt_id=error['attempt_id'], started_at=1800000000.,
                        finished_at=1800000001., elapsed_seconds=1., last_error=error))))
        store.db.close()
        app = traced_app(root)
        with TestClient(app, headers=HEADERS) as client, ReadingMeasurements(client, app, root) as measure:
            summary, summary_cost = measure.request('summary', 'GET', '/api/v3/collection-status')
            selected = next(source for source in summary.json()['sources'] if source['feed_id'] == feeds[0])
            detail, detail_cost = measure.request('detail', 'GET', '/api/v3/collection-status/'+feeds[0])
            assert detail.json()['source'] == selected
            assert len(detail.json()['operations']) == 1
            assert detail.json()['operations'][0]['last_error']['traceback'].startswith('Synthetic traceback')
            assert selected['error_count'] == 1 and selected['operation']['state'] == 'error'
            assert 'Synthetic traceback' not in summary.text
            costs.append((summary_cost, detail_cost))
    print(costs)
    # Two extra bounded batches of feed facts and collection operations.
    assert costs[-1][0]['sql'] <= costs[0][0]['sql'] + 16, costs
    assert costs[-1][1]['sql'] <= costs[0][1]['sql'] + 5, costs
    assert costs[-1][1]['json_input_bytes'] <= costs[0][1]['json_input_bytes'] + 1000, costs
    assert costs[-1][0]['json_input_bytes'] < 150_000, costs


def test_postgres_collection_details_are_bounded_and_keep_diagnostics(postgres_url, tmp_path, monkeypatch):
    import json
    import psycopg
    from tests.support.storage import Store
    from oko.subscriptions.subscriptions import AddFeed
    from tests.support.reading_cost import traced_app, request_sql
    from tests.support.reader import Clock

    original_execute, original_loads = psycopg.Connection.execute, json.loads
    loaded = []
    def execute(connection, query, *args, **kwargs):
        queries = request_sql.get()
        if queries is not None:
            queries.append(str(query))
        return original_execute(connection, query, *args, **kwargs)
    def loads(value, *args, **kwargs):
        if request_sql.get() is not None:
            loaded.append(len(value))
        return original_loads(value, *args, **kwargs)
    monkeypatch.setattr(psycopg.Connection, 'execute', execute)
    monkeypatch.setattr(json, 'loads', loads)
    clock = Clock()
    costs = []
    feeds = []
    for count in (10, 100, 1000):
        store = Store(tmp_path, lambda _: None, database_url=postgres_url, **clock.options())
        with store.db:
            store.set_work_enabled(False)
            for index in range(len(feeds), count):
                feed = store.subscriptions.add_feed(AddFeed(url=f'https://fixture.test/{index}',
                    title=f'Synthetic {index}', verification='synthetic'))['id']
                feeds.append(feed)
                store.jobs.enqueue_discovery(feed)
                job = store.db.execute("SELECT id , generation FROM jobs WHERE item = %s AND kind = 'discover'", (feed,)).fetchone()
                error = dict(attempt_id=f"{job['id']}:{job['generation']}", operation_id='discover:'+feed,
                             occurred_at='2026-01-01T00:00:00Z', error_type='SourceError', message='Synthetic failure',
                             traceback='Synthetic traceback\n' * 1000, source='server', message_truncated=False,
                             traceback_truncated=False, resolved=False, requests=[], request_count=0)
                store.db.execute("UPDATE jobs SET status = 'failed' , error = 'Synthetic failure' WHERE id = %s", (job['id'],))
                store.db.execute('INSERT INTO collection_attempts ( job , feed , data ) VALUES ( %s , %s , %s )',
                    (job['id'], feed, json.dumps(dict(attempt_id=error['attempt_id'], started_at=1800000000.,
                        finished_at=1800000001., elapsed_seconds=1., last_error=error))))
        store.db.close()
        app = traced_app(tmp_path, database_url=postgres_url, **clock.options())
        with TestClient(app, headers=HEADERS) as client:
            loaded.clear()
            summary = client.get('/api/v3/collection-status')
            assert summary.status_code == 200, summary.text
            summary_bytes = sum(loaded)
            summary_queries = int(summary.headers['X-Test-SQL'])
            selected = next(source for source in summary.json()['sources'] if source['feed_id'] == feeds[0])
            loaded.clear()
            detail = client.get('/api/v3/collection-status/'+feeds[0])
            assert detail.status_code == 200, detail.text
            detail_bytes = sum(loaded)
            assert detail.json()['source'] == selected
            assert detail.json()['operations'][0]['last_error']['traceback'].startswith('Synthetic traceback')
            assert selected['error_count'] == 1
            costs.append((summary_queries, int(detail.headers['X-Test-SQL']), summary_bytes, detail_bytes))
    print(costs)
    assert costs[-1][0] <= costs[0][0] + 16, costs
    assert costs[-1][1] <= costs[0][1] + 2, costs
    assert costs[-1][2] < 150_000, costs
    assert costs[-1][3] <= costs[0][3] + 1000, costs
