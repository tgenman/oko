from tests.support.source import publication_packet
"""Feed information through HTTP and atomic storage lifecycle boundaries."""
from dataclasses import replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import httpx
import pytest

from oko.collection.demo import DemoSource
from oko.collection.contracts import DiscoveryResult, Publication, SourceError, SourceObservation
from oko.storage.store import Store
from tests.support.app import create_app
from tests.support.filters import FilterSource, setup_filters, transport
from tests.support.reader import HEADERS, wait_for
from tests.support.subscriptions import configured_sources


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def test_feed_title_folder_atomicity_noops_and_restart(tmp_path):
    configured_sources(tmp_path, [('https://one.test/feed', 'One', 'First'), ('https://two.test/feed', 'Two', 'Second')])
    clock = [1800000000.]
    app = lambda: create_app(tmp_path, token='test', api_only=True, wall_clock=lambda: clock[0])
    with TestClient(app(), headers=HEADERS) as client:
        catalog = client.get('/api/v3/subscriptions').json()
        one, two = sorted(catalog['feeds'], key=lambda f: f['title'])
        feed = one['id']
        path = '/api/v3/settings/feed/' + feed
        activity = lambda: client.get(path).json()['activity']
        initial=activity()
        assert initial['created'] is not None and initial['unread']==0
        assert client.patch('/api/v3/feeds/' + feed, json={'title':'One'}).status_code == 200
        assert activity()['modified']==initial['modified']
        # All validation precedes any write, including title and Modified.
        bad = client.patch('/api/v3/feeds/' + feed, json={'title':'New', 'folder_id':'missing'})
        assert bad.status_code == 400
        assert client.get(path).json()['feed']['title'] == 'One'
        assert activity()['modified']==initial['modified']
        for title in ('', '   ', 'a'*201, None):
            assert client.patch('/api/v3/feeds/' + feed, json={'title':title}).status_code in (400, 422)
        assert client.patch(path, json={'originals':False}).status_code == 200
        clock[0] += 100
        reply = client.patch('/api/v3/feeds/' + feed, json={'title':'  Renamed  ', 'folder_id':two['folder_id']}, headers={'X-Oko-Action':'rename-move'})
        assert reply.status_code == 200, reply.text
        assert reply.json()['title'] == 'Renamed'
        assert reply.json()['id'] == feed
        assert reply.json()['identity'] == one['identity']
        assert reply.json()['original_urls'] == one['original_urls']
        assert client.get(path).json()['effective']['originals']['value'] is False
        modified = activity()['modified']
        assert modified == stamp(clock[0])
        assert client.get('/api/v3/commands/rename-move').json()['context']['fields'] == ['folder_id', 'title']
        clock[0] += 100
        assert client.patch('/api/v3/feeds/' + feed, json={'title':'Renamed', 'folder_id':two['folder_id']}).status_code == 200
        assert client.patch(path, json={'originals':False}).status_code == 200
        assert client.patch('/api/v3/settings/folder/'+two['folder_id'], json={'theses':True}).status_code == 200
        assert activity()['modified'] == modified
        assert client.patch(path, json={'originals':None}).status_code == 200
        assert activity()['modified'] == stamp(clock[0])
    with TestClient(app(), headers=HEADERS) as client:
        saved = client.get(path).json()
        assert saved['feed']['title'] == 'Renamed'
        assert saved['feed']['folder_id'] == two['folder_id']
        assert saved['activity']['created']==initial['created']
        assert saved['activity']['modified'] == stamp(clock[0])


def test_new_feed_dates_rss_description_empty_and_missing_metadata(tmp_path):
    raw = ['<rss><channel><title>RSS</title><description><![CDATA[<b>Channel</b> description]]></description></channel></rss>']
    clock = [1800000000.]
    def respond(request):
        return httpx.Response(200, text=raw[0])
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_enabled=True, rss_transport=httpx.MockTransport(respond), wall_clock=lambda: clock[0]), headers=HEADERS) as client:
        from tests.support.subscriptions import add_source
        feed = add_source(client, 'https://example.test/feed')['id']
        path = '/api/v3/settings/feed/' + feed
        def activity():
            return client.get(path).json()['activity']
        wait_for(client, lambda: activity()['updated'])
        assert activity()['created'] == activity()['modified'] == stamp(clock[0])
        assert activity()['description'] == 'Channel description'
        clock[0] += 10
        raw[0] = '<rss><channel><title>Changed externally</title></channel></rss>'
        assert client.post('/api/v3/collect').status_code == 200
        wait_for(client, lambda: activity()['updated'] == stamp(clock[0]))
        assert activity()['description'] == 'Channel description'
        assert client.get(path).json()['feed']['title'] == 'Источник'
        raw[0] = '<rss><channel><description/></channel></rss>'
        clock[0] += 10
        assert client.post('/api/v3/collect').status_code == 200
        wait_for(client, lambda: activity()['updated'] == stamp(clock[0]))
        assert activity()['description'] == ''
        assert activity()['modified'] == stamp(1800000000.)


