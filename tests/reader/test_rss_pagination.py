"""RSS history through the reader HTTP boundary and synthetic external pages."""
import time
from collections import Counter
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.feed_url import HEADERS, add, verify


from tests.support.rss_history import page, walk


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


def test_initial_history_follows_rss_next_and_loads_selected_articles(tmp_path: Path):
    calls = Counter()
    def reply(request):
        calls[request.url.path] += 1
        if request.url.path == '/feed':
            return httpx.Response(200, text=page([3, 2], '<atom:link rel="next" href="/older"/>'))
        if request.url.path == '/older':
            return httpx.Response(200, text=page([2, 1]))
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        response = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode': 'count', 'count': 3})
        assert response.status_code == 200, response.text
        history = wait_history(client, response.json()['id'])
        assert history['progress']['available'] == 3
        assert history['progress']['loaded'] == 3
        assert calls['/older'] == 1
        assert len(client.get('/api/v3/cards').json()) == 3


def test_short_history_has_persisted_reason_and_failed_page_is_not_retried(tmp_path):
    calls = Counter()
    def reply(request):
        calls[request.url.path] += 1
        if request.url.path == '/feed':
            return httpx.Response(200, text=page([3, 2], '<atom:link rel="next" href="/broken"/>'))
        if request.url.path == '/broken':
            return httpx.Response(503)
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':3}).json()['id']
        history = wait_history(client, feed)
        assert history['progress']['loaded'] == 2
        assert history['progress']['stop_reason'] == 'request_failed'
        assert history['progress']['discovery_problem']['retry_at'] is None
        assert calls['/broken'] == 1
        assert client.post('/api/v3/collect', json={'scope':'feed','feed_id':feed}).status_code == 200
        time.sleep(.1)
        assert calls['/broken'] == 1
        assert client.get('/api/v3/settings/feed/'+feed).json()['feed']['history']['progress']['stop_reason'] == 'request_failed'
    with reader(tmp_path, reply) as client:
        history = wait_history(client, feed)
        assert history['progress']['stop_reason'] == 'request_failed'
        assert calls['/broken'] == 1


def test_existing_rss_can_select_count_or_date_without_restoring_viewed(tmp_path):
    def reply(request):
        return httpx.Response(200, text=page([3, 2, 1]) if request.url.path == '/feed' else '<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':1}).json()['id']
        wait_history(client, feed)
        card = client.get('/api/v3/cards').json()[0]
        assert client.post('/api/v3/materials/'+card['id']+'/viewed').status_code == 200
        response = client.post('/api/v3/feeds/'+feed+'/history', json={'mode':'count','count':'3'})
        assert response.status_code == 200, response.text
        progress = wait_history(client, feed)['progress']
        assert progress['available'] == 3
        assert progress['viewed'] == 1
        assert progress['loaded'] == 2
        assert len(client.get('/api/v3/cards').json()) == 2
        response = client.post('/api/v3/feeds/'+feed+'/history', json={'mode':'date','date':'2025-10-01','timezone':'UTC'})
        assert response.status_code == 422, response.text
        assert wait_history(client, feed)['progress']['available'] == 3


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
        assert len(client.get('/api/v3/cards').json()) == 6




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


@pytest.mark.parametrize('target', ['file:///private/history', 'http://['])
def test_invalid_continuation_preserves_current_page_publications(tmp_path, target):
    def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=page([3], f'<atom:link rel="next" href="{target}"/>'))
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':3}).json()['id']
        progress = wait_history(client, feed)['progress']
        assert progress['available'] == progress['loaded'] == 1
        assert progress['stop_reason'] == 'request_failed'


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
        assert history['progress']['available'] == 1
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
        assert history['progress']['available'] == 1
        assert all(not c['source_url'].endswith('/2') for c in client.get('/api/v3/cards').json())


def test_same_publication_with_changed_guid_does_not_consume_history_count(tmp_path):
    def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=page([3], '<atom:link rel="next" href="/older"/>'))
        if request.url.path == '/older':
            return httpx.Response(200, text=page([3,2,1]).replace('<guid>3</guid>','<guid>changed-guid</guid>'))
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':3}).json()['id']
        progress = wait_history(client, feed)['progress']
        assert progress['available'] == progress['loaded'] == 3
        assert progress['stop_reason'] == 'count_reached'


def test_new_arrivals_do_not_consume_fixed_history_count(tmp_path):
    def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=page([3], '<atom:link rel="next" href="/older"/>'))
        if request.url.path == '/older':
            return httpx.Response(200, text=page([4,2]).replace('Wed, 01 Oct 2025 12:00:00 GMT', 'Thu, 01 Oct 2099 12:00:00 GMT',1))
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':2}).json()['id']
        progress = wait_history(client, feed)['progress']
        assert progress['available'] == 2
        assert progress['loaded'] == 2
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            cards = client.get('/api/v3/cards').json()
            if len(cards)==3:
                break
            time.sleep(.02)
        assert len(cards)==3
        assert {c['source_url'] for c in cards} == {'https://source.test/post/3','https://source.test/post/2','https://source.test/post/4'}


def test_root_failure_finishes_with_zero_without_retry(tmp_path):
    calls = Counter()
    def reply(request):
        calls[request.url.path] += 1
        return httpx.Response(200, text=page([])) if calls['/feed'] == 1 else httpx.Response(503)
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':200}).json()['id']
        progress = wait_history(client, feed)['progress']
        assert progress['available'] == progress['loaded'] == 0
        assert progress['stop_reason'] == 'request_failed'
        assert progress['requests'] == 1
        assert calls['/feed'] == 2  # Verification, then one discovery request.


