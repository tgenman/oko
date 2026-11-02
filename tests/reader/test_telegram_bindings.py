"""Public Telegram binding and duplicate claims through HTTP."""
from tests.support.app import create_app
from tests.support.v3 import TestClient

HEADERS = {'Authorization': 'Bearer test'}


def test_web_binding_needs_no_account_and_duplicates_are_normalized(tmp_path):
    import httpx
    from tests.support.telegram import settled
    from tests.support.telegram_web import page
    raw = page([(392, 'Public post')]).replace('fixture', 'telegram').encode()
    calls = []
    def reply(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=raw)
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        response = client.post('/api/v3/telegram/connect', json={'source_locator':'https://t.me/s/Telegram/392', 'folder':'Web'})
        assert response.status_code == 200, response.text
        feed = response.json()['feed_id']
        assert response.json()['access'] == {'kind':'web', 'username':'telegram', 'preview_url':'https://t.me/s/telegram'}
        card = settled(client)[0]
        material = client.get('/api/v3/publication/' + card['id']).json()
        assert material['source']['message_ids'] == ['392']
        collected_calls = len(calls)
        assert collected_calls >= 1
        assert client.post('/api/v3/telegram/connect', json={'source_locator':'@TELEGRAM', 'folder':'Other'}).status_code == 409
        assert len(calls) == collected_calls
        state = client.get('/api/v3/subscriptions').json()['feeds'][0]
        assert state['id'] == feed
