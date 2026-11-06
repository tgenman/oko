"""Shared Telegram HTTP admission observed through reader commands."""
import time

import httpx

from tests.support.app import create_app
from tests.support.telegram_web import page
from tests.support.v3 import TestClient

HEADERS = {'Authorization': 'Bearer test'}


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


def test_telegram_backoff_uses_configured_delay_without_shortening_existing_wait(tmp_path):
    from tests.support.storage import database
    import json
    calls=[]
    wall=[1800000000.]
    def response(request):
        calls.append(str(request.url))
        return httpx.Response(429,headers={'Retry-After':'120'})
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response),wall_clock=lambda:wall[0])
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global',json={'retry_initial_seconds':600,'retry_max_seconds':1200}).status_code==200
        assert client.post('/api/v3/telegram/connect',json={'source_locator':'@firstchannel','folder':'Inbox'}).status_code==503
        with database(tmp_path) as db:
            state=json.loads(db.execute("SELECT value FROM state WHERE key='telegram-http:html'").fetchone()[0])
        assert state['until']==wall[0]+600
        assert client.patch('/api/v3/settings/oko/global',json={'retry_initial_seconds':60}).status_code==200
        assert client.post('/api/v3/telegram/connect',json={'source_locator':'@secondchannel','folder':'Inbox'}).status_code==503
        assert len(calls)==1
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        assert client.post('/api/v3/telegram/connect',json={'source_locator':'@thirdchannel','folder':'Inbox'}).status_code==503
        assert len(calls)==1


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
        count = len(calls)  # The worker may perform the request before POST returns.
        reply = client.post('/api/v3/collect', json={'scope':'feed','feed_id':feed})
        assert reply.status_code == 200, reply.text
        deadline = time.monotonic()+3
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
