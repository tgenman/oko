"""Bounded per-feed failure retention through real reader HTTP requests."""
import asyncio
import threading

from fastapi.testclient import TestClient
import httpx

from oko.collection.contracts import SourceError
from tests.support.synthetic_source import SyntheticSource
from tests.support.app import create_app
from tests.support.reader import Clock, wait_for


TOKEN = 'reader-auth-fixture-value'
HEADERS = {'Authorization':'Bearer '+TOKEN}


def details(client, feed='fixture'):
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


def test_multiple_operations_share_one_hundred_newest_failed_requests(tmp_path):
    clock = Clock()
    release_second = threading.Event()

    class MultipleFailures(SyntheticSource):
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
