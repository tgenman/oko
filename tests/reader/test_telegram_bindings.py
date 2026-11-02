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
        assert state['id'] == feed and 'auto_read' not in state
        assert client.patch('/api/v3/feeds/'+feed, json={'auto_read':False}).status_code == 422
        assert client.get('/api/v3/telegram/profiles').status_code == 404






def test_account_access_is_not_accepted_by_http(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        for role in ('personal', 'collection'):
            result = client.post('/api/v3/telegram/connect', json={
                'source_locator': '@fixture', 'folder': 'Inbox',
                'access': {'kind': 'client', 'profile': role},
            })
            assert result.status_code == 422
            assert client.put('/api/v3/telegram/profiles/'+role, json={'configuration_ref':'fixture'}).status_code == 404
        assert not client.get('/api/v3/subscriptions').json()['feeds']
