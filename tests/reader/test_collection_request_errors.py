"""Collection diagnostics observed only through the reader HTTP interface."""
from pathlib import Path

from fastapi.testclient import TestClient
import httpx

from tests.support.app import create_app
from tests.support.presence import presence
from tests.support.reader import wait_for
from tests.support.rss import rss, wait_cards
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


def test_swallowed_article_http_failure_keeps_original_exception_and_url(tmp_path: Path):
    def respond(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        return httpx.Response(503, text='Private response body must never enter diagnostics')

    configured_sources(tmp_path, [('https://source.test/feed', 'Источник', 'Новости')])
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, api_only=True,
                              rss_transport=httpx.MockTransport(respond)), headers=HEADERS) as client:
        presence(client, 'reader')
        card = wait_cards(client)[0]
        material = client.get('/api/v3/publication/' + card['id']).json()
        assert material['completeness']['state'] == 'incomplete'
        assert material['text'] == 'Анонс статьи'
        body, error = wait_for(client, lambda: failed_details(client))
        assert error['error_type'] == 'HTTPStatusError'
        assert '503' in error['message']
        request = error['requests'][0]
        assert request['url'] == 'https://source.test/article'
        assert request['method'] == 'GET' and request['purpose'] == 'publication'
        assert request['http_status'] == 503
        assert request['error_type'] == 'HTTPStatusError'
        assert request['occurred_at'] and request['first_at'] and request['last_at']
        assert error['request_count'] == 1
        assert 'Private response body' not in str(body)


def test_attachment_redirect_timeout_retains_both_redacted_addresses(tmp_path: Path):
    original = 'https://source.test/redirect?token=original-secret'
    final = 'https://cdn.test/image.png?X-Amz-Signature=signature-secret'

    def respond(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        if request.url.path == '/article':
            return httpx.Response(200, text=f'<article>Текст статьи<img src="{original}"></article>')
        if request.url.path == '/redirect':
            return httpx.Response(302, headers={'Location': final})
        raise httpx.ReadTimeout('Synthetic read timeout', request=request)

    configured_sources(tmp_path, [('https://source.test/feed', 'Источник', 'Новости')])
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, api_only=True,
                              rss_transport=httpx.MockTransport(respond)), headers=HEADERS) as client:
        presence(client, 'reader')
        wait_cards(client)
        body, error = wait_for(client, lambda: failed_details(client))
        assert error['error_type'] == 'ReadTimeout'
        assert error['message'] == 'Synthetic read timeout'
        assert 'ReadTimeout' in error['traceback']
        request = error['requests'][0]
        assert request['url'].startswith('https://cdn.test/image.png?X-Amz-Signature=')
        assert request['original_url'].startswith('https://source.test/redirect?token=')
        assert request['purpose'] == 'attachment'
        assert request['http_status'] is None
        assert request['redacted'] is True
        all_json = str(body) + client.get('/api/v3/collection-status').text
        assert 'original-secret' not in all_json
        assert 'signature-secret' not in all_json


def test_owned_caption_worker_preserves_remote_error_without_inventing_request(tmp_path: Path, monkeypatch):
    import os
    from oko.collection.youtube.source import LocalCaptions
    from tests.support.reader import Clock
    from tests.support.youtube import CHANNEL, PNG, feed_xml

    package = tmp_path / 'sdk' / 'youtube_transcript_api'
    package.mkdir(parents=True)
    (package / '_errors.py').write_text('class TranscriptsDisabled(Exception): pass\nclass NoTranscriptFound(Exception): pass\nclass VideoUnplayable(Exception): pass\n')
    (package / '__init__.py').write_text("""
class YouTubeTranscriptApi:
    def __init__(self, http_client): pass
    def list(self, video):
        raise RuntimeError('Synthetic caption failure password=caption-secret')
""")
    monkeypatch.setenv('PYTHONPATH', str(package.parent) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    clock = Clock()
    root = tmp_path / 'reader'
    configured_sources(root, [(f'https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}', 'Канал', 'Новости')])
    def respond(request):
        return httpx.Response(200, text=feed_xml()) if request.url.host == 'www.youtube.com' else httpx.Response(200, content=PNG, headers={'Content-Type': 'image/png'})
    with TestClient(create_app(root, token='test-token', rss_enabled=True, api_only=True,
                              rss_transport=httpx.MockTransport(respond), youtube_captions=LocalCaptions(),
                              clock=clock, wall_clock=clock), headers=HEADERS) as client:
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]['id']
        assert client.patch('/api/v3/feeds/' + feed, json={'importance': 'high'}).status_code == 200
        presence(client, 'reader')
        def remote_failure():
            clock.now += 1
            return failed_details(client, require_requests=False, error_type='RuntimeError')
        body, error = wait_for(client, remote_failure)
        assert error['error_type'] == 'RuntimeError'
        assert 'Synthetic caption failure' in error['message']
        assert 'RuntimeError' in error['traceback']
        assert error['source'] == 'worker'
        assert error['requests'] == [] and error['request_count'] == 0
        assert 'caption-secret' not in str(body)


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
