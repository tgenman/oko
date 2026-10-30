from tests.support.cards import read_all_cards, read_publications_for_cards
from oko.collection.contracts import DiscoveryResult, FetchResult
from tests.support.v3 import TestClient
from oko.collection.demo import DemoSource
from oko.reader.generated import models as wire
from tests.support.app import create_app
from tests.support.reader import HEADERS, setup_reader


def test_reading_and_explicit_completion_receipts(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        item = setup_reader(client, theses=False)
        response = client.get('/api/v3/cards')
        assert response.status_code == 200, response.text
        cards = [wire.CardListItem.model_validate(card) for card in response.json()['items']]
        assert len(cards) == 3
        card = next(card for card in cards if card.id.root == item)
        material = client.get('/api/v3/publication/' + item)
        assert material.status_code == 200, material.text
        value = wire.PublicationResponse.model_validate(material.json())
        assert value.availability == 'available' and value.text
        assert value.feed_id == card.feed_id
        assert len(read_all_cards(client, '/api/v3/cards')) == 3
        result = client.post('/api/v3/materials/' + item + '/viewed', headers={'X-Oko-Action':'view-one'})
        assert result.status_code == 200, result.text
        assert client.get('/api/v3/commands/view-one').json()['outcome'] == 'succeeded'
        assert client.get('/api/v3/publication/' + item).status_code == 404
        folder = next(f['id'] for f in client.get('/api/v3/reading-composition').json()['folders'])
        preview = client.post(f'/api/v3/folders/{folder}/completion/preview').json()
        assert preview['publications_count'] == 2
        stale = client.post(f'/api/v3/folders/{folder}/completion', json={'token':'stale'}, headers={'X-Oko-Action':'stale-folder'})
        assert stale.status_code == 409, stale.text
        assert stale.json()['code'] == 'composition_changed'
        wire.ErrorEnvelope.model_validate(stale.json())
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
        result = client.post(f'/api/v3/folders/{folder}/completion', json={'token':stale.json()['details']['preview']['token']}, headers={'X-Oko-Action':'complete-folder'})
        assert result.status_code == 200, result.text
        assert read_all_cards(client, '/api/v3/cards') == []
        assert client.get('/api/v3/commands/complete-folder').json()['outcome'] == 'succeeded'


def test_reading_errors_are_typed_and_queries_strict(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        for path in ['/api/v3/cards?show_hidden=1', '/api/v3/cards?sort=bad', '/api/v3/cards?feed=', '/api/v3/cards?sort=newest&sort=oldest', '/api/v3/cards?extra=true']:
            response = client.get(path)
            assert response.status_code == 422, (path, response.text)
            assert response.json()['code'] == 'request_invalid'
        for suffix in ['viewed', 'retry', 'regenerate', 'theses/retry', 'filter/retry']:
            key = suffix.replace('/', '-')
            response = client.post('/api/v3/materials/missing/' + suffix, headers={'X-Oko-Action':key})
            assert response.status_code == 404, response.text
            assert response.json()['code'] == 'resource_not_found'
            receipt = client.get('/api/v3/commands/' + key).json()
            assert receipt['outcome'] == 'failed' and receipt['error']['code'] == 'resource_not_found'
        assert client.post('/api/v3/folders/missing/completion/preview').status_code == 404
        for path in ['/api/v3/materials/missing', '/api/v3/materials/missing/versions/1', '/api/v3/materials/missing/media/media-1.mp3']:
            response = client.get(path)
            assert response.status_code == 404, response.text
        assert client.get('/api/v3/cards', headers={'Authorization':'Bearer invalid'}).status_code == 403


def test_versions_all_media_and_receipt_survive_until_explicit_viewed(tmp_path):
    from oko.collection.contracts import Publication
    from tests.support.source import SyntheticMaterial,Image,Attachment
    from tests.support.reader import wait_for

    class Source:
        changes = None
        revision = 1
        async def discover(self, cursor):
            return DiscoveryResult(tuple([] if cursor else [Publication('post', 'https://example.org/post', 100)]), str(self.revision))
        async def fetch(self, publication):
            return FetchResult(SyntheticMaterial('Version ' + str(self.revision), 'Text ' + str(self.revision),
                full=self.revision > 1, reason='incomplete' if self.revision == 1 else '',
                images=(Image('image-1.png', b'image'),),
                attachments=tuple(Attachment(f'media-{i}.bin', kind.encode(), kind, 'application/octet-stream') for i, kind in enumerate(['image', 'video', 'audio', 'circle', 'file']))))
    source = Source()
    options = dict(token='test', api_only=True, source=source)
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses':False, 'prompt_filters':''})
        first = wait_for(client, lambda: next((v for v in read_all_cards(client, '/api/v3/cards') if not v['loading']), None))
        item = first['id']
        assert client.get('/api/v3/publication/'+item).json()['completeness']['state'] == 'incomplete'
        source.revision = 2
        response = client.post(f'/api/v3/materials/{item}/retry', headers={'X-Oko-Action':'fetch-again'})
        assert response.status_code == 200, response.text
        current = wait_for(client, lambda: (v if (v := client.get(f'/api/v3/publication/{item}').json())['text'] == 'Text 2' and not v['loading'] else None))
        content = current
        assert content['changed'] and 'previous_versions' not in content
        urls = []
        for material in [content]:
            assert [a['kind'] for a in material['media'] if a['role']=='attachment'] == ['image','image','video','audio','circle','file']
            urls += [a['url'] for a in material['media']]
        for url in urls:
            response = client.get(url)
            assert response.status_code == 200 and response.content
            assert response.headers['cache-control'] == 'no-store'
        assert client.get(f'/api/v3/materials/{item}/media/image-1.png').status_code == 404
        assert client.post(f'/api/v3/materials/{item}/viewed', headers={'X-Oko-Action':'view-with-media'}).status_code == 200
        assert all(client.get(url).status_code == 404 for url in urls)
        assert client.get('/api/v3/commands/fetch-again').json()['outcome'] == 'succeeded'
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/commands/view-with-media').json()['outcome'] == 'succeeded'
        assert read_all_cards(client, '/api/v3/cards') == []


def test_pending_unknown_dates_and_completion_do_not_admit_late_fetch(tmp_path):
    import asyncio
    from oko.collection.contracts import Material, Publication
    from tests.support.reader import wait_for

    class Source:
        changes = None
        async def discover(self, cursor):
            return DiscoveryResult(tuple([] if cursor else [Publication('pending','https://example.org/pending',0,published_known=False)]), '1')
        async def fetch(self, publication):
            await asyncio.Event().wait()
            return FetchResult(Material('Never accepted','late'))
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=Source()), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'originals':False,'theses':False,'prompt_filters':''})
        cards = wait_for(client, lambda: read_all_cards(client, '/api/v3/cards'))
        pending = cards[0]
        assert pending['loading'] and pending['published_at'] is None
        material = client.get('/api/v3/publication/' + pending['id']).json()
        assert material['originals'] is False
        assert material['source_position'] is None
        assert material['availability'] == 'pending' and material['loading']
        assert material['text'] == ''
        composition = client.get('/api/v3/reading-composition').json()
        assert composition['count'] == 1
        folder = composition['folders'][0]['id']
        preview = client.post(f'/api/v3/folders/{folder}/completion/preview').json()
        assert preview['publications_count'] == 1
        assert client.post(f'/api/v3/folders/{folder}/completion', json={'token':preview['token']}).status_code == 200
        assert client.get('/api/v3/reading-composition').json()['count'] == 0
        assert read_all_cards(client, '/api/v3/cards') == []


