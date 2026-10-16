from tests.support.presence import presence, close_presence
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import httpx
from fastapi.testclient import TestClient
from tests.support.app import create_app

from tests.support.feed_url import HEADERS, YOUTUBE, RSS, ATOM, client_for, verify, add


def test_check_title_then_add_is_durable_and_does_not_save_early(tmp_path):
    with client_for(tmp_path) as client:
        checked = verify(client)
        assert checked['candidate']['title'] == 'Новости & книги'
        assert client.get('/api/v3/subscriptions').json() == {'folders': [], 'feeds': []}
        response = add(client, checked, title='Моё название')
        assert response.status_code == 200, response.text
        feed = response.json()
        assert feed['title'] == 'Моё название'
        assert feed['kind'] == feed['transport'] == 'rss'
    with client_for(tmp_path) as client:
        assert client.get('/api/v3/subscriptions').json()['feeds'] == [feed]


def test_cannot_bypass_check_or_use_result_for_other_url(tmp_path):
    with client_for(tmp_path) as client:
        presence(client, 'reader')
        assert client.post('/api/v3/feeds', json={'url': 'https://example.org/feed.xml', 'title': 'X'}).status_code == 422
        checked = verify(client)
        assert add(client, checked, url='https://example.org/other').status_code == 409
        assert client.get('/api/v3/subscriptions').json()['feeds'] == []


def test_atom_and_missing_title_and_validation_errors(tmp_path):
    replies = {'/atom': ATOM, '/empty': '<rss><channel/></rss>', '/html': '<html><title>Page</title></html>', '/xml': '<rss'}
    def reply(request):
        if request.url.path == '/timeout':
            raise httpx.ReadTimeout('timed out')
        return httpx.Response(200, text=replies.get(request.url.path, RSS))
    with client_for(tmp_path, reply) as client:
        assert verify(client, 'https://example.org/atom')['candidate']['title'] == 'Учёба'
        checked = verify(client, 'https://example.org/empty')
        assert checked['candidate']['title'] == ''
        assert add(client, checked).status_code == 422
        assert add(client, checked, title='Название вручную').status_code == 200
        before = client.get('/api/v3/subscriptions').json()
        for url in ['https://example.org/html', 'https://example.org/xml', 'https://example.org/timeout',
                    'file:///etc/passwd', 'https://user:password@example.org/feed', 'https://t.me/channel']:
            reply=client.post('/api/v3/feeds/verify', json={'url': url})
            expected=400 if url.startswith('file:') or 'user:password' in url or url.startswith('https://t.me/') else 503
            assert reply.status_code == expected, reply.text
        assert client.get('/api/v3/subscriptions').json() == before
        assert client.get('/api/v3/diagnostics').json()['groups']


def test_duplicates_keep_settings_and_concurrent_adds_have_one_identity(tmp_path):
    with client_for(tmp_path) as client:
        checked = verify(client)
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: add(client, checked), range(2)))
        assert all(response.status_code == 200 for response in responses)
        feed = responses[0].json()
        assert responses[1].json()['id'] == feed['id']
        client.patch('/api/v3/feeds/' + feed['id'], json={'importance': 'high'})
        repeated = add(client, checked, title='Другое', folder='Другая').json()
        assert repeated['id'] == feed['id']
        assert repeated['title'] == feed['title']
        assert repeated['folder_id'] == feed['folder_id']
        assert repeated['importance'] == 'high'
        checked_again = verify(client)
        assert checked_again['existing_feed']['id'] == feed['id']
        assert len(client.get('/api/v3/subscriptions').json()['folders']) == 1


def test_late_check_after_stop_cannot_be_saved(tmp_path):
    started, release = threading.Event(), threading.Event()
    async def reply(request):
        started.set()
        while not release.is_set():
            try:
                await asyncio.sleep(.01)
            except asyncio.CancelledError:
                pass
        return httpx.Response(200, text=RSS)
    with client_for(tmp_path, reply) as client:
        presence(client, 'reader')
        with ThreadPoolExecutor(1) as pool:
            response = pool.submit(client.post, '/api/v3/feeds/verify', json={'url': 'https://example.org/feed'})
            assert started.wait(2)
            assert client.post('/api/v3/stop').status_code == 200
            release.set()
            assert response.result(timeout=2).status_code == 409
    with client_for(tmp_path) as client:
        assert client.get('/api/v3/subscriptions').json()['feeds'] == []
