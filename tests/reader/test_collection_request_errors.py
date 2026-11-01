"""Collection diagnostics observed only through the reader HTTP interface."""
from pathlib import Path

from fastapi.testclient import TestClient
import httpx

from tests.support.app import create_app
from tests.support.presence import presence
from tests.support.reader import wait_for
from tests.support.rss import rss
from tests.support.subscriptions import configured_sources


HEADERS = {'Authorization': 'Bearer test-token'}


def failed_details(client, *, require_requests=True, error_type=None):
    response = client.get('/api/v3/collection-status')
    assert response.status_code == 200, response.text
    for source in response.json()['sources']:
        details = client.get('/api/v3/collection-status/' + source['feed_id'])
        assert details.status_code == 200, details.text
        body = details.json()
        errors = [operation.get('last_error') for operation in body['operations']]
        errors.append(body.get('last_error'))
        for error in errors:
            if error and (error['requests'] or not require_requests) and (error_type is None or error['error_type'] == error_type):
                return body, error
    return None


def test_original_request_message_and_traceback_have_explicit_byte_limits(tmp_path: Path):
    long_message = 'я' * 40_000
    def respond(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        raise httpx.ReadTimeout(long_message, request=request)

    configured_sources(tmp_path, [('https://source.test/feed', 'Источник', 'Новости')])
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, api_only=True,
                              rss_transport=httpx.MockTransport(respond)), headers=HEADERS) as client:
        presence(client, 'reader')
        _, error = wait_for(client, lambda: failed_details(client))
        assert error['error_type'] == 'ReadTimeout'
        assert error['message_truncated'] is True
        assert len(error['message'].encode()) <= 16 * 1024
        assert error['traceback_truncated'] is True
        assert len(error['traceback'].encode()) <= 64 * 1024
        request = error['requests'][0]
        assert request['message_truncated'] is True
        assert len(request['message'].encode()) <= 16 * 1024
