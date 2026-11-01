import time
import httpx
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import wait_for
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