def test_unread_includes_hidden_and_viewed_dates_follow_actual_scope(tmp_path):
    clock = [1800000000.]
    with TestClient(create_app(tmp_path, token='test', source=FilterSource(), ai_transport=transport([]), wall_clock=lambda: clock[0]), headers=HEADERS) as client:
        setup_filters(client)
        path = '/api/v3/settings/feed/demo'
        activity = lambda: client.get(path).json()['activity']
        assert activity()['unread'] == 3
        assert activity()['created'] == stamp(clock[0])
        wait_for(client, lambda: len(client.get('/api/v3/cards').json()) == 2)
        card = client.get('/api/v3/cards').json()[0]
        clock[0] += 10
        assert client.get('/api/v3/materials/' + card['id']).status_code == 200
        assert activity()['marked_read'] is None
        assert client.post('/api/v3/materials/' + card['id'] + '/viewed').status_code == 200
        assert activity()['unread'] == 2
        marked = activity()['marked_read']
        assert marked == stamp(clock[0])
        clock[0] += 10
        assert client.post('/api/v3/materials/' + card['id'] + '/viewed').status_code == 200
        assert activity()['marked_read'] == marked
        preview = client.post('/api/v3/folders/demo/completion/preview').json()
        assert client.post('/api/v3/folders/demo/completion', json={'token':preview['token']}).status_code == 200
        assert activity()['unread'] == 0
        assert activity()['marked_read'] == stamp(clock[0])
    with TestClient(create_app(tmp_path, token='test', source=FilterSource(), api_only=True), headers=HEADERS) as client:
        assert client.get(path).json()['activity']['marked_read'] == stamp(clock[0])


class MetadataSource(DemoSource):
    def __init__(self):
        self.fail = False
        self.failures = 0
        self.description = 'Source description'

    async def discover(self, cursor: str | None) -> DiscoveryResult:
        if self.fail:
            self.failures += 1
            raise SourceError('Synthetic failure')
        return replace(await super().discover(cursor), observation=SourceObservation(description=self.description))


def test_failed_discovery_preserves_update_and_unread_metadata(tmp_path):
    source = MetadataSource()
    clock = [1800000000.]
    steady = [100.]
    with TestClient(create_app(tmp_path, source=source, token='test', api_only=True, clock=lambda: steady[0], wall_clock=lambda: clock[0]), headers=HEADERS) as client:
        path = '/api/v3/settings/feed/demo'
        wait_for(client, lambda: client.get(path).json()['activity']['updated'])
        before = client.get(path).json()['activity']
        clock[0] += 10
        source.fail = True
        client.post('/api/v3/collect')
        wait_for(client, lambda: source.failures > 0)
        assert client.get(path).json()['activity'] == before
        source.fail = False
        steady[0] += 1000
        client.post('/api/v3/collect')
        wait_for(client, lambda: client.get(path).json()['activity']['updated'] == stamp(clock[0]))


def test_stale_and_rolled_back_discovery_cannot_advance_date(tmp_path):
    clock = [1800000000.]
    def fault(point):
        if point == 'before_discovery_commit':
            raise RuntimeError('Synthetic crash')
    store = Store(tmp_path, fault, wall_clock=lambda: clock[0])
    store.subscriptions.ensure_demo()
    store.enqueue_discovery()
    job = store.next_job(0)
    assert job
    publications = [publication_packet(Publication('one', 'https://example.test/one', 1800000000.))]
    with pytest.raises(RuntimeError, match='Synthetic crash'):
        store.discovered(publications, 'one', job, description='Uncommitted')
    assert store.subscriptions.details.snapshot('demo')['updated'] is None
    assert store.subscriptions.details.snapshot('demo')['description'] is None
    store.retry(job, 0, 'stale', 1)
    store.discovered(publications, 'two', job, description='Stale')
    assert store.subscriptions.details.snapshot('demo')['updated'] is None
    assert store.subscriptions.details.snapshot('demo')['unread'] == 0
    store.db.close()
