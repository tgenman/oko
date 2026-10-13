import time
import pytest
from datetime import date, timedelta
from tests.support.feed_url import client_for, verify, add


def test_new_feed_saves_default_history_before_collection_and_restarts(tmp_path):
    with client_for(tmp_path) as client:
        feed = add(client, verify(client)).json()
        assert feed['initial_history']['mode'] == 'count'
        assert feed['initial_history']['count'] == 20
        assert feed['initial_history']['state'] == 'pending'
    with client_for(tmp_path) as client:
        history = client.get('/api/subscriptions').json()['feeds'][0]['initial_history']
        assert history['count'] == 20
        assert history['loaded'] == 0


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
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history['loaded'] == 2:
                break
            time.sleep(.02)
        assert history['loaded'] == 2
        assert {card['url'] for card in client.get('/api/cards').json()} == {'https://source.test/post/5', 'https://source.test/post/4'}
    posts.insert(0, 6)
    with open_reader() as client:
        client.post('/api/tabs/reader/heartbeat')
        for _ in range(100):
            cards = client.get('/api/cards').json()
            if len(cards) == 3:
                break
            time.sleep(.02)
        assert {card['url'] for card in cards} == {'https://source.test/post/6', 'https://source.test/post/5', 'https://source.test/post/4'}
        assert client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']['loaded'] == 2


def test_date_boundary_uses_user_zone_and_invalid_values_are_rejected(tmp_path):
    with client_for(tmp_path) as client:
        checked = verify(client)
        for history in [{'mode': 'count', 'count': 0}, {'mode': 'count', 'count': 1.5}, {'mode': 'count', 'count': True},
                        {'mode': 'date', 'count': None, 'date': str(date.today() + timedelta(days=2)), 'timezone': 'Europe/Moscow'},
                        {'mode': 'date', 'date': '2025-10-01'}, {'mode': 'date', 'count': None, 'date': 'bad'}]:
            assert add(client, checked, initial_history=history).status_code == 422
        response = add(client, checked, initial_history={'mode': 'date', 'count': None, 'date': '2025-10-01', 'timezone': 'Europe/Moscow'})
        assert response.status_code == 200, response.text
        history = response.json()['initial_history']
        assert history['since'] == 1759266000.0  # 2025-09-30 21:00 UTC = Oct 1 midnight Moscow
    with client_for(tmp_path) as client:
        assert client.get('/api/subscriptions').json()['feeds'][0]['initial_history']['since'] == 1759266000.0


def test_seven_of_twenty_restart_keeps_selected_history_and_progress(tmp_path):
    import asyncio
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.feed_url import HEADERS
    blocked = True
    posts = list(range(20, 0, -1))
    async def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text='<rss><channel><title>История</title>' + ''.join(f'<item><guid>{n}</guid><title>Post {n}</title><link>https://source.test/post/{n}</link><pubDate>Wed, 01 Oct 2025 12:00:00 GMT</pubDate></item>' for n in posts) + '</channel></rss>')
        if blocked and request.url.path == '/post/13':
            await asyncio.Event().wait()
        return httpx.Response(200, text='<article>Полный материал</article>')
    def open_reader():
        return TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS)
    with open_reader() as client:
        feed = add(client, verify(client, 'https://source.test/feed')).json()
        for _ in range(200):
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history['loaded'] == 7:
                break
            time.sleep(.01)
        assert history['loaded'] == 7
        assert history['available'] == 20
        assert history['state'] == 'loading'
    blocked = False
    posts[:] = [21, 20]  # feed window rotates; persisted refs/teasers retain remaining 13
    with open_reader() as client:
        assert client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']['loaded'] == 7
        client.post('/api/tabs/reader/heartbeat')
        for _ in range(200):
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history['loaded'] == 20:
                break
            time.sleep(.01)
        assert history['loaded'] == 20
        assert history['available'] == 20
        assert history['state'] == 'done'
        assert len(client.get('/api/cards').json()) == 21


def test_selected_date_includes_midnight_and_every_later_post(tmp_path):
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.feed_url import HEADERS
    def reply(request):
        if request.url.path == '/feed':
            timestamps = ['2025-09-30T20:59:59Z', '2025-09-30T21:00:00Z', '2025-10-01T10:00:00Z']
            return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"><title>История</title>' + ''.join(f'<entry><id>{n}</id><title>Post {n}</title><link href="https://source.test/post/{n}"/><published>{stamp}</published></entry>' for n, stamp in enumerate(timestamps)) + '</feed>')
        return httpx.Response(200, text='<article>Текст</article>')
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode': 'date', 'count': None, 'date': '2025-10-01', 'timezone': 'Europe/Moscow'}).json()
        for _ in range(100):
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history['loaded'] == 2:
                break
            time.sleep(.02)
        assert history['loaded'] == 2
        assert {c['url'] for c in client.get('/api/cards').json()} == {'https://source.test/post/1', 'https://source.test/post/2'}


