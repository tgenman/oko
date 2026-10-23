from oko.collection.contracts import DiscoveryResult, FetchResult, FetchRequirements, FetchResources
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
        cards = [wire.ReadingCard.model_validate(card).root for card in response.json()]
        assert len(cards) == 3
        card = next(card for card in cards if card.id.root == item)
        material = client.get('/api/v3/materials/' + item)
        assert material.status_code == 200, material.text
        value = wire.ReadableMaterial.model_validate(material.json()).root
        assert value.availability == 'available' and value.content.text
        assert value.folder_id == card.folder_id
        assert len(client.get('/api/v3/cards').json()) == 3
        result = client.post('/api/v3/materials/' + item + '/viewed', headers={'X-Oko-Action':'view-one'})
        assert result.status_code == 200, result.text
        assert client.get('/api/v3/commands/view-one').json()['outcome'] == 'succeeded'
        assert client.get('/api/v3/materials/' + item).status_code == 404
        folder = card.folder_id.root
        preview = client.post(f'/api/v3/folders/{folder}/completion/preview').json()
        assert preview['publications_count'] == 2
        stale = client.post(f'/api/v3/folders/{folder}/completion', json={'token':'stale'}, headers={'X-Oko-Action':'stale-folder'})
        assert stale.status_code == 409, stale.text
        assert stale.json()['code'] == 'composition_changed'
        wire.ErrorEnvelope.model_validate(stale.json())
        assert len(client.get('/api/v3/cards').json()) == 2
        result = client.post(f'/api/v3/folders/{folder}/completion', json={'token':stale.json()['details']['preview']['token']}, headers={'X-Oko-Action':'complete-folder'})
        assert result.status_code == 200, result.text
        assert client.get('/api/v3/cards').json() == []
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
    from oko.collection.contracts import Material, Publication, Image, Attachment
    from tests.support.reader import wait_for

    class Source:
        fetch_requirements = FetchRequirements()
        changes = None
        revision = 1
        async def discover(self, cursor):
            return DiscoveryResult(tuple([Publication('post', 'https://example.org/post', 100)]), str(self.revision))
        async def fetch(self, publication, resources=FetchResources()):
            return FetchResult(Material('Version ' + str(self.revision), 'Text ' + str(self.revision),
                full=self.revision > 1, reason='incomplete' if self.revision == 1 else '',
                images=(Image('image-1.png', b'image'),),
                attachments=tuple(Attachment(f'media-{i}.bin', kind.encode(), kind, 'application/octet-stream') for i, kind in enumerate(['image', 'video', 'audio', 'circle', 'file']))))
    source = Source()
    options = dict(token='test', api_only=True, source=source)
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses':False, 'prompt_filters':''})
        first = wait_for(client, lambda: next((v for v in client.get('/api/v3/cards').json() if v['availability'] == 'available'), None))
        item = first['id']
        assert first['completeness']['state'] == 'incomplete'
        source.revision = 2
        response = client.post(f'/api/v3/materials/{item}/retry', headers={'X-Oko-Action':'fetch-again'})
        assert response.status_code == 200, response.text
        current = wait_for(client, lambda: (v if (v := client.get(f'/api/v3/materials/{item}').json())['content']['text'] == 'Text 2' else None))
        content = current['content']
        assert content['changed'] and content['previous_versions']==[]
        urls = []
        for material in [content]:
            assert [a['kind'] for a in material['attachments']] == ['image','video','audio','circle','file']
            urls += material['images'] + [a['url'] for a in material['attachments']]
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
        assert client.get('/api/v3/cards').json() == []


def test_pending_unknown_dates_and_completion_do_not_admit_late_fetch(tmp_path):
    import asyncio
    from oko.collection.contracts import Material, Publication
    from tests.support.reader import wait_for

    class Source:
        fetch_requirements = FetchRequirements()
        changes = None
        async def discover(self, cursor):
            return DiscoveryResult(tuple([] if cursor else [Publication('pending','https://example.org/pending',0,published_known=False)]), '1')
        async def fetch(self, publication, resources=FetchResources()):
            await asyncio.Event().wait()
            return FetchResult(Material('Never accepted','late'))
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=Source()), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'originals':False,'theses':False,'prompt_filters':''})
        cards = wait_for(client, lambda: client.get('/api/v3/cards').json())
        pending = cards[0]
        assert pending['availability'] == 'pending' and pending['published_at'] is None
        assert pending['originals'] is False
        assert pending['source_position'] is None
        material = client.get('/api/v3/materials/' + pending['id']).json()
        assert material['content'] == {'availability':'pending','loading':True}
        assert 'text' not in material['content']
        composition = client.get('/api/v3/reading-composition').json()
        assert composition['count'] == 1
        folder = pending['folder_id']
        preview = client.post(f'/api/v3/folders/{folder}/completion/preview').json()
        assert preview['publications_count'] == 1
        assert client.post(f'/api/v3/folders/{folder}/completion', json={'token':preview['token']}).status_code == 200
        assert client.get('/api/v3/reading-composition').json()['count'] == 0
        assert client.get('/api/v3/cards').json() == []


