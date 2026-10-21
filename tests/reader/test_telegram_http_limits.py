"""Shared Telegram HTTP admission observed through reader commands."""
import time

import httpx

from tests.support.app import create_app
from tests.support.telegram_web import page
from tests.support.v3 import TestClient

HEADERS = {'Authorization': 'Bearer test'}


def test_connections_share_two_requests_per_second(tmp_path):
    calls = []
    def response(request):
        calls.append(time.monotonic())
        username = request.url.path.split('/')[-1]
        return httpx.Response(200, text=page([]).replace('fixture', username))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        for name in ('firstchannel', 'secondchannel'):
            result = client.post('/api/v3/telegram/connect', json={'source_locator':'@'+name, 'folder':'Inbox'})
            assert result.status_code == 200, result.text
        assert len(calls) >= 2
        assert all(b-a >= .48 for a,b in zip(calls,calls[1:]))


def test_429_blocks_other_connections_and_survives_restart(tmp_path):
    calls = []
    wall = [1800000000.]
    def response(request):
        calls.append(str(request.url))
        return httpx.Response(429, headers={'Retry-After':'120'})
    options = dict(token='test', api_only=True, rss_enabled=True, rss_transport=httpx.MockTransport(response), wall_clock=lambda: wall[0])
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        result = client.post('/api/v3/telegram/connect', json={'source_locator':'@firstchannel','folder':'Inbox'})
        assert result.status_code == 503
        result = client.post('/api/v3/telegram/connect', json={'source_locator':'@secondchannel','folder':'Inbox'})
        assert result.status_code == 503
        assert len(calls) == 1
        assert client.get('/api/v3/subscriptions').status_code == 200
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        result = client.post('/api/v3/telegram/connect', json={'source_locator':'@thirdchannel','folder':'Inbox'})
        assert result.status_code == 503
        assert len(calls) == 1
        wall[0] += 121
        client.post('/api/v3/telegram/connect', json={'source_locator':'@thirdchannel','folder':'Inbox'})
        assert len(calls) == 2


def test_feed_refresh_cannot_override_429_deadline_and_rss_keeps_working(tmp_path):
    from tests.support.telegram import settled
    calls = []
    wall = [1800000000.]
    blocked = [False]
    def response(request):
        if request.url.host == 'example.org':
            return httpx.Response(200, text='<rss version="2.0"><channel><title>RSS</title><item><guid>one</guid><link>https://example.org/one</link><title>One</title><description>RSS text</description></item></channel></rss>')
        calls.append(str(request.url))
        if blocked[0]:
            return httpx.Response(429, headers={'Retry-After':'120'})
        return httpx.Response(200, text=page([(11,'Original')]))
    options = dict(token='test', api_only=True, rss_enabled=True, rss_transport=httpx.MockTransport(response), wall_clock=lambda: wall[0])
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        feed = client.post('/api/v3/telegram/connect', json={'source_locator':'@fixture','folder':'Inbox'}).json()['feed_id']
        settled(client)
        blocked[0] = True
        client.post('/api/v3/collect', json={'scope':'feed','feed_id':feed})
        deadline = time.monotonic()+3
        count = len(calls)
        while len(calls) == count and time.monotonic() < deadline:
            time.sleep(.02)
        assert len(calls) == count+1
        for _ in range(3):
            client.post('/api/v3/collect', json={'scope':'feed','feed_id':feed})
            time.sleep(.05)
        assert len(calls) == count+1
        verified = client.post('/api/v3/feeds/verify', json={'url':'https://example.org/feed'}).json()
        result = client.post('/api/v3/feeds', json={'url':verified['candidate']['url'],'title':'RSS','verification':verified['verification'],'folder':'Inbox'})
        assert result.status_code == 200, result.text
        settled(client, 2)
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/v3/collect')
        time.sleep(.2)
        assert len(calls) == count+1


def test_media_budget_counts_avatar_and_stops_after_429_without_blocking_html(tmp_path):
    from tests.support.telegram import settled
    media_calls, html_calls = [], []
    wall = [1800000000.]
    avatar = '<img class="tgme_page_photo_image" src="https://cdn.example/avatar.jpg">'
    photos = ''.join('<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/'+name+'.jpg\')"></a>' for name in ('first','second'))
    def response(request):
        if request.url.host == 'cdn.example':
            media_calls.append((time.monotonic(), request.url.path))
            if request.url.path == '/first.jpg':
                return httpx.Response(429, headers={'Retry-After':'600'})
            return httpx.Response(200, content=b'image', headers={'content-type':'image/jpeg'})
        html_calls.append(str(request.url))
        return httpx.Response(200, text=avatar+page([(11,'Photos'+photos)]))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response), wall_clock=lambda:wall[0]), headers=HEADERS) as client:
        client.post('/api/v3/telegram/connect', json={'source_locator':'@fixture','folder':'Inbox'})
        deadline = time.monotonic()+5
        while not any(path=='/first.jpg' for _,path in media_calls) and time.monotonic()<deadline:
            time.sleep(.02)
        assert any(path=='/first.jpg' for _,path in media_calls)
        assert not any(path=='/second.jpg' for _,path in media_calls)
        assert all(b[0]-a[0]>=.48 for a,b in zip(media_calls,media_calls[1:]))
        client.post('/api/v3/telegram/connect', json={'source_locator':'@otherchannel','folder':'Inbox'})
        assert len(html_calls)>=3
        assert not any(path=='/second.jpg' for _,path in media_calls)


def test_http_date_and_long_retry_after_are_not_shortened(tmp_path):
    from email.utils import formatdate
    wall = [1800000000.]
    calls = []
    def response(request):
        calls.append(str(request.url))
        return httpx.Response(429, headers={'Retry-After':formatdate(wall[0]+172800, usegmt=True)})
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response), wall_clock=lambda:wall[0])
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/v3/telegram/connect', json={'source_locator':'@fixture','folder':'Inbox'})
    wall[0] += 86401
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/v3/telegram/connect', json={'source_locator':'@fixture','folder':'Inbox'})
        assert len(calls) == 1
        wall[0] += 86400
        client.post('/api/v3/telegram/connect', json={'source_locator':'@fixture','folder':'Inbox'})
        assert len(calls) == 2


def test_redirects_share_html_budget_and_429_without_header_increases_pause(tmp_path):
    wall, calls = [1800000000.], []
    phase = [0]
    def response(request):
        calls.append((time.monotonic(), str(request.url)))
        if phase[0] == 0 and request.url.host == 't.me':
            return httpx.Response(302, headers={'Location':'https://telegram.me/s/fixture'})
        if phase[0] == 0:
            return httpx.Response(200, text=page([]))
        return httpx.Response(429)
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response), wall_clock=lambda:wall[0]), headers=HEADERS) as client:
        assert client.post('/api/v3/telegram/connect', json={'source_locator':'@fixture','folder':'Inbox'}).status_code == 200
        assert calls[1][0]-calls[0][0] >= .48
        phase[0] = 1
        client.post('/api/v3/telegram/connect', json={'source_locator':'@secondchannel','folder':'Inbox'})
        count = len(calls)
        wall[0] += 61
        client.post('/api/v3/telegram/connect', json={'source_locator':'@secondchannel','folder':'Inbox'})
        assert len(calls) == count+1
        wall[0] += 61
        client.post('/api/v3/telegram/connect', json={'source_locator':'@secondchannel','folder':'Inbox'})
        assert len(calls) == count+1
