"""Bounded per-feed failure retention through real reader HTTP requests."""
import asyncio
import threading

from fastapi.testclient import TestClient
import httpx

from oko.collection.contracts import SourceError
from oko.collection.demo import DemoSource
from tests.support.app import create_app
from tests.support.reader import Clock, wait_for
from tests.support.subscriptions import configured_sources
from tests.support.rss import rss


TOKEN = 'reader-auth-fixture-value'
HEADERS = {'Authorization':'Bearer '+TOKEN}


def details(client, feed='demo'):
    result = client.get('/api/v3/collection-status/' + feed)
    assert result.status_code == 200, result.text
    return result.json()


def unique_errors(value):
    rows = [op['last_error'] for op in value['operations'] if op['last_error']]
    if value['last_error']:
        rows.append(value['last_error'])
    return {row['attempt_id']: row for row in rows}.values()


def remote_error(label, prefix, first_time, count=60):
    rows = [dict(url=f'https://synthetic.test/{prefix}/{n}', original_url=None,
                 method='GET', purpose='attachment', occurred_at=first_time + n,
                 http_status=503, error_type='HTTPStatusError', message=f'{prefix} failed {n}',
                 count=1, first_at=first_time + n, last_at=first_time + n,
                 redacted=False, message_truncated=False) for n in range(count)]
    return SourceError.from_worker(label, dict(error_type='HTTPStatusError', message=label,
        traceback=f'HTTPStatusError: {label}', location='synthetic-worker:1',
        request_failures=rows, request_failure_count=count), delay=60)


def test_missing_rss_feed_records_its_actual_failed_request(tmp_path):
    configured_sources(tmp_path, [('https://synthetic.test/feed', 'Пропавшая лента', 'Новости')])
    with TestClient(create_app(tmp_path, token=TOKEN, api_only=True, rss_enabled=True,
                              rss_transport=httpx.MockTransport(lambda _:httpx.Response(404))), headers=HEADERS) as client:
        def failure():
            snapshot = client.get('/api/v3/collection-status').json()
            if not snapshot['sources']:
                return None
            value = details(client, snapshot['sources'][0]['feed_id'])
            return next((error for error in unique_errors(value) if error['requests']), None)
        error = wait_for(client, failure)
        assert error['error_type'] == 'HTTPStatusError'
        assert error['requests'][0]['url'] == 'https://synthetic.test/feed'
        assert error['requests'][0]['purpose'] == 'feed'
        assert error['requests'][0]['http_status'] == 404


def test_multiple_operations_share_one_hundred_newest_failed_requests(tmp_path):
    clock = Clock()
    release_second = threading.Event()

    class MultipleFailures(DemoSource):
        async def fetch(self, publication):
            if publication.id == '1':
                raise remote_error('first operation', 'recent', 2000)
            if publication.id == '2':
                while not release_second.is_set():
                    await asyncio.sleep(.01)
                raise remote_error('second operation', 'older', 1000)
            return await super().fetch(publication)

    options = dict(token=TOKEN, api_only=True, source=MultipleFailures(), clock=clock,
                   wall_clock=lambda:1800000000 + clock.now)
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        wait_for(client, lambda: any(e['message']=='first operation' for e in unique_errors(details(client))))
        clock.now += 10
        release_second.set()
        def both_failed():
            value = details(client)
            return value if {e['message'] for e in unique_errors(value)} == {'first operation', 'second operation'} else None
        value = wait_for(client, both_failed)
        errors = list(unique_errors(value))
        requests = [request for error in errors for request in error['requests']]
        assert len(requests) == 100
        assert sum(error['request_count'] for error in errors) == 120
        expected = {f'https://synthetic.test/recent/{n}' for n in range(60)}
        expected.update(f'https://synthetic.test/older/{n}' for n in range(20, 60))
        assert {request['url'] for request in requests} == expected
        for error in errors:
            assert [r['last_at'] for r in error['requests']] == sorted((r['last_at'] for r in error['requests']), reverse=True)
        assert client.put('/api/v3/power', json={'enabled':False}).status_code == 200
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        restored = [r for e in unique_errors(details(client)) for r in e['requests']]
        assert len(restored) == 100
        assert {r['url'] for r in restored} == expected


def test_older_operation_success_keeps_newer_failure_as_feed_last_error(tmp_path):
    clock = Clock()
    release_second = threading.Event()
    calls = {'1':0, '2':0}

    class RetryingOperations(DemoSource):
        async def fetch(self, publication):
            if publication.id in calls:
                calls[publication.id] += 1
                if calls[publication.id] == 1:
                    if publication.id == '2':
                        while not release_second.is_set():
                            await asyncio.sleep(.01)
                    raise remote_error('older A' if publication.id=='1' else 'newer B', publication.id, 1000, count=1)
            return await super().fetch(publication)

    with TestClient(create_app(tmp_path, token=TOKEN,api_only=True,source=RetryingOperations(),
                              clock=clock,wall_clock=lambda:1800000000+clock.now),headers=HEADERS) as client:
        wait_for(client, lambda: any(e['message']=='older A' for e in unique_errors(details(client))))
        clock.now += 10
        release_second.set()
        def both_failed():
            value = details(client)
            return value if {e['message'] for e in unique_errors(value)} == {'older A','newer B'} else None
        value = wait_for(client,both_failed)
        older = next(op for op in value['operations'] if op['last_error'] and op['last_error']['message']=='older A')
        newer = next(op for op in value['operations'] if op['last_error'] and op['last_error']['message']=='newer B')
        item_a = older['operation_id'].removeprefix('fetch:')
        assert client.post('/api/v3/materials/'+item_a+'/retry').status_code == 200
        def older_finished():
            value = details(client)
            return value if not any(op['operation_id']==older['operation_id'] for op in value['operations']) else None
        value = wait_for(client, older_finished)
        assert {e['message'] for e in unique_errors(value)} == {'newer B'}
        assert not next(iter(unique_errors(value)))['resolved']
        item_b = newer['operation_id'].removeprefix('fetch:')
        assert client.post('/api/v3/materials/'+item_b+'/retry').status_code == 200
        value = wait_for(client, lambda: value if not (value:=details(client))['operations'] else None)
        assert value['last_error']['message'] == 'newer B'
        assert value['last_error']['resolved'] is True


def test_missing_rss_attachment_records_failure_without_removing_publication(tmp_path):
    def respond(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        if request.url.path == '/article':
            return httpx.Response(200, text='<article>Сохранённый текст<img src="https://synthetic.test/missing.png"></article>')
        return httpx.Response(404)
    configured_sources(tmp_path, [('https://synthetic.test/feed', 'Источник', 'Новости')])
    with TestClient(create_app(tmp_path, token=TOKEN, api_only=True, rss_enabled=True,
                              rss_transport=httpx.MockTransport(respond)), headers=HEADERS) as client:
        def failure():
            snapshot = client.get('/api/v3/collection-status').json()
            value = details(client, snapshot['sources'][0]['feed_id'])
            return next((error for error in unique_errors(value) if error['requests']), None)
        error = wait_for(client, failure)
        assert error['requests'][0]['url'] == 'https://synthetic.test/missing.png'
        assert error['requests'][0]['purpose'] == 'attachment'
        assert error['requests'][0]['http_status'] == 404
        cards = client.get('/api/v3/cards').json()
        assert len(cards) == 1
        material = client.get('/api/v3/materials/'+cards[0]['id']).json()
        assert material['content']['text'].endswith('Сохранённый текст')
