from tests.support.cards import read_all_cards, read_publications_for_cards
from tests.support.v3 import TestClient
from tests.support.synthetic_source import SyntheticSource
from oko.reader.generated import models as wire
from tests.support.app import create_app
from tests.support.reader import HEADERS, setup_reader


def test_reading_and_explicit_completion_receipts(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=SyntheticSource()), headers=HEADERS) as client:
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
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=SyntheticSource()), headers=HEADERS) as client:
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


def test_public_codex_denial_has_domain_waiting_reason(tmp_path):
    from tests.support.reader import wait_for
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=SyntheticSource()), headers=HEADERS) as client:
        item = setup_reader(client, theses=False)
        client.patch('/api/v3/settings/oko/global', json={'theses':True,'processor':'codex'})
        wait_for(client, lambda: any(w.get('reason') == 'public_ai_not_allowed' for w in client.get('/api/v3/status/details').json()['work']))
        wait_for(client, lambda: 'Запрещена публичная' in (client.get('/api/v3/publication/' + item).json()['theses']['error'] or ''))
        assert client.get('/api/v3/publication/' + item).json()['theses']['result'] is None
