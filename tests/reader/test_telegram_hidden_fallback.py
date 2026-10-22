"""Hidden Telegram fallback is not an attachment; active restrictions still are."""
import httpx
import pytest

from tests.support.app import create_app
from tests.support.telegram import configure_fast_web, settled
from tests.support.telegram_web import hidden_fallback_page, page, mark_post_unavailable
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
        content = client.get('/api/v3/materials/' + identity).json()['content']
        assert content['completeness']['state'] == ('incomplete' if active else 'complete')
        assert 'Available text 🔵' in content['text'] and '[Source](https://example.test/source)' in content['text']
        assert content['external_attachments'] == [] and content['attachments'] == []
        assert content['post_web_unavailable'] is False
        state = client.get('/api/v3/collection-status/' + added['feed_id']).json()['source']
        assert state['state'] == ('error' if active else 'complete')
        assert client.get('/api/v3/status').json()['problems']['count'] == (1 if active else 0)


def test_existing_false_error_clears_without_inventing_an_author_edit(tmp_path):
    active = [True]
    def response(request):
        return httpx.Response(200, text=hidden_fallback_page(TEXT, active[0]))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers={'Authorization': 'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        identity = settled(client)[0]['id']
        path = '/api/v3/materials/' + identity
        original = client.get(path).json()['content']['text']
        active[0] = False
        client.post(path + '/retry'); settled(client)
        content = client.get(path).json()['content']
        assert content['text'] == original
        assert content['completeness']['state'] == 'complete'
        assert content['previous_versions'] == []
        assert content['changed'] is False
        assert client.get('/api/v3/status').json()['problems']['count'] == 0


def test_forward_only_standalone_placeholder_saves_confirmed_restriction(tmp_path):
    def response(request):
        html = mark_post_unavailable(page([(11, '')]), 'fixture/11')
        html = html.replace('<div class="tgme_widget_message_text"></div>', '<div class="tgme_widget_message_forwarded_from">Forwarded from Original author</div>')
        return httpx.Response(200, text=html)
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers={'Authorization': 'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        content = client.get('/api/v3/materials/' + settled(client)[0]['id']).json()['content']
        assert content['post_web_unavailable'] is False
        assert content['completeness']['state'] == 'incomplete'
        assert 'Forwarded from' in content['text']
        assert client.get('/api/v3/status').json()['problems']['count'] == 0
        assert 'Фрагмент предпросмотра' in content['linked_content']['post_text'] or 'фрагмент предпросмотра' in content['linked_content']['post_text']


def test_removing_a_real_unsupported_iframe_is_still_an_author_edit(tmp_path):
    embedded = [True]
    def response(request):
        return httpx.Response(200, text=page([(11, 'Own text' + ('<iframe src="https://example.test/embed"></iframe>' if embedded[0] else ''))]))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers={'Authorization': 'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        path = '/api/v3/materials/' + settled(client)[0]['id']
        assert client.get(path).json()['content']['completeness']['state'] == 'incomplete'
        embedded[0] = False
        client.post(path + '/retry'); settled(client)
        content = client.get(path).json()['content']
        assert content['completeness']['state'] == 'complete'
        assert content['changed'] is True
        assert len(content['previous_versions']) == 1