def test_hidden_filter_theses_restoration_and_processing_receipts(tmp_path):
    from tests.support.filters import FilterSource, transport
    from tests.support.reader import wait_for
    from tests.support.filters import setup_filters
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=FilterSource(), ai_transport=transport([], theses=True)), headers=HEADERS) as client:
        setup_filters(client)
        cards = wait_for(client, lambda: (v if len(v := client.get('/api/v3/cards?show_hidden=true').json()) == 3 and all(c['filter']['attempt']['state'] == 'ready' for c in v) else None))
        hidden = next(c for c in cards if c['filter']['result']['hidden'])
        assert len(client.get('/api/v3/cards').json()) == 2
        assert client.get('/api/v3/reading-composition').json()['count'] == 2
        assert hidden['filter']['result']['provenance']['kind'] == 'local'
        assert client.post('/api/v3/materials/' + hidden['id'] + '/filter/restore', headers={'X-Oko-Action':'restore-hidden'}).status_code == 404
        assert len(client.get('/api/v3/cards').json()) == 2
        client.patch('/api/v3/settings/oko/global', json={'theses':True})
        item = next(c['id'] for c in cards if not c['filter']['result']['hidden'])
        path = '/api/v3/materials/' + item
        value = wait_for(client, lambda: (v if (v := client.get(path).json())['theses']['result'] else None))
        assert value['theses']['result']['provenance']['kind'] == 'local'
        assert value['content']['text']
        for suffix in ['regenerate','theses/retry','filter/retry']:
            key = suffix.replace('/','-')
            response = client.post(path + '/' + suffix, headers={'X-Oko-Action':key})
            assert response.status_code == 200, response.text
            assert client.get('/api/v3/commands/' + key).json()['outcome'] == 'succeeded'
        folder = hidden['folder_id']
        preview = client.post(f'/api/v3/folders/{folder}/completion/preview').json()
        assert preview['publications_count'] == 3
        assert client.post(f'/api/v3/folders/{folder}/completion', json={'token':preview['token']}).status_code == 200
        assert client.get('/api/v3/cards?show_hidden=true').json() == []
        assert client.get('/api/v3/commands/restore-hidden').json()['outcome']=='unknown'


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
        client.patch('/api/v3/settings/oko/global', json={'theses':True,'processor':'llamacpp'})
        state = wait_for(client, lambda: (s if (s := client.get(path).json()['theses'])['attempt']['state'] == 'waiting' else None))
        assert state['attempt']['reason'] == 'processor_not_configured'
        assert state['attempt']['retry_at'] is not None
        client.put('/api/v3/processors/llamacpp', json={'endpoint':'https://remote.example.org','model':'local:latest','local_execution':True})
        state = wait_for(client, lambda: (s if (s := client.get(path).json()['theses'])['attempt'].get('reason') == 'public_ai_not_allowed' else None))
        assert state['result'] is None
        client.put('/api/v3/processors/llamacpp', json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        accepted = wait_for(client, lambda: client.get(path).json()['theses']['result'])
        wait_for(client, lambda: all(c['theses']['result'] for c in client.get('/api/v3/cards').json()))
        blocked[0] = True
        try:
            assert client.post(path + '/regenerate').status_code == 200
            current = client.get(path).json()['theses']
            assert current['attempt']['state'] in ('queued','running')
            assert current['result'] == accepted
            fail[0] = True
        finally:
            blocked[0] = False
        state = wait_for(client, lambda: (s if (s := client.get(path).json()['theses'])['attempt']['state'] == 'waiting' else None))
        assert state['result'] == accepted
        assert state['attempt']['reason'] == 'retry_scheduled'
        blocked[0] = True
        fail[0] = False
        clock.now += 31
        try:
            running = wait_for(client, lambda: (s if (s := client.get(path).json()['theses'])['attempt']['state'] == 'running' else None))
            assert running['result'] == accepted
        finally:
            blocked[0] = False
        blocked[0] = True
        try:
            client.patch('/api/v3/settings/oko/global', json={'prompt_theses':'Different input'})
            assert client.get(path).json()['theses']['result'] is None
        finally:
            blocked[0] = False


def test_public_codex_denial_has_domain_waiting_reason(tmp_path):
    from tests.support.reader import wait_for
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        item = setup_reader(client, theses=False)
        client.patch('/api/v3/settings/oko/global', json={'theses':True,'processor':'codex'})
        state = wait_for(client, lambda: (s if (s := client.get('/api/v3/materials/' + item).json()['theses'])['attempt']['state'] == 'waiting' else None))
        assert state['attempt']['reason'] == 'public_ai_not_allowed'
