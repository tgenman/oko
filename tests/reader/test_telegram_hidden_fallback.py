"""Hidden Telegram fallback is not an attachment; active restrictions still are."""
import httpx
import pytest

from tests.support.app import create_app
from tests.support.telegram import configure_fast_web, settled
from tests.support.telegram_web import hidden_fallback_page
from tests.support.v3 import TestClient

TEXT = 'Available text <tg-emoji emoji-id="1"><b>🔵</b></tg-emoji> <a href="https://example.test/source">Source</a>'


@pytest.mark.parametrize('active', [False, True])
def test_hidden_fallback_is_ignored_but_active_restriction_is_not(tmp_path, active):
    def response(request):
        return httpx.Response(200, text=hidden_fallback_page(TEXT, active))
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(tmp_path, **options), headers={'Authorization': 'Bearer test'}) as client:
        configure_fast_web(client)
        added = client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox')).json()
        identity = settled(client)[0]['id']
        content = client.get('/api/v3/publication/' + identity).json()
        assert content['completeness']['state'] == ('incomplete' if active else 'complete')
        assert 'Available text 🔵' in content['text'] and '[Source](https://example.test/source)' in content['text']
        assert content['external_attachments'] == [] and [media for media in content['media'] if media['role']=='attachment'] == []
        assert content['post_web_unavailable'] is False
        state = client.get('/api/v3/collection-status/' + added['feed_id']).json()['source']
        assert state['state'] == ('error' if active else 'complete')
        assert client.get('/api/v3/status/details').json()['problems']['count'] == (1 if active else 0)
