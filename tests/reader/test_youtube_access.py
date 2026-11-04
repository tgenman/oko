from tests.support.cards import read_all_cards
from datetime import datetime
from tests.support.presence import presence, close_presence
from tests.support.subscriptions import add_sources
import asyncio
import json
import stat
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from oko.collection.youtube.access import CaptionsBlocked
from tests.support.app import create_app
from tests.support.youtube import CHANNEL, PNG, VIDEO, feed_xml

HEADERS = {'Authorization': 'Bearer test'}


class Clock:
    tick = 100.0
    wall = 1_900_000_000.0

    def advance(self, seconds):
        self.tick += seconds
        self.wall += seconds


class Captions:
    def __init__(self, clock, outcomes):
        self.clock, self.outcomes, self.calls = clock, list(outcomes), []

    async def fetch(self, video):
        self.calls.append((video, self.clock.wall))
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def external(request):
    if request.url.host == 'i.ytimg.com':
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    if request.url.host == 'rss.test':
        return httpx.Response(200, text='<rss><channel><title>Другой источник</title><item><guid>one</guid><title>Статья</title><link>https://rss.test/article</link><description>Текст</description></item></channel></rss>' if request.url.path == '/feed' else '<article>Независимый текст</article>')
    channel = request.url.params.get('channel_id') or 'UC' + request.url.params.get('playlist_id', '')[4:]
    video = VIDEO if channel == CHANNEL else 'lmnopqrstuv'
    return httpx.Response(200, text=feed_xml().replace(VIDEO, video))


def app(root, clock, captions):
    return create_app(root, token='test', clock=lambda: clock.tick, wall_clock=lambda: clock.wall,
                      lease=1_000_000, grace=1_000_000, rss_enabled=True,
                      rss_transport=httpx.MockTransport(external), youtube_captions=captions)


def add(client, channel=CHANNEL):
    url = f'https://www.youtube.com/feeds/videos.xml?channel_id={channel}' if channel.startswith('UC') else channel
    response = add_sources(client, [(f'{url}', 'Источник', 'Тема')])
    assert response.status_code == 200, response.text
    feed = next(f for f in response.json()['feeds'] if url in f['original_urls'])
    if channel.startswith('UC'):
        client.patch('/api/v3/feeds/' + feed['id'], json={'importance': 'high'})
    return feed['id']


def eventually(predicate):
    for _ in range(300):
        result = predicate()
        if result:
            return result
        time.sleep(.01)
    raise AssertionError('Condition did not become true')


def materials(client):
    return [client.get('/api/v3/publication/' + row['id']).json() for row in read_all_cards(client, '/api/v3/cards')]


def heartbeat(client):
    assert presence(client, 'reader').status_code in (200,201)


def test_block_is_shared_persistent_and_retry_cannot_bypass_it(tmp_path):
    from tests.support.publication_pipeline import publication_root
    tmp_path=publication_root(tmp_path)
    clock = Clock()
    captions = Captions(clock, [CaptionsBlocked(), 'После паузы', 'Следующий'])
    with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
        add(client)
        add(client, 'UC' + 'b' * 22)
        heartbeat(client)
        def paused():
            if client.get('/api/v3/youtube').json()['admission']=='paused':
                return True
            clock.advance(1)  # Common discovery commits its initial cursor before the next portion.
            return False
        eventually(paused)
        item = eventually(lambda: next((m for m in materials(client) if m['source'].get('captions') == 'pending'), None))
        retry_at = datetime.fromisoformat(client.get('/api/v3/youtube').json()['retry_at'].replace('Z','+00:00')).timestamp()
        assert retry_at == captions.calls[0][1] + 900
        client.post('/api/v3/materials/' + item['id'] + '/retry')
        client.patch('/api/v3/youtube', json={'mode': 'proxy', 'address': 'http://proxy.test:8000', 'password': 'secret'})
        add(client, 'https://rss.test/feed')
        def independent():
            if any('Независимый текст' in m.get('text','') for m in materials(client)):
                return True
            clock.tick+=1
            return False
        eventually(independent)
        assert len(captions.calls) == 1
        clock.advance(1)  # Some wall time passes before restart, independently of polling.
    clock.tick = 0  # A new monotonic origin must not expire the durable pause.
    with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
        heartbeat(client)
        clock.advance(retry_at - clock.wall - 1)
        time.sleep(.1)
        assert len(captions.calls) == 1
        assert (client.get('/api/v3/youtube').json()['admission']=='paused')
        clock.advance(1)
        eventually(lambda: len(captions.calls) == 2 and client.get('/api/v3/youtube').json()['strikes'] == 0)
        time.sleep(.1)
        assert len(captions.calls) == 2
        clock.advance(10)
        eventually(lambda: len(captions.calls) == 3)


