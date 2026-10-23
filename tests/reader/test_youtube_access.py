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

from oko.collection.contracts import SourceError
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
    return [client.get('/api/v3/materials/' + row['id']).json() for row in client.get('/api/v3/cards').json()]


def heartbeat(client):
    assert presence(client, 'reader').status_code in (200,201)


def test_shared_spacing_and_cached_text_do_not_block_other_sources(tmp_path):
    clock = Clock()
    captions = Captions(clock, ['Первый текст', 'Второй текст'])
    with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
        add(client)
        add(client, 'UC' + 'b' * 22)
        heartbeat(client)
        eventually(lambda: len(captions.calls) == 1 and any(m['source'].get('captions') == 'available' for m in materials(client)))
        add(client, 'https://rss.test/feed')
        eventually(lambda: any(m['content'].get('text') == 'Независимый текст' for m in materials(client)))
        assert len(captions.calls) == 1
        saved = next(m for m in materials(client) if m['source'].get('captions') == 'available')
        client.post('/api/v3/materials/' + saved['id'] + '/retry')
        eventually(lambda: not client.get('/api/v3/materials/' + saved['id']).json()['content'].get('loading'))
        assert len(captions.calls) == 1
        clock.advance(9.9)
        time.sleep(.1)
        assert len(captions.calls) == 1
        clock.advance(.1)
        eventually(lambda: len(captions.calls) == 2)
        assert captions.calls[1][1] - captions.calls[0][1] >= 10


@pytest.mark.parametrize('publication_pipeline',[False,True])
def test_block_is_shared_persistent_and_retry_cannot_bypass_it(tmp_path,publication_pipeline):
    if publication_pipeline:
        from tests.reader.test_independent_media import migrated_root
        tmp_path=migrated_root(tmp_path)
    clock = Clock()
    captions = Captions(clock, [CaptionsBlocked(), 'После паузы', 'Следующий'])
    with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
        add(client)
        add(client, 'UC' + 'b' * 22)
        heartbeat(client)
        def paused():
            if client.get('/api/v3/youtube').json()['admission']=='paused':
                return True
            if publication_pipeline:
                clock.advance(1)  # Common discovery commits its initial cursor before the next portion.
            return False
        eventually(paused)
        item = eventually(lambda: next((m for m in materials(client) if m['source'].get('captions') == 'error'), None))
        assert datetime.fromisoformat(client.get('/api/v3/youtube').json()['retry_at'].replace('Z','+00:00')).timestamp() == captions.calls[0][1] + 900
        client.post('/api/v3/materials/' + item['id'] + '/retry')
        client.patch('/api/v3/youtube', json={'mode': 'proxy', 'address': 'http://proxy.test:8000', 'password': 'secret'})
        add(client, 'https://rss.test/feed')
        def independent():
            if any('Независимый текст' in m['content'].get('text','') for m in materials(client)):
                return True
            if publication_pipeline:
                clock.tick+=1
            return False
        eventually(independent)
        assert len(captions.calls) == 1
    clock.tick = 0  # A new monotonic origin must not expire the durable pause.
    with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
        heartbeat(client)
        clock.advance(899)
        time.sleep(.1)
        assert len(captions.calls) == 1
        assert (client.get('/api/v3/youtube').json()['admission']=='paused')
        clock.advance(1)
        eventually(lambda: len(captions.calls) == 2 and client.get('/api/v3/youtube').json()['strikes'] == 0)
        time.sleep(.1)
        assert len(captions.calls) == 2
        clock.advance(10)
        eventually(lambda: len(captions.calls) == 3)


