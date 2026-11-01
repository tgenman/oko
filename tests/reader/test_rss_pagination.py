"""RSS history through the reader HTTP boundary and synthetic external pages."""
from tests.support.cards import read_all_cards
import time
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.feed_url import HEADERS, add, verify


from tests.support.rss_history import page


def reader(root, reply):
    return TestClient(create_app(root, token='test', api_only=True, rss_enabled=True,
                                 rss_transport=httpx.MockTransport(reply)), headers=HEADERS)


def wait_history(client, identity, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get('/api/v3/settings/feed/' + identity)
        assert response.status_code == 200, response.text
        history = response.json()['feed']['history']
        if history['state'] == 'complete':
            return history
        time.sleep(.02)
    raise AssertionError(history)


@pytest.mark.parametrize('atom', [False, True])
def test_rss_atom_pages_persist_selected_articles_through_http(tmp_path, atom):
    calls = Counter()
    def render(ids, links=''):
        return page(ids, links if atom else links.replace('<link ', '<atom:link '), atom=atom)
    def reply(request):
        path = request.url.path
        calls[path] += 1
        if path == '/feed':
            return httpx.Response(302, headers={'location':'/pages/start'})
        if path == '/pages/start':
            return httpx.Response(200, text=render([6,5], '<link rel="next" href="empty"/><link rel="prev-archive" href="../archive/first"/>'))
        if path == '/pages/empty':
            return httpx.Response(200, text=render([], '<link rel="next" href="second"/>'))
        if path == '/pages/second':
            return httpx.Response(200, text=render([5,4], '<link rel="prev-archive" href="../archive/first"/>'))
        if path == '/archive/first':
            return httpx.Response(200, text=render([3,2,1], '<link rel="next" href="should-not-load"/>').replace('<feed ', '<feed xml:base="https://source.test/archive/" '))
        assert not path.endswith('should-not-load')
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count', 'count':6}).json()['id']
        progress = wait_history(client, feed)['progress']
        assert progress['available'] == progress['loaded'] == 6
        assert progress['stop_reason'] == 'count_reached'
        assert calls['/pages/empty'] == calls['/pages/second'] == calls['/archive/first'] == 1
        assert len(read_all_cards(client, '/api/v3/cards')) == 6


def test_cycle_preserves_partial_history(tmp_path):
    def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=page([3], '<atom:link rel="next" href="/older"/>'))
        if request.url.path == '/older':
            return httpx.Response(200, text=page([2], '<atom:link rel="next" href="/feed"/>'))
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':3}).json()['id']
        progress = wait_history(client, feed)['progress']
        assert progress['stop_reason'] == 'cycle'
        assert progress['loaded'] == 2


def test_power_and_restart_preserve_unfinished_rss_history(tmp_path):
    import asyncio
    import threading
    started = threading.Event()
    blocked = True
    async def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=page([3], '<atom:link rel="next" href="/older"/>'))
        if request.url.path == '/older':
            started.set()
            while blocked:
                await asyncio.sleep(.01)
            return httpx.Response(200, text=page([2]))
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':2}).json()['id']
        assert started.wait(5)
        assert client.put('/api/v3/power', json={'enabled':False}).status_code == 200
        history = client.get('/api/v3/settings/feed/'+feed).json()['feed']['history']
        assert history['progress']['available'] == 0  # No discoveries commit until cursor_before fixes the window.
        assert history['progress']['loaded'] == 0
    blocked = False
    with reader(tmp_path, reply) as client:
        assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
        assert client.put('/api/v3/power', json={'enabled':True}).status_code == 200
        history = wait_history(client, feed)
        assert history['progress']['loaded'] == 2
        assert history['progress']['stop_reason'] == 'count_reached'


def test_cancel_rejects_late_page_and_requires_new_selection(tmp_path):
    import asyncio
    import threading
    started = threading.Event()
    async def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=page([3], '<atom:link rel="next" href="/older"/>'))
        if request.url.path == '/older':
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return httpx.Response(200, text=page([2]))  # Deliberately ignore cancellation.
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':2}).json()['id']
        assert started.wait(5)
        assert client.delete('/api/v3/feeds/'+feed+'/history').status_code == 200
        time.sleep(.1)
        history = client.get('/api/v3/settings/feed/'+feed).json()['feed']['history']
        assert history['state'] == 'cancelled'
        assert history['progress']['stop_reason'] == 'cancelled'
        assert history['progress']['available'] == 0  # No discoveries commit until cursor_before fixes the window.
        assert all(not c['source_url'].endswith('/2') for c in read_all_cards(client, '/api/v3/cards'))
