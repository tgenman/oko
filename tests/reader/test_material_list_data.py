from tests.support.cards import read_all_cards
from oko.collection.contracts import DiscoveryResult, FetchResult
from tests.support.presence import presence, close_presence
import httpx
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import HEADERS, wait_for
from tests.support.youtube import PNG, subscribe, feed_xml


class Captions:
    def __init__(self):
        self.calls = 0

    async def fetch(self, video):
        self.calls += 1
        return 'Сохранённые субтитры.'


def test_youtube_description_survives_restart_without_requesting_captions(tmp_path):
    import asyncio
    description = ['Описание из Atom &amp; детали.']
    allow_source = [True]
    captions = Captions()

    async def reply(request):
        while not allow_source[0]:
            await asyncio.sleep(.01)
        if request.url.path == '/feeds/videos.xml':
            xml = feed_xml().replace('<media:thumbnail', '<media:description>' + description[0] + '</media:description><media:thumbnail')
            return httpx.Response(200, text=xml)
        assert request.url.host == 'i.ytimg.com'
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})

    kwargs = dict(token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply), youtube_captions=captions)
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'reader')
        assert client.patch('/api/v3/settings/oko/global', json={'theses': False}).status_code == 200
        feed = subscribe(client)
        client.patch('/api/v3/settings/feed/' + feed, json={'theses': False})
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'reader')
        card = wait_for(client, lambda: next((c for c in read_all_cards(client, '/api/v3/cards') if client.get('/api/v3/materials/'+c['id']).json()['source'].get('captions') == 'not_requested' and not c.get('loading')), None))
        assert 'snippet' not in card
        assert next(f['feed']['title'] for f in client.get('/api/v3/reading-composition').json()['feeds'] if f['feed']['id']==card['feed_id']) == 'Канал'
        item = card['id']
        material = client.get('/api/v3/materials/' + item).json()
        assert material['content']['description'] == 'Описание из Atom & детали.'
        assert material['content']['text'] == '' and captions.calls == 0
        client.patch('/api/v3/feeds/' + feed, json={'importance': 'high'})
        wait_for(client, lambda: client.get('/api/v3/materials/' + item).json()['source'].get('captions') == 'available')
        assert captions.calls == 1

    description[0] = 'Новое описание из обычного фида.'
    allow_source[0] = False
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'reader')
        assert client.get('/api/v3/materials/' + item).json()['content']['description'] == 'Описание из Atom & детали.'
        allow_source[0] = True
        client.post('/api/v3/collect')
        wait_for(client, lambda: client.get('/api/v3/materials/' + item).json()['content']['description'] == description[0])
        material = client.get('/api/v3/materials/' + item).json()
        assert material['content']['text'] == 'Сохранённые субтитры.'
        assert material['source']['captions'] == 'available' and captions.calls == 1


