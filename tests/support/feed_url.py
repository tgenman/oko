import httpx
from fastapi.testclient import TestClient
from tests.support.app import create_app

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
