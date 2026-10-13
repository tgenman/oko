from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.demo import DemoSource
from tests.support.processes import wait_cards
from tests.support.reader import HEADERS


def test_navigation_counts_and_feed_selection(tmp_path):
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test'), headers=HEADERS) as client:
        assert client.get('/api/navigation', headers={'Authorization': 'Bearer wrong'}).status_code == 403
        client.patch('/api/settings/oko/global', json={'events': False})
        client.post('/api/tabs/a/heartbeat')
        cards = wait_cards(client, 3)
        navigation = client.get('/api/navigation').json()
        assert navigation['count'] == 3
        assert next(f['count'] for f in navigation['folders'] if f['id'] == 'demo') == 3
        assert next(f['count'] for f in navigation['feeds'] if f['id'] == 'demo') == 3
        assert client.get('/api/cards?feed=demo').json() == cards
        assert client.get('/api/cards?feed=missing').json() == []
        assert client.get('/api/cards?folder=missing&feed=demo').json() == []
        client.post('/api/materials/' + cards[0]['id'] + '/viewed')
        assert client.get('/api/navigation').json()['count'] == 2


def test_navigation_counts_loading_publications_and_empty_feeds(tmp_path):
    from tests.support.sources import LateSource
    from tests.support.reader import wait_for
    with TestClient(create_app(tmp_path, source=LateSource(), token='test'), headers=HEADERS) as client:
        client.patch('/api/settings/oko/global', json={'events': False})
        assert client.get('/api/navigation').json()['count'] == 0
        client.post('/api/tabs/a/heartbeat')
        wait_for(client, lambda: len(client.get('/api/cards').json()) == 3)
        assert all(not card['complete'] for card in client.get('/api/cards').json())
        assert client.get('/api/navigation').json()['count'] == 3


def test_hidden_publications_stay_excluded_inside_digest(tmp_path):
    import httpx
    import json
    from tests.support.filters import FilterSource, transport
    from tests.support.digests import configure, digest_transport
    from tests.support.reader import wait_for
    filters, summaries = transport([]), digest_transport([])
    def response(request):
        if request.url.path == '/v1/chat/completions':
            task = json.loads(json.loads(request.content)['messages'][1]['content'])
            if task['operation'] == 'digest':
                return summaries.handler(request)
        return filters.handler(request)
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        configure(client)
        client.patch('/api/settings/oko/global', json={'prompt_filters': 'Убери рекламу'})
        wait_for(client, lambda: len(c := client.get('/api/cards').json()) == 2 and all(x['filter']['status'] == 'done' for x in c))
        assert client.get('/api/navigation').json()['count'] == 2
        assert len(client.get('/api/cards?show_hidden=true').json()) == 3
        assert client.get('/api/navigation').json()['count'] == 2
        digest = client.post('/api/digests/folder/demo', json={}).json()['id']
        wait_for(client, lambda: client.get('/api/materials/' + digest).json().get('digest'))
        assert [c['id'] for c in client.get('/api/cards?feed=demo').json()] == [digest]
        assert client.get('/api/cards?feed=demo').json()[0]['snippet'] == 'Темы: библиотека и космос.'
        navigation = client.get('/api/navigation').json()
        assert navigation['count'] == 2
        assert navigation['folders'][0]['count'] == 2
        assert navigation['feeds'][0]['count'] == 2


def test_event_selected_by_each_participating_feed_has_all_sources(tmp_path):
    import httpx
    from tests.support.rss import rss
    from tests.support.events import event_transport
    from tests.support.reader import wait_for
    def response(request):
        text = rss('https://' + request.url.host + '/article') if request.url.path == '/feed' else '<article>Открылась библиотека на 24 места.</article>'
        return httpx.Response(200, text=text)
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(response), ai_transport=event_transport([])), headers=HEADERS) as client:
        plan = client.post('/api/import', json={'opml': '<opml><body><outline text="Новости"><outline text="a" xmlUrl="https://a.test/feed"/><outline text="b" xmlUrl="https://b.test/feed"/></outline></body></opml>'}).json()
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp'})
        client.post('/api/tabs/a/heartbeat')
        wait_for(client, lambda: len(c := client.get('/api/cards').json()) == 1 and c[0].get('event'))
        navigation = client.get('/api/navigation').json()
        assert navigation['count'] == 2
        assert navigation['folders'][0]['count'] == 2
        assert [f['count'] for f in navigation['feeds']] == [1, 1]
        expected = {(f['id'], f['title']) for f in plan['feeds']}
        for feed in plan['feeds']:
            cards = client.get('/api/cards', params={'feed': feed['id']}).json()
            assert len(cards) == 1 and cards[0]['event']
            assert cards[0]['snippet'] == 'Открылась библиотека.'
            assert {(s['feed_id'], s['title']) for s in cards[0]['sources']} == expected
        assert client.get('/api/cards?feed=missing').json() == []
        assert client.get('/api/cards?sort=oldest&feed=' + plan['feeds'][0]['id']).json()[0]['event']