def test_rss_channel_and_link_xml_base_select_the_correct_archive(tmp_path):
    calls = []
    def reply(request):
        path = request.url.path
        calls.append(path)
        if path == '/feed':
            xml = page([3], '<atom:link xml:base="monthly/" rel="prev-archive" href="older"/>')
            return httpx.Response(200, text=xml.replace('<channel>', '<channel xml:base="/archives/">'))
        if path == '/archives/monthly/older':
            return httpx.Response(200, text=page([2]))
        assert not path.startswith('/archives/')
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':200}).json()['id']
        progress = wait_history(client, feed)['progress']
        assert progress['available'] == progress['loaded'] == 2
        assert progress['stop_reason'] == 'archive_end'
        assert calls.count('/archives/monthly/older') == 1


def test_ambiguous_continuation_stops_after_saving_current_page(tmp_path):
    def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=page([3], '<atom:link rel="next" href="/a"/><atom:link rel="next" href="/b"/>'))
        assert request.url.path not in ('/a','/b')
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':2}).json()['id']
        progress = wait_history(client, feed)['progress']
        assert progress['available'] == progress['loaded'] == 1
        assert progress['stop_reason'] == 'ambiguous_continuation'


def test_explicit_history_after_upgrade_replaces_legacy_window_cursor(tmp_path):
    import json
    from tests.support.subscriptions import saved_sources
    from oko.storage.store import Store
    from oko.subscriptions.history import InitialHistory
    saved_sources(tmp_path, [('https://source.test/feed','History','News')])
    # This is an on-disk pre-upgrade fixture, prepared before starting the reader.
    # All behavior below is observed through HTTP, not through storage internals.
    store = Store(tmp_path, lambda _:None)
    feed = store.subscriptions.feeds()[0]['id']
    with store.db:
        store.subscriptions.history.configure(feed, InitialHistory(mode='count', count=1))
        store.db.execute('UPDATE initial_history SET frozen=1 WHERE feed=?', (feed,))
        store.db.execute('INSERT INTO state VALUES (?,?)', ('cursor:'+feed, json.dumps(dict(history_ids=['3'], excluded=['2','1'], since=time.time(), warnings=[]))))
    store.db.close()
    def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=page([3,2], '<atom:link rel="next" href="/older"/>'))
        if request.url.path == '/older':
            return httpx.Response(200, text=page([1]))
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        old = client.get('/api/v3/settings/feed/'+feed).json()['feed']['history']
        assert old['progress']['stop_reason'] is None
        response = client.post('/api/v3/feeds/'+feed+'/history', json={'mode':'count','count':'3'})
        assert response.status_code == 200, response.text
        history = wait_history(client, feed, timeout=2)
        assert history['progress']['available'] == history['progress']['loaded'] == 3
        assert history['progress']['stop_reason'] == 'count_reached'


@pytest.mark.parametrize('location, available', [('channel',1), ('root',0), ('entry',0)])
def test_malformed_xml_base_finishes_attempt_without_retry(tmp_path, location, available):
    calls = 0
    def reply(request):
        nonlocal calls
        if request.url.path == '/feed':
            calls += 1
            # Verification receives a valid feed; discovery gets an invalid base.
            xml = page([3], '<atom:link rel="next" href="older"/>')
            if calls > 1:
                element = {'channel':'channel', 'root':'rss', 'entry':'item'}[location]
                opening = '<'+element
                xml = xml.replace(opening, opening+' xml:base="http://["', 1)
            return httpx.Response(200, text=xml)
        return httpx.Response(200, text='<article>Full article</article>')
    with reader(tmp_path, reply) as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':2}).json()['id']
        progress = wait_history(client, feed, timeout=2)['progress']
        assert progress['stop_reason'] == 'request_failed'
        assert progress['available'] == progress['loaded'] == available
        assert progress['requests'] == 1


def test_page_budget_resumes_from_disk_and_is_visible_through_http(tmp_path):
    import asyncio
    from oko.storage.store import Store
    from oko.subscriptions.history import InitialHistory
    from tests.support.subscriptions import saved_sources

    calls = []
    def reply(request):
        calls.append(request.url.path)
        number = 1 if request.url.path == '/feed' else int(request.url.path.rsplit('/', 1)[1])
        return httpx.Response(200, text=page([], f'<atom:link rel="next" href="/pages/{number+1}"/>'))

    # Prepare a real adapter cursor, not an invented near-limit count. The full
    # 1000-step walk is separately exercised without 1000 scheduler sleeps.
    saved_sources(tmp_path, [('https://source.test/feed', 'History', 'News')])
    store = Store(tmp_path, lambda _: None)
    try:
        feed = store.subscriptions.feeds()[0]['id']
        with store.db:
            selection = store.subscriptions.history.start(feed, InitialHistory(mode='count', count=200))
        result, publications = asyncio.run(walk(reply, selection=selection, stop_after=999))
        assert publications == [] and result.continuation == 0
        store.enqueue_discovery(feed)
        job = store.next_job(0, feeds=[feed])
        assert job is not None and job.kind == 'discover'
        store.discovered([], result.cursor, job, feed)
    finally:
        store.db.close()
    with reader(tmp_path, reply) as client:
        progress = wait_history(client, feed)['progress']
        assert progress['stop_reason'] == 'page_limit'
        assert progress['requests'] == 1000
        assert progress['available'] == progress['loaded'] == 0
        assert calls == ['/feed', *[f'/pages/{n}' for n in range(2, 1001)]]
    calls.clear()
    with reader(tmp_path, reply) as client:
        assert wait_history(client, feed)['progress']['stop_reason'] == 'page_limit'
        assert '/pages/1001' not in calls
