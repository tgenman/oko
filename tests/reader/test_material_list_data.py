from oko.collection.contracts import DiscoveryResult, FetchResult, FetchRequirements, FetchResources
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
    description = ['Описание из Atom &amp; детали.']
    captions = Captions()

    def reply(request):
        if request.url.path == '/feeds/videos.xml':
            xml = feed_xml().replace('<media:thumbnail', '<media:description>' + description[0] + '</media:description><media:thumbnail')
            return httpx.Response(200, text=xml)
        assert request.url.host == 'i.ytimg.com'
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})

    kwargs = dict(token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply), youtube_captions=captions)
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        feed = subscribe(client)
        client.patch('/api/v3/settings/feed/' + feed, json={'theses': False})
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'reader')
        card = wait_for(client, lambda: next((c for c in client.get('/api/v3/cards').json() if c['source'].get('captions') == 'not_requested' and not c.get('loading')), None))
        assert card['snippet'] == 'Описание из Atom & детали.'
        assert card['feed_title'] == 'Канал'
        item = card['id']
        material = client.get('/api/v3/materials/' + item).json()
        assert material['content']['description'] == 'Описание из Atom & детали.'
        assert material['content']['text'] == '' and captions.calls == 0
        client.patch('/api/v3/feeds/' + feed, json={'importance': 'high'})
        wait_for(client, lambda: client.get('/api/v3/materials/' + item).json()['source'].get('captions') == 'available')
        assert captions.calls == 1

    description[0] = 'Новое описание из обычного фида.'
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'reader')
        assert client.get('/api/v3/materials/' + item).json()['content']['description'] == 'Описание из Atom & детали.'
        client.post('/api/v3/collect')
        card = wait_for(client, lambda: next((c for c in client.get('/api/v3/cards').json() if c.get('snippet') == description[0]), None))
        assert card['snippet'] == description[0]
        material = client.get('/api/v3/materials/' + item).json()
        assert material['content']['text'] == 'Сохранённые субтитры.'
        assert material['source']['captions'] == 'available' and captions.calls == 1


def test_cards_have_plain_bounded_text_and_unknown_dates_are_last(tmp_path):
    from oko.collection.contracts import Material, Publication

    class Source:
        fetch_requirements = FetchRequirements()
        changes = None
        async def discover(self, cursor):
            return DiscoveryResult(tuple([] if cursor else [
                Publication('old', 'https://example.org/old', 10),
                Publication('new', 'https://example.org/new', 20),
                Publication('unknown', 'https://example.org/unknown', 0, published_known=False),
            ]), 'done')

        async def fetch(self, publication, resources=FetchResources()):
            return FetchResult(Material(publication.id, '<p>Краткий <b>текст</b> &amp; подробности.</p><script>secret()</script><style>secret-style</style>' + 'x' * 700))

    with TestClient(create_app(tmp_path, source=Source(), token='test'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'reader')
        wait_for(client, lambda: len(c := client.get('/api/v3/cards').json()) == 3 and all((card['availability'] == 'available') for card in c))
        newest = client.get('/api/v3/cards?sort=newest').json()
        oldest = client.get('/api/v3/cards?sort=oldest').json()
        assert [c['title'] for c in newest] == ['new', 'old', 'unknown']
        assert [c['title'] for c in oldest] == ['old', 'new', 'unknown']
        assert newest[-1]['published_at'] is None
        for card in newest:
            assert card['snippet'].startswith('Краткий текст & подробности.')
            assert len(card['snippet']) == 500
            assert '<' not in card['snippet'] and 'secret' not in card['snippet']
            assert card['feed_title']
            assert 'text' not in card and 'display_text' not in card


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
        card = wait_for(client, lambda: next((c for c in client.get('/api/v3/cards').json() if c['source'].get('captions') == 'available' and not c.get('loading')), None))
        assert card['snippet'] == 'Сохранённые субтитры.'
        description[0] = 'Добавленное описание.'
        client.post('/api/v3/collect')
        wait_for(client, lambda: client.get('/api/v3/cards').json()[0]['snippet'] == description[0])
        material = client.get('/api/v3/materials/' + card['id']).json()
        assert material['content']['description'] == description[0] and material['content']['text'] == 'Сохранённые субтитры.'
        assert captions.calls == 1


def test_snippet_does_not_restore_filtered_fragments(tmp_path):
    from tests.support.filters import FilterSource, transport

    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=transport([])), headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/v3/settings/oko/global', json={'theses': False, 'processor': 'llamacpp', 'prompt_filters': 'Убери рекламу'})
        presence(client, 'reader')
        cards = wait_for(client, lambda: (c if len(c := client.get('/api/v3/cards').json()) == 2 and all(card['filter']['attempt']['state'] == 'ready' for card in c) else None))
        card = next(c for c in cards if c['title'] == 'Материал 1')
        assert card['snippet'] == 'Открылась библиотека.'
        assert 'Реклама' in client.get('/api/v3/materials/' + card['id']).json()['content']['text']