def test_hidden_filter_theses_restoration_and_processing_receipts(tmp_path):
    from tests.support.filters import FilterSource, transport
    from tests.support.reader import wait_for
    from tests.support.filters import setup_filters
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=FilterSource(), ai_transport=transport([], theses=True)), headers=HEADERS) as client:
        setup_filters(client)
        cards = wait_for(client, lambda: (v if len(v := read_publications_for_cards(client, '/api/v3/cards?show_hidden=true')) == 3 and all(c['filter']['state'] == 'ready' for c in v) else None))
        hidden = next(c for c in cards if c['filter']['result']['hidden'])
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
        assert client.get('/api/v3/reading-composition').json()['count'] == 2
        assert hidden['filter']['result']['provenance']['kind'] == 'local'
        assert client.post('/api/v3/materials/' + hidden['id'] + '/filter/restore', headers={'X-Oko-Action':'restore-hidden'}).status_code == 404
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
        client.patch('/api/v3/settings/oko/global', json={'theses':True})
        item = next(c['id'] for c in cards if not c['filter']['result']['hidden'])
        path = '/api/v3/materials/' + item
        publication_path = '/api/v3/publication/' + item
        value = wait_for(client, lambda: (v if (v := client.get(publication_path).json())['theses']['result'] else None))
        assert value['theses']['result']['provenance']['kind'] == 'local'
        assert value['text']
        for suffix in ['regenerate','theses/retry','filter/retry']:
            key = suffix.replace('/','-')
            response = client.post(path + '/' + suffix, headers={'X-Oko-Action':key})
            assert response.status_code == 200, response.text
            assert client.get('/api/v3/commands/' + key).json()['outcome'] == 'succeeded'
        folder = client.get('/api/v3/reading-composition').json()['folders'][0]['id']
        preview = client.post(f'/api/v3/folders/{folder}/completion/preview').json()
        assert preview['publications_count'] == 3
        assert client.post(f'/api/v3/folders/{folder}/completion', json={'token':preview['token']}).status_code == 200
        assert read_all_cards(client, '/api/v3/cards?show_hidden=true') == []
        assert client.get('/api/v3/commands/restore-hidden').json()['outcome']=='unknown'


