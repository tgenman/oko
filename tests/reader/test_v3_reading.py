import pytest
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
        assert material.json()['content'].get('blocks')
        assert value.feed_id == card.feed_id
        assert len(read_all_cards(client, '/api/v3/cards')) == 3
        result = client.post('/api/v3/materials/' + item + '/viewed', headers={'X-Oko-Action':'view-one'})
        assert result.status_code == 200, result.text
        assert client.get('/api/v3/commands/view-one').json()['outcome'] == 'succeeded'
        assert client.get('/api/v3/publication/' + item).status_code == 200
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
        cards = wait_for(client, lambda: (v if len(v := read_publications_for_cards(client, '/api/v3/cards?show_hidden=true')) == 3 and all('hidden' in c['filtered_content'] for c in v) else None))
        hidden = next(c for c in cards if c['filtered_content']['hidden'])
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
        assert client.get('/api/v3/reading-composition').json()['count'] == 2
        assert hidden['processing_metadata']['filter']['processor'] == 'llamacpp'
        assert client.post('/api/v3/materials/' + hidden['id'] + '/filter/restore', headers={'X-Oko-Action':'restore-hidden'}).status_code == 404
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
        client.patch('/api/v3/settings/oko/global', json={'theses':True})
        item = next(c['id'] for c in cards if not c['filtered_content']['hidden'])
        path = '/api/v3/materials/' + item
        publication_path = '/api/v3/publication/' + item
        value = wait_for(client, lambda: (v if (v := client.get(publication_path).json())['summarized_content'].get('points') else None))
        assert value['processing_metadata']['summary']['processor'] == 'llamacpp'
        assert value['content']['blocks']
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
        wait_for(client, lambda: any('Запрещена публичная' in (job['reason_message'] or '') for job in client.get('/api/v3/publication/' + item).json()['jobs'] if job['operation']=='theses'))
        assert 'points' not in client.get('/api/v3/publication/' + item).json()['summarized_content']


def test_cards_sort_unknown_publication_dates_by_discovery_time(tmp_path):
    from tests.support.storage import database
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=SyntheticSource()), headers=HEADERS) as client:
        setup_reader(client, theses=False)
        ids = sorted(card['id'] for card in read_all_cards(client, '/api/v3/cards'))
        with database(tmp_path) as db:
            db.execute('UPDATE publications SET published_at=%s, discovered_at=%s WHERE id=%s', (1000, 100, ids[0]))
            db.execute('UPDATE publications SET published_at=NULL, discovered_at=%s WHERE id=%s', (2000, ids[1]))
            db.execute('UPDATE publications SET published_at=NULL, discovered_at=%s WHERE id=%s', (500, ids[2]))
        newest = read_all_cards(client, '/api/v3/cards?sort=newest')
        assert [card['id'] for card in newest] == [ids[1], ids[0], ids[2]]
        assert newest[0]['published_at'] is None and newest[2]['published_at'] is None
        oldest = read_all_cards(client, '/api/v3/cards?sort=oldest')
        assert [card['id'] for card in oldest] == [ids[2], ids[0], ids[1]]
        with database(tmp_path) as db:
            db.execute('UPDATE publications SET discovered_at=%s WHERE id=%s', (2000, ids[2]))
        paged = read_all_cards(client, '/api/v3/cards?sort=newest', params={'limit': 1})
        assert [card['id'] for card in paged] == [ids[1], ids[2], ids[0]]


@pytest.mark.parametrize('prior_owner', ['other', None])
def test_discovery_reports_conflicting_publication_owner(tmp_path, prior_owner):
    from tests.support.storage import database
    from tests.support.reader import wait_for

    class ReplaySource(SyntheticSource):
        async def discover(self, cursor):
            return await super().discover(None)

    with TestClient(create_app(tmp_path, token='test', api_only=True, source=ReplaySource()), headers=HEADERS) as client:
        publication = setup_reader(client, theses=False)
        with database(tmp_path) as db:
            original_feed = db.execute('SELECT feed_id FROM publications WHERE id=%s', (publication,)).fetchone()[0]
            db.execute("INSERT INTO feeds(id,platform,external_id) VALUES ('other','rss','https://other.test/feed')")
            db.execute('UPDATE publications SET feed_id=%s WHERE id=%s', (prior_owner, publication))
            db.execute("UPDATE feeds SET cursor='' WHERE id=%s", (original_feed,))
        response = client.post('/api/v3/collect', json={'scope':'feed','feed_id':original_feed})
        assert response.status_code == 200, response.text

        def failed():
            with database(tmp_path) as db:
                return db.execute("SELECT status,reason_message,reason_code FROM jobs WHERE kind='discover' AND item=%s AND status='failed'", (original_feed,)).fetchone()
        status = wait_for(client, failed)
        assert status['reason_code'] == 'publication-ownership-conflict'
        assert all(text in status['reason_message'] for text in (publication, str(prior_owner), original_feed, 'https://example.org/oko/'))
        details = client.get('/api/v3/status/details')
        assert details.status_code == 200
        assert publication in details.text and str(prior_owner) in details.text
        with database(tmp_path) as db:
            assert db.execute('SELECT feed_id FROM publications WHERE id=%s', (publication,)).fetchone()[0] == prior_owner
            assert db.execute("SELECT count(*) FROM publications WHERE platform='other_web' AND external_id LIKE 'https://example.org/oko/%%'").fetchone()[0] == 3


