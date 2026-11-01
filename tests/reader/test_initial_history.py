from tests.support.cards import read_all_cards
from tests.support.presence import presence
import time
import pytest
from tests.support.feed_url import verify, add


@pytest.mark.parametrize('posts', [[5, 4, 3, 2, 1], [5, 5, 4, 3, 2, 1]])
def test_count_selects_fixed_latest_set_and_new_arrivals_do_not_consume_count(tmp_path, posts):
    import httpx
    def reply(request):
        if request.url.path == '/feed':
            xml = '<rss><channel><title>История</title>' + ''.join(f'<item><guid>{n}</guid><title>Post {n}</title><link>https://source.test/post/{n}</link><pubDate>Wed, {n:02d} Oct 2025 12:00:00 GMT</pubDate></item>' for n in posts) + '</channel></rss>'
            return httpx.Response(200, text=xml)
        return httpx.Response(200, text='<article>Полный материал</article>')
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.feed_url import HEADERS
    def open_reader():
        return TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS)
    with open_reader() as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode': 'count', 'count': 2}).json()
        for _ in range(100):
            history = client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']
            if history['progress']['loaded'] == 2:
                break
            time.sleep(.02)
        assert history['progress']['loaded'] == 2
        assert {card['source_url'] for card in read_all_cards(client, '/api/v3/cards')} == {'https://source.test/post/5', 'https://source.test/post/4'}
    posts.insert(0, 6)
    with open_reader() as client:
        presence(client, 'reader')
        for _ in range(100):
            cards = read_all_cards(client, '/api/v3/cards')
            if len(cards) == 6:
                break
            time.sleep(.02)
        assert {card['source_url'] for card in cards} == {f'https://source.test/post/{n}' for n in range(1,7)}
        assert client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']['progress']['loaded'] == 2


def test_two_of_four_restart_keeps_selected_history_and_progress(tmp_path):
    import asyncio
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.feed_url import HEADERS
    blocked = True
    posts = list(range(4, 0, -1))
    async def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text='<rss><channel><title>История</title>' + ''.join(f'<item><guid>{n}</guid><title>Post {n}</title><link>https://source.test/post/{n}</link><pubDate>Wed, 01 Oct 2025 12:00:00 GMT</pubDate></item>' for n in posts) + '</channel></rss>')
        if blocked and request.url.path == '/post/2':
            await asyncio.Event().wait()
        return httpx.Response(200, text='<article>Полный материал</article>')
    def open_reader():
        return TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS)
    with open_reader() as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode': 'count', 'count': 4}).json()
        for _ in range(200):
            history = client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']
            if history['progress']['loaded'] == 4:
                break
            time.sleep(.01)
        assert history['progress']['loaded'] == 4
        assert history['progress']['available'] == 4
        assert history['state'] == 'complete'
    blocked = False
    posts[:] = [5, 4]  # rotated window omits both remaining selected publications
    with open_reader() as client:
        assert client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']['progress']['loaded'] == 4
        presence(client, 'reader')
        for _ in range(200):
            history = client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']
            if history['progress']['loaded'] == 4:
                break
            time.sleep(.01)
        assert history['progress']['loaded'] == 4
        assert history['progress']['available'] == 4
        assert history['state'] == 'complete'
        from tests.support.reader import wait_for
        assert len(wait_for(client,lambda: cards if len(cards:=read_all_cards(client, '/api/v3/cards'))==5 else None)) == 5


def test_finite_rss_history_reports_requested_and_available_without_archive(tmp_path):
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.feed_url import HEADERS
    def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text='<rss><channel><title>История</title>' + ''.join(f'<item><guid>{n}</guid><title>{n}</title><link>https://source.test/{n}</link><pubDate>Wed, 01 Oct 2025 12:00:00 GMT</pubDate></item>' for n in range(15)) + '</channel></rss>')
        assert request.url.path[1:].isdigit()  # no archive endpoint
        return httpx.Response(200, text='<article>Текст</article>')
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode': 'count', 'count': 100}).json()
        for _ in range(100):
            history = client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']
            if history['progress']['loaded'] == 15:
                break
            time.sleep(.02)
        assert int(history['selection']['count']) == 100
        assert history['progress']['available'] == history['progress']['loaded'] == 15
        assert history['state'] == 'complete'
