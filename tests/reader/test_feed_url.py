from tests.support.presence import presence
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import httpx

from tests.support.feed_url import client_for, verify, add


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