def test_youtube_count_uses_video_publications_without_waiting_for_theses(tmp_path):
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.feed_url import HEADERS, YOUTUBE
    from tests.support.youtube import PNG, History, NoCaptions
    def reply(request):
        if request.url.host == 'i.ytimg.com':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        assert request.url.path == '/feeds/videos.xml'
        return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"><title>Учёба</title>' + ''.join(f'<entry><id>yt:video:{video}</id><title>{video}</title><link href="https://www.youtube.com/watch?v={video}"/><published>2025-10-0{n}T12:00:00Z</published></entry>' for n, video in enumerate(['abcdefghijk', 'lmnopqrstuv', 'wxyzabcdefg'], 1)) + '</feed>')
    clock = [100.0]
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply), youtube_history=History(['wxyzabcdefg', 'lmnopqrstuv', 'abcdefghijk']), youtube_captions=NoCaptions(), clock=lambda: clock[0], wall_clock=lambda: clock[0], lease=60), headers=HEADERS) as client:
        feed = add(client, verify(client, YOUTUBE), initial_history={'mode': 'count', 'count': 2}).json()
        for _ in range(100):
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history['loaded'] == 1:
                break
            time.sleep(.02)
        assert history['loaded'] == 1
        clock[0] += 10
        for _ in range(100):
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history['loaded'] == 2:
                break
            time.sleep(.02)
        assert history['loaded'] == 2
        assert history['state'] == 'done'
        assert {c['url'] for c in client.get('/api/cards').json()} == {'https://www.youtube.com/watch?v=lmnopqrstuv', 'https://www.youtube.com/watch?v=wxyzabcdefg'}


def test_count_has_no_product_limit_and_preserves_large_integer(tmp_path):
    with client_for(tmp_path) as client:
        response = add(client, verify(client), initial_history={'mode': 'count', 'count': '9007199254740993'})
        assert response.status_code == 200, response.text
        assert response.json()['initial_history']['count'] == 9007199254740993


def test_rss_unknown_dates_included_and_window_warnings_survive_rotation(tmp_path):
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.feed_url import HEADERS
    rows = [('unknown', ''), ('new', '2025-10-02T12:00:00Z'), ('old', '2025-09-01T12:00:00Z')]
    def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"><title>История</title>' + ''.join(f'<entry><id>{n}</id><title>{n}</title><link href="https://source.test/{n}"/><published>{stamp}</published></entry>' for n, stamp in rows) + '</feed>')
        return httpx.Response(200, text='<article>Текст</article>')
    def reader():
        return TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS)
    with reader() as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode': 'date', 'count': None, 'date': '2025-10-01'}).json()
        for _ in range(100):
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history['loaded'] == 2:
                break
            time.sleep(.02)
        assert history['loaded'] == 2
        assert 'полнота истории за период не подтверждена' in ' '.join(history['warnings'])
        unknown = next(c for c in client.get('/api/cards').json() if c['url'].endswith('/unknown'))
        assert unknown['published_known'] is False
        assert unknown['published'] == 0  # no invented publication date
        assert client.get('/api/materials/' + unknown['id']).json()['published_known'] is False
    rows[:] = [('later', '2025-10-03T12:00:00Z')]
    with reader() as client:
        history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
        assert history['available'] == 2
        assert 'полнота истории за период не подтверждена' in ' '.join(history['warnings'])


@pytest.mark.parametrize('dates, warning', [(['', '', ''], True), (['2025-10-03T12:00:00Z', '', '2025-10-01T12:00:00Z'], False), (['2025-10-01T12:00:00Z', '', '2025-10-03T12:00:00Z'], True)])
def test_unknown_rss_count_keeps_feed_order_and_warns_when_precision_unconfirmed(tmp_path, dates, warning):
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.feed_url import HEADERS
    def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text='<rss><channel><title>История</title>' + ''.join(f'<item><guid>{n}</guid><title>{n}</title><link>https://source.test/{n}</link><published>{stamp}</published></item>' for n, stamp in zip(['first', 'second', 'third'], dates)) + '</channel></rss>')
        return httpx.Response(200, text='<article>Текст</article>')
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode': 'count', 'count': 2}).json()
        for _ in range(100):
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history['loaded'] == 2:
                break
            time.sleep(.02)
        assert {c['url'] for c in client.get('/api/cards').json()} == {'https://source.test/first', 'https://source.test/second'}
        assert ('точность выбора последних N не подтверждена' in ' '.join(history['warnings'])) is warning


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
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history['loaded'] == 15:
                break
            time.sleep(.02)
        assert history['count'] == 100
        assert history['available'] == history['loaded'] == 15
        assert history['state'] == 'done'