def test_cards_have_plain_bounded_text_and_unknown_dates_are_last(tmp_path):
    from oko.collection.contracts import Material, Publication

    class Source:
        changes = None
        async def discover(self, cursor):
            return DiscoveryResult(tuple([] if cursor else [
                Publication('old', 'https://example.org/old', 10),
                Publication('new', 'https://example.org/new', 20),
                Publication('unknown', 'https://example.org/unknown', 0, published_known=False),
            ]), 'done')

        async def fetch(self, publication):
            return FetchResult(Material(publication.id, '<p>Краткий <b>текст</b> &amp; подробности.</p><script>secret()</script><style>secret-style</style>' + 'x' * 700))

    with TestClient(create_app(tmp_path, source=Source(), token='test'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'reader')
        wait_for(client, lambda: len(c := read_all_cards(client, '/api/v3/cards')) == 3 and all(not card['loading'] for card in c))
        newest = read_all_cards(client, '/api/v3/cards?sort=newest')
        oldest = read_all_cards(client, '/api/v3/cards?sort=oldest')
        assert [c['title'] for c in newest] == ['new', 'old', 'unknown']
        assert [c['title'] for c in oldest] == ['old', 'new', 'unknown']
        assert newest[-1]['published_at'] is None
        for card in newest:
            assert 'snippet' not in card and 'text' not in card and 'display_text' not in card
            assert card['feed_id']



def test_old_video_uses_captions_until_atom_supplies_description(tmp_path):
    description, captions = [''], Captions()

    def reply(request):
        if request.url.path == '/feeds/videos.xml':
            xml = feed_xml().replace('<media:thumbnail', '<media:description>' + description[0] + '</media:description><media:thumbnail')
            return httpx.Response(200, text=xml)
        assert request.url.host == 'i.ytimg.com'
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})

    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply), youtube_captions=captions), headers=HEADERS) as client:
        feed = subscribe(client)
        client.patch('/api/v3/settings/oko/global', json={})
        client.patch('/api/v3/feeds/' + feed, json={'importance': 'high'})
        presence(client, 'reader')
        card = wait_for(client, lambda: next((c for c in read_all_cards(client, '/api/v3/cards') if client.get('/api/v3/materials/'+c['id']).json()['source'].get('captions') == 'available' and not c.get('loading')), None))
        assert 'snippet' not in card
        assert client.get('/api/v3/materials/' + card['id']).json()['content']['text'] == 'Сохранённые субтитры.'
        description[0] = 'Добавленное описание.'
        client.post('/api/v3/collect')
        wait_for(client, lambda: client.get('/api/v3/materials/' + card['id']).json()['content']['description'] == description[0])
        material = client.get('/api/v3/materials/' + card['id']).json()
        assert material['content']['description'] == description[0] and material['content']['text'] == 'Сохранённые субтитры.'
        assert captions.calls == 1


def test_snippet_does_not_restore_filtered_fragments(tmp_path):
    from tests.support.filters import FilterSource, transport

    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=transport([])), headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/v3/settings/oko/global', json={'theses': False, 'processor': 'llamacpp', 'prompt_filters': 'Убери рекламу'})
        presence(client, 'reader')
        cards = wait_for(client, lambda: (c if len(c := read_all_cards(client, '/api/v3/cards')) == 2 and all(client.get('/api/v3/materials/' + card['id']).json()['filter']['attempt']['state'] == 'ready' for card in c) else None))
        card = next(c for c in cards if c['title'] == 'Материал 1')
        assert 'snippet' not in card
        material=client.get('/api/v3/materials/' + card['id']).json()
        assert material['content']['display_text'].strip() == 'Открылась библиотека.'
        assert 'Реклама' in material['content']['text']


def test_preview_does_not_restore_fragment_formed_at_a_removed_boundary(tmp_path):
    import json
    from oko.collection.contracts import Material, Publication
    from tests.support.filters import FilterSource
    from tests.support.models import model_transport

    class Source(FilterSource):
        async def discover(self, cursor):
            return DiscoveryResult(tuple([] if cursor else [Publication('edge', 'https://example.org/edge', 20)]), 'done')

        async def fetch(self, publication):
            return FetchResult(Material('Boundary', 'aabb'))

    native = model_transport([])

    def reply(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': json.dumps(dict(hidden=False, reason='Removed fragment', removed=['ab']))}}]})

    with TestClient(create_app(tmp_path, source=Source(), token='test', ai_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/v3/settings/oko/global', json={'theses': False, 'processor': 'llamacpp', 'prompt_filters': 'Remove ab'})
        presence(client, 'reader')
        cards = wait_for(client, lambda: (c if len(c := read_all_cards(client, '/api/v3/cards')) == 1 and client.get('/api/v3/materials/' + c[0]['id']).json()['filter']['attempt']['state'] == 'ready' else None))
        assert 'snippet' not in cards[0]
        material = client.get('/api/v3/materials/' + cards[0]['id']).json()
        assert material['content']['text'] == 'aabb'
        assert material['content']['display_text'] == 'ab'