def test_rediscovery_by_same_feed_keeps_publication_ids(tmp_path):
    from tests.support.reader import wait_for
    from tests.support.storage import database

    class ReplaySource(SyntheticSource):
        async def discover(self, cursor):
            return await super().discover(None)

    with TestClient(create_app(tmp_path, token='test', api_only=True, source=ReplaySource()), headers=HEADERS) as client:
        item = setup_reader(client, theses=False)
        before = {card['id'] for card in read_all_cards(client, '/api/v3/cards')}
        with database(tmp_path) as db:
            feed = db.execute('SELECT feed_id FROM publications WHERE id=%s', (item,)).fetchone()[0]
            db.execute("UPDATE feeds SET cursor='' WHERE id=%s", (feed,))
        response = client.post('/api/v3/collect', json={'scope':'feed','feed_id':feed})
        assert response.status_code == 200, response.text
        wait_for(client, lambda: client.get('/api/v3/status').json()['collection']['state'] == 'idle')
        assert {card['id'] for card in read_all_cards(client, '/api/v3/cards')} == before
        with database(tmp_path) as db:
            assert db.execute('SELECT count(*) FROM publications WHERE feed_id=%s', (feed,)).fetchone()[0] == 3


def test_distinct_external_keys_are_not_merged_by_member_number(tmp_path):
    from dataclasses import replace

    class OverlappingMembers(SyntheticSource):
        async def discover(self, cursor):
            result = await super().discover(cursor)
            return replace(result, publications=tuple(replace(publication, content={'blocks': [], 'extra': {'telegram': {'members': [17]}}}) for publication in result.publications))

    with TestClient(create_app(tmp_path, token='test', api_only=True, source=OverlappingMembers()), headers=HEADERS) as client:
        setup_reader(client, theses=False)
        assert len({card['id'] for card in read_all_cards(client, '/api/v3/cards')}) == 3


def test_reader_ignores_legacy_completeness_flags_when_job_and_status_are_ready(tmp_path):
    from tests.support.storage import database
    from tests.support.reader import setup_reader, HEADERS
    from tests.support.synthetic_source import SyntheticSource
    from fastapi.testclient import TestClient
    import json
    with TestClient(create_app(tmp_path,source=SyntheticSource(),token='test',api_only=True),headers=HEADERS) as client:
        item=setup_reader(client,theses=False)
        with database(tmp_path) as db:
            row=db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()
            content=row[0] if not isinstance(row[0],str) else json.loads(row[0])
            content['extra'].setdefault('_reader',{})['full']=False
            content['extra']['_reader']['reason']='Obsolete reason'
            content['extra'].pop('_collection_reason',None)
            db.execute("UPDATE publications SET content=%s,status='ready' WHERE id=%s",(json.dumps(content),item))
        material=client.get('/api/v3/publication/'+item).json()
        assert material['status']=='ready'
        assert 'Obsolete reason' not in str(material)


def test_unavailable_collection_without_reason_is_not_shown_as_waiting(tmp_path):
    from tests.support.storage import database
    from tests.support.reader import setup_reader, HEADERS
    from tests.support.synthetic_source import SyntheticSource
    from fastapi.testclient import TestClient
    with TestClient(create_app(tmp_path,source=SyntheticSource(),token='test',api_only=True),headers=HEADERS) as client:
        item=setup_reader(client,theses=False)
        with database(tmp_path) as db:
            db.execute("UPDATE publications SET status='pending_collection' WHERE id=%s",(item,))
            db.execute("INSERT INTO jobs(kind,item,status,reason_code,reason_message) VALUES ('fetch',%s,'unavailable',NULL,NULL) ON CONFLICT(kind,item) DO UPDATE SET status='unavailable',reason_code=NULL,reason_message=NULL",(item,))
        material=client.get('/api/v3/publication/'+item).json()
        assert any(job['operation']=='fetch' and job['status']=='unavailable' and job['reason_message'] is None for job in material['jobs'])


def test_saved_publication_does_not_duplicate_completeness_in_content(tmp_path):
    from tests.support.storage import database
    from tests.support.reader import setup_reader, HEADERS
    from tests.support.synthetic_source import SyntheticSource
    from fastapi.testclient import TestClient
    import json
    with TestClient(create_app(tmp_path,source=SyntheticSource(),token='test',api_only=True),headers=HEADERS) as client:
        item=setup_reader(client,theses=False)
        with database(tmp_path) as db:
            raw=db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]
        content=raw if isinstance(raw,dict) else json.loads(raw)
        extra=content.get('extra',{})
        assert '_collection_reason' not in extra
        assert 'full' not in extra.get('_reader',{}) and 'reason' not in extra.get('_reader',{})