def test_exhausted_blocked_material_can_be_retried_by_manual_refresh(tmp_path):
    clock = Clock()
    captions = Captions(clock, [CaptionsBlocked(), CaptionsBlocked(), CaptionsBlocked(), 'Успех'])
    with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
        add(client)
        heartbeat(client)
        for count, delay in enumerate((900, 1800, 3600), 1):
            eventually(lambda: len(captions.calls) == count and client.get('/api/v3/youtube').json()['strikes'] == count)
            assert datetime.fromisoformat(client.get('/api/v3/youtube').json()['retry_at'].replace('Z','+00:00')).timestamp() == clock.wall + delay
            clock.advance(delay)
        item = eventually(lambda:next((m for m in materials(client) if m['source']['captions']=='error' and not m['content']['loading']),None))
        client.post('/api/v3/collect')
        eventually(lambda: materials(client)[0]['source'].get('captions') == 'available')
        assert len(captions.calls) == 4


def test_retry_after_and_backoff_cap(tmp_path):
    clock = Clock()
    captions = Captions(clock, [CaptionsBlocked(30_000)] + [CaptionsBlocked() for _ in range(7)])
    with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
        add(client)
        heartbeat(client)
        for count, delay in enumerate((30000, 1800, 3600, 7200, 14400, 21600, 21600), 1):
            eventually(lambda: len(captions.calls) == count and datetime.fromisoformat(client.get('/api/v3/youtube').json()['retry_at'].replace('Z','+00:00')).timestamp() == clock.wall + delay)
            item = eventually(lambda: next((m for m in materials(client) if m['source'].get('captions') == 'error'), None))
            # Explicit cycles let the test exercise the global cap beyond the per-item budget.
            client.post('/api/v3/materials/' + item['id'] + '/retry')
            clock.advance(delay)


@pytest.mark.parametrize('outcome,state', [(None, 'unavailable'), (SourceError('Сетевой сбой'), 'error')])
def test_missing_and_network_failure_do_not_pause_or_automatically_retry(tmp_path, outcome, state):
    clock = Clock()
    captions = Captions(clock, [outcome, 'Другой материал'])
    with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
        add(client)
        add(client, 'UC' + 'b' * 22)
        heartbeat(client)
        eventually(lambda: any(m['source'].get('captions') == state for m in materials(client)))
        assert not (client.get('/api/v3/youtube').json()['admission']=='paused')
        clock.advance(10)
        eventually(lambda: len(captions.calls) == 2)
        clock.advance(1000)
        # Automatic discovery leaves the terminal attempt alone. Manual refresh retries it.
        time.sleep(.2)
        assert len(captions.calls) == 2
        client.post('/api/v3/collect')
        eventually(lambda:len(captions.calls)==3)


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


def test_proxy_change_rejects_late_block_and_prevents_overlapping_calls(tmp_path):
    clock = Clock()
    started, release = threading.Event(), threading.Event()

    class Late:
        calls = 0

        async def fetch(self, video):
            self.calls += 1
            if self.calls > 1:
                return 'Новое подключение'
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(.01)
                except asyncio.CancelledError:
                    pass
            raise CaptionsBlocked()

    captions = Late()
    try:
        with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
            add(client)
            heartbeat(client)
            assert started.wait(3)
            client.patch('/api/v3/youtube', json={'mode': 'proxy', 'address': 'http://proxy.test:8000'})
            clock.advance(10)
            time.sleep(.15)
            assert captions.calls == 1
            release.set()
            time.sleep(.1)
            clock.advance(1)
            eventually(lambda: materials(client)[0]['content'].get('text') == 'Новое подключение')
            assert not (client.get('/api/v3/youtube').json()['admission']=='paused')
    finally:
        release.set()


def test_viewed_waiting_material_and_closed_reader_do_not_resume(tmp_path):
    clock = Clock()
    captions = Captions(clock, [CaptionsBlocked()])
    with TestClient(app(tmp_path, clock, captions), headers=HEADERS) as client:
        add(client)
        heartbeat(client)
        item = eventually(lambda: next((m for m in materials(client) if m['source'].get('captions') == 'error'), None))
        close_presence(client, 'reader')
        clock.advance(900)
        time.sleep(.1)
        assert len(captions.calls) == 1
        client.post('/api/v3/materials/' + item['id'] + '/viewed')
        heartbeat(client)
        time.sleep(.1)
        assert client.get('/api/v3/cards').json() == []
        assert len(captions.calls) == 1
