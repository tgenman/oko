from tests.support.cards import read_all_cards
from tests.support.presence import presence, close_presence
import time
import httpx
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import Clock, wait_for
from tests.support.feed_url import HEADERS, verify, add


def test_initial_history_retry_partial_and_terminal_outcomes_survive_restart(tmp_path):
    def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text='<rss><channel><title>История</title>' + ''.join(f'<item><guid>{n}</guid><title>{n}</title><link>https://source.test/{n}</link></item>' for n in ['good', 'partial', 'retry', 'deleted']) + '</channel></rss>')
        return httpx.Response({'retry': 503, 'deleted': 404}.get(request.url.path[1:], 200), text='<article>Текст</article>' if request.url.path == '/good' else '<p>Анонс</p>')
    def reader():
        return TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS)
    with reader() as client:
        feed = add(client, verify(client, 'https://source.test/feed')).json()
        def completed_history():
            history = client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']
            return history if history['state'] == 'complete' else None
        history = wait_for(client, completed_history)
        assert history['progress']['retrying'] == 0
        assert history['progress']['unavailable'] == 0
        assert history['progress']['loaded'] == 4
        assert history['progress']['incomplete'] >= 1
        assert history['state'] == 'complete'
        assert history['progress']['errors'] == []
    with reader() as client:
        history = client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']
        assert history['progress']['retrying'] == history['progress']['unavailable'] == 0
        assert history['progress']['loaded'] == 4


def test_temporary_error_retries_and_completes_without_waiting_for_ai(tmp_path):
    clock = Clock()
    calls = 0
    def reply(request):
        nonlocal calls
        if request.url.path == '/feed':
            return httpx.Response(200, text='<rss><channel><title>История</title><item><guid>one</guid><title>One</title><link>https://source.test/one</link></item></channel></rss>')
        calls += 1
        return httpx.Response(503) if calls == 1 else httpx.Response(200, text='<article>Полный текст</article>')
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply), clock=clock,wall_clock=clock,api_only=True), headers=HEADERS) as client:
        feed = add(client, verify(client, 'https://source.test/feed')).json()
        def progress():
            return client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']
        wait_for(client, lambda: calls==1)
        assert progress()['progress']['retrying']==0
        assert calls == 1
        clock.now += 61.1
        history = wait_for(client, lambda: value if calls==2 and (value := progress())['state'] == 'complete' else None)
        assert calls == 2
        assert history['progress']['loaded'] == 1
        assert history['state'] == 'complete'
        assert history['progress']['retrying'] == 0
        assert history['progress']['errors'] == [] == []


def test_terminal_unavailability_finishes_and_is_not_retried_on_rediscovery(tmp_path):
    calls = 0
    def reply(request):
        nonlocal calls
        if request.url.path == '/feed':
            return httpx.Response(200, text='<rss><channel><title>История</title><item><guid>one</guid><title>One</title><description>Сохранившийся анонс</description><link>https://source.test/one</link></item></channel></rss>')
        calls += 1
        return httpx.Response(410)
    def reader():
        return TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS)
    with reader() as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history={'mode':'count','count':1}).json()
        for _ in range(100):
            history = client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']
            if history['state'] == 'complete':
                break
            time.sleep(.01)
        assert history['state'] == 'complete'
        assert history['progress']['loaded'] == 1
        assert history['progress']['unavailable'] == 0
        wait_for(client,lambda:calls==1)
        assert client.get('/api/v3/materials/' + read_all_cards(client, '/api/v3/cards')[0]['id']).json()['content']['text'] == 'Сохранившийся анонс'
    with reader() as client:
        presence(client, 'reader')
        time.sleep(.3)
        history = client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']
        assert history['progress']['unavailable'] == 0
        assert history['state'] == 'complete'
        assert calls == 1