def test_hidden_result_stays_hidden_during_filter_regeneration(tmp_path):
    from tests.support.filters import FilterSource, transport, setup_filters
    from tests.support.reader import wait_for
    app = create_app(tmp_path, token='test', api_only=True, source=FilterSource(), ai_transport=transport([], theses=True))
    with TestClient(app, headers=HEADERS) as client:
        setup_filters(client)
        cards = wait_for(client, lambda: (v if len(v := read_publications_for_cards(client, '/api/v3/cards?show_hidden=true')) == 3 and all(c['filter']['state'] == 'ready' for c in v) else None))
        item = next(c['id'] for c in cards if c['filter']['result']['hidden'])
        async def schedule():
            async with app.state.host.execution._actions:
                store = app.state.host.require_store()
                with store.db:
                    assert app.state.host.jobs.retry_filter(item)
                    return store.db.execute('SELECT status FROM publications WHERE id=%s', (item,)).fetchone()[0]
        assert client.portal.call(schedule) == 'filter_out'


def test_waiting_reasons_and_previous_theses_during_failed_regeneration(tmp_path):
    import asyncio
    import httpx
    from tests.support.models import model_transport
    from tests.support.reader import wait_for
    from tests.support.reader import Clock
    clock = Clock()
    native = model_transport([])
    blocked = [False]
    fail = [False]
    async def respond(request):
        if request.url.path == '/v1/chat/completions':
            while blocked[0]:
                await asyncio.sleep(.01)
            if fail[0]:
                return httpx.Response(503, json={'error':'synthetic unavailable'})
        return native.handler(request)
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource(), ai_transport=httpx.MockTransport(respond), clock=clock), headers=HEADERS) as client:
        item = setup_reader(client, theses=False)
        path = '/api/v3/materials/' + item
        publication_path = '/api/v3/publication/' + item
        client.patch('/api/v3/settings/oko/global', json={'theses':True,'processor':'llamacpp'})
        clock.now+=1
        state = wait_for(client, lambda: next((w for w in client.get('/api/v3/status/details').json()['work'] if w.get('reason') == 'processor_not_configured'), None))
        assert state['retry_at'] is not None
        wait_for(client, lambda: client.get(publication_path).json()['theses']['state'] == 'error')
        client.put('/api/v3/processors/llamacpp', json={'endpoint':'https://remote.example.org','model':'local:latest','local_execution':True})
        clock.now+=1
        wait_for(client, lambda: any(w.get('reason') == 'public_ai_not_allowed' for w in client.get('/api/v3/status/details').json()['work']))
        wait_for(client, lambda: 'Запрещена публичная' in (client.get(publication_path).json()['theses']['error'] or ''))
        assert client.get(publication_path).json()['theses']['result'] is None
        client.put('/api/v3/processors/llamacpp', json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        clock.now+=1
        accepted = wait_for(client, lambda: client.get(publication_path).json()['theses']['result'])
        wait_for(client, lambda: all(c['theses']['result'] for c in read_publications_for_cards(client, '/api/v3/cards')))
        blocked[0] = True
        try:
            assert client.post(path + '/regenerate').status_code == 200
            current = client.get(publication_path).json()['theses']
            assert current['state'] in ('pending','processing')
            assert current['result'] == accepted
            fail[0] = True
        finally:
            blocked[0] = False
        wait_for(client, lambda: any(w.get('reason') == 'retry_scheduled' for w in client.get('/api/v3/status/details').json()['work']))
        wait_for(client, lambda: client.get(publication_path).json()['theses']['state'] == 'error')
        assert client.get(publication_path).json()['theses']['result'] == accepted
        blocked[0] = True
        fail[0] = False
        clock.now += 61
        try:
            running = wait_for(client, lambda: (s if (s := client.get(publication_path).json()['theses'])['state'] == 'processing' else None))
            assert running['result'] == accepted
        finally:
            blocked[0] = False
        blocked[0] = True
        try:
            client.patch('/api/v3/settings/oko/global', json={'prompt_theses':'Different input'})
            assert client.get(publication_path).json()['theses']['result'] is None
        finally:
            blocked[0] = False


def test_public_codex_denial_has_domain_waiting_reason(tmp_path):
    from tests.support.reader import wait_for
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        item = setup_reader(client, theses=False)
        client.patch('/api/v3/settings/oko/global', json={'theses':True,'processor':'codex'})
        wait_for(client, lambda: any(w.get('reason') == 'public_ai_not_allowed' for w in client.get('/api/v3/status/details').json()['work']))
        wait_for(client, lambda: 'Запрещена публичная' in (client.get('/api/v3/publication/' + item).json()['theses']['error'] or ''))
        assert client.get('/api/v3/publication/' + item).json()['theses']['result'] is None
