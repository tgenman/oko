import time
import httpx
from fastapi.testclient import TestClient
from tests.support.app import create_app
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
        for _ in range(150):
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history.get('retrying') == 1 and history.get('unavailable') == 1:
                break
            time.sleep(.02)
        assert history['retrying'] == 1
        assert history['unavailable'] == 1
        assert history['loaded'] == 2
        assert history['incomplete'] == 1
        assert history['state'] == 'loading'
        assert history['errors']
    with reader() as client:
        history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
        assert history['retrying'] == history['unavailable'] == 1
        assert history['loaded'] == 2


def test_temporary_error_retries_and_completes_without_waiting_for_ai(tmp_path):
    calls = 0
    def reply(request):
        nonlocal calls
        if request.url.path == '/feed':
            return httpx.Response(200, text='<rss><channel><title>История</title><item><guid>one</guid><title>One</title><link>https://source.test/one</link></item></channel></rss>')
        calls += 1
        return httpx.Response(429, headers={'retry-after': '1'}) if calls == 1 else httpx.Response(200, text='<article>Полный текст</article>')
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        feed = add(client, verify(client, 'https://source.test/feed')).json()
        states = []
        for _ in range(200):
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            states.append(history['retrying'])
            if history['state'] == 'done':
                break
            time.sleep(.015)
        assert 1 in states
        assert calls == 2
        assert history['loaded'] == 1
        assert history['state'] == 'done'
        assert history['retrying'] == 0
        assert history['errors'] == []


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
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history['state'] == 'done':
                break
            time.sleep(.01)
        assert history['state'] == 'done'
        assert history['loaded'] == 0
        assert history['unavailable'] == 1
        assert calls == 1
        assert client.get('/api/materials/' + client.get('/api/cards').json()[0]['id']).json()['text'] == 'Сохранившийся анонс'
    with reader() as client:
        client.post('/api/tabs/reader/heartbeat')
        time.sleep(.3)
        history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
        assert history['unavailable'] == 1
        assert history['state'] == 'done'
        assert calls == 1
