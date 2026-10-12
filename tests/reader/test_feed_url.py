import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import httpx
from fastapi.testclient import TestClient
from oko.reader.app import create_app

HEADERS = {'Authorization': 'Bearer test'}
YOUTUBE = 'https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw'
RSS = '<rss version="2.0"><channel><title>Новости &amp; книги</title></channel></rss>'
ATOM = '<feed xmlns="http://www.w3.org/2005/Atom"><title>Учёба</title></feed>'


def client_for(root, reply=lambda request: httpx.Response(200, text=RSS)):
    return TestClient(create_app(root, token='test', rss_transport=httpx.MockTransport(reply)), headers=HEADERS)


def verify(client, url='https://example.org/feed.xml'):
    client.post('/api/tabs/reader/heartbeat')
    response = client.post('/api/feeds/verify', json={'url': url})
    assert response.status_code == 200, response.text
    return response.json()


def add(client, checked, **values):
    return client.post('/api/feeds', json={'url': checked['url'], 'verification': checked['verification'],
                                        'title': checked['title'], 'folder': 'Учёба', **values})


def test_check_title_then_add_is_durable_and_does_not_save_early(tmp_path):
    with client_for(tmp_path) as client:
        checked = verify(client)
        assert checked['title'] == 'Новости & книги'
        assert client.get('/api/subscriptions').json() == {'folders': [], 'feeds': []}
        response = add(client, checked, title='Моё название')
        assert response.status_code == 200, response.text
        feed = response.json()
        assert feed['title'] == 'Моё название'
        assert feed['kind'] == feed['transport'] == 'rss'
    with client_for(tmp_path) as client:
        assert client.get('/api/subscriptions').json()['feeds'] == [feed]


def test_cannot_bypass_check_or_use_result_for_other_url(tmp_path):
    with client_for(tmp_path) as client:
        client.post('/api/tabs/reader/heartbeat')
        assert client.post('/api/feeds', json={'url': 'https://example.org/feed.xml', 'title': 'X'}).status_code == 422
        checked = verify(client)
        assert add(client, checked, url='https://example.org/other').status_code == 400
        assert client.get('/api/subscriptions').json()['feeds'] == []


def test_atom_and_missing_title_and_validation_errors(tmp_path):
    replies = {'/atom': ATOM, '/empty': '<rss><channel/></rss>', '/html': '<html><title>Page</title></html>', '/xml': '<rss'}
    def reply(request):
        if request.url.path == '/timeout':
            raise httpx.ReadTimeout('timed out')
        return httpx.Response(200, text=replies.get(request.url.path, RSS))
    with client_for(tmp_path, reply) as client:
        assert verify(client, 'https://example.org/atom')['title'] == 'Учёба'
        checked = verify(client, 'https://example.org/empty')
        assert checked['title'] == ''
        assert add(client, checked).status_code == 422
        assert add(client, checked, title='Название вручную').status_code == 200
        before = client.get('/api/subscriptions').json()
        for url in ['https://example.org/html', 'https://example.org/xml', 'https://example.org/timeout',
                    'file:///etc/passwd', 'https://user:password@example.org/feed', 'https://t.me/channel']:
            assert client.post('/api/feeds/verify', json={'url': url}).status_code == 400
        assert client.get('/api/subscriptions').json() == before
        assert client.get('/api/debug').json()['groups']


def test_duplicates_keep_settings_and_concurrent_adds_have_one_identity(tmp_path):
    with client_for(tmp_path) as client:
        checked = verify(client)
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: add(client, checked), range(2)))
        assert all(response.status_code == 200 for response in responses)
        feed = responses[0].json()
        assert responses[1].json()['id'] == feed['id']
        client.patch('/api/feeds/' + feed['id'], json={'importance': 'high'})
        repeated = add(client, checked, title='Другое', folder='Другая').json()
        assert repeated['id'] == feed['id']
        assert repeated['title'] == feed['title']
        assert repeated['folder_id'] == feed['folder_id']
        assert repeated['importance'] == 'high'
        checked_again = verify(client)
        assert checked_again['existing']['id'] == feed['id']
        assert len(client.get('/api/subscriptions').json()['folders']) == 1


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
        client.post('/api/tabs/reader/heartbeat')
        with ThreadPoolExecutor(1) as pool:
            response = pool.submit(client.post, '/api/feeds/verify', json={'url': 'https://example.org/feed'})
            assert started.wait(2)
            assert client.post('/api/stop').status_code == 200
            release.set()
            assert response.result(timeout=2).status_code == 409
    with client_for(tmp_path) as client:
        assert client.get('/api/subscriptions').json()['feeds'] == []