def test_proxy_configuration_redacts_preserves_and_clears_secrets(tmp_path):
    clock = Clock()
    captions = Captions(clock, [])
    with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
        assert client.get('/api/v3/youtube').json()['mode'] == 'direct'
        for bad in ({'mode': 'proxy', 'address': 'socks5://localhost:1'}, {'mode': 'proxy', 'address': 'http://user:secret@proxy.test:1'}, {'password': 123}):
            response = client.patch('/api/v3/youtube', json=bad)
            assert response.status_code == (422 if 'password' in bad else 400)
            assert 'secret' not in response.text
        response = client.patch('/api/v3/youtube', json={'mode': 'proxy', 'address': 'http://proxy.test:8000', 'credentials':{'action':'replace','username':'private-user','password':'private-password'}})
        assert response.status_code == 200
        assert response.json()['has_password']
        assert 'private-' not in response.text
        assert not captions.calls
        assert stat.S_IMODE((tmp_path / 'youtube-proxy.json').stat().st_mode) == 0o600
        client.patch('/api/v3/youtube', json={'mode': 'direct'})
    with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
        config = client.get('/api/v3/youtube').json()
        assert config['mode'] == 'direct' and config['has_password'] and config['has_username']
        client.patch('/api/v3/youtube', json={'mode': 'proxy'})
        assert client.get('/api/v3/youtube').json()['has_password']
        client.patch('/api/v3/youtube', json={'credentials':{'action':'clear'}})
        assert not client.get('/api/v3/youtube').json()['has_password']
        assert not client.get('/api/v3/youtube').json()['has_username']
        assert 'private-' not in json.dumps(client.get('/api/v3/diagnostics').json())


def test_closed_browser_allows_retry_but_viewed_material_does_not_resume(tmp_path):
    clock = Clock()
    captions = Captions(clock, [CaptionsBlocked(), CaptionsBlocked()])
    with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
        # Isolate pending-caption cancellation from a later automatic rescan.
        assert client.patch('/api/v3/settings/oko/global', json={'feed_refresh_minutes':1440}).status_code == 200
        add(client)
        heartbeat(client)
        item = eventually(lambda: next((m for m in materials(client) if len(captions.calls)==1 and m['source'].get('captions') == 'pending' and not m['loading']), None))
        assert close_presence(client, 'reader').status_code == 409
        clock.advance(900)
        eventually(lambda: len(captions.calls) == 2 and any(m['source'].get('captions') == 'pending' and not m['loading'] for m in materials(client)))
        assert client.get('/api/v3/status').json()['work_allowed'] is True
        client.put('/api/v3/power',json={'enabled':False})
        client.post('/api/v3/materials/' + item['id'] + '/viewed')
        assert client.put('/api/v3/power',json={'enabled':True}).status_code == 200
        clock.advance(900)
        time.sleep(.1)
        assert client.get('/api/v3/publication/'+item['id']).status_code==404
        assert read_all_cards(client, '/api/v3/cards') == []
        assert len(captions.calls) == 2
