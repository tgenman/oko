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
        client.patch('/api/settings/feed/' + feed, json={'theses': False})
        client.patch('/api/settings/oko/global', json={'events': False})
        client.post('/api/tabs/reader/heartbeat')
        card = wait_for(client, lambda: next((c for c in client.get('/api/cards').json() if c.get('captions') == 'not_requested' and not c.get('loading')), None))
        assert card['snippet'] == 'Описание из Atom & детали.'
        assert card['feed_title'] == 'Канал'
        item = card['id']
        material = client.get('/api/materials/' + item).json()
        assert material['description'] == 'Описание из Atom & детали.'
        assert material['text'] == '' and captions.calls == 0
        client.patch('/api/feeds/' + feed, json={'importance': 'high'})
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('captions') == 'available')
        assert captions.calls == 1

    description[0] = 'Новое описание из обычного фида.'
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        assert client.get('/api/materials/' + item).json()['description'] == 'Описание из Atom & детали.'
        client.post('/api/collect')
        card = wait_for(client, lambda: next((c for c in client.get('/api/cards').json() if c.get('snippet') == description[0]), None))
        assert card['snippet'] == description[0]
        material = client.get('/api/materials/' + item).json()
        assert material['text'] == 'Сохранённые субтитры.'
        assert material['captions'] == 'available' and captions.calls == 1


def test_cards_have_plain_bounded_text_and_unknown_dates_are_last(tmp_path):
    from oko.collection.contracts import Material, Publication

    class Source:
        async def discover(self, cursor):
            return ([] if cursor else [
                Publication('old', 'https://example.org/old', 10),
                Publication('new', 'https://example.org/new', 20),
                Publication('unknown', 'https://example.org/unknown', 0, published_known=False),
            ]), 'done'

        async def fetch(self, publication):
            return Material(publication.id, '<p>Краткий <b>текст</b> &amp; подробности.</p><script>secret()</script><style>secret-style</style>' + 'x' * 700)

    with TestClient(create_app(tmp_path, source=Source(), token='test'), headers=HEADERS) as client:
        client.patch('/api/settings/oko/global', json={'events': False})
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client, lambda: len(c := client.get('/api/cards').json()) == 3 and all(card['complete'] for card in c))
        newest = client.get('/api/cards?sort=newest').json()
        oldest = client.get('/api/cards?sort=oldest').json()
        assert [c['title'] for c in newest] == ['new', 'old', 'unknown']
        assert [c['title'] for c in oldest] == ['old', 'new', 'unknown']
        assert newest[-1]['published_known'] is False
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
        client.patch('/api/settings/oko/global', json={'events': False})
        client.patch('/api/feeds/' + feed, json={'importance': 'high'})
        client.post('/api/tabs/reader/heartbeat')
        card = wait_for(client, lambda: next((c for c in client.get('/api/cards').json() if c.get('captions') == 'available' and not c.get('loading')), None))
        assert card['snippet'] == 'Сохранённые субтитры.'
        description[0] = 'Добавленное описание.'
        client.post('/api/collect')
        wait_for(client, lambda: client.get('/api/cards').json()[0]['snippet'] == description[0])
        material = client.get('/api/materials/' + card['id']).json()
        assert material['description'] == description[0] and material['text'] == 'Сохранённые субтитры.'
        assert captions.calls == 1


def test_snippet_does_not_restore_filtered_fragments(tmp_path):
    from tests.support.filters import FilterSource, transport

    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=transport([])), headers=HEADERS) as client:
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'events': False, 'theses': False, 'digests': False, 'processor': 'llamacpp', 'prompt_filters': 'Убери рекламу'})
        client.post('/api/tabs/reader/heartbeat')
        cards = wait_for(client, lambda: (c if len(c := client.get('/api/cards').json()) == 2 and all(card.get('filter', {}).get('status') == 'done' for card in c) else None))
        card = next(c for c in cards if c['title'] == 'Материал 1')
        assert card['snippet'] == 'Открылась библиотека.'
        assert 'Реклама' in client.get('/api/materials/' + card['id']).json()['text']


def test_event_without_dated_originals_keeps_unknown_date(tmp_path):
    from oko.collection.contracts import Material, Publication
    from tests.support.events import event_transport

    class Source:
        async def discover(self, cursor):
            return ([] if cursor else [Publication(str(i), f'https://example.org/{i}', 0, published_known=False) for i in range(2)]), 'done'

        async def fetch(self, publication):
            return Material('О библиотеке', 'Открылась библиотека.')

    with TestClient(create_app(tmp_path, source=Source(), token='test', ai_transport=event_transport([])), headers=HEADERS) as client:
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp'})
        client.post('/api/tabs/reader/heartbeat')
        card = wait_for(client, lambda: next((c for c in client.get('/api/cards').json() if c.get('event')), None))
        assert card['published_known'] is False and card['published'] == 0
        assert card['snippet'] == 'Открылась библиотека.'
        assert len(card['sources']) == 2
