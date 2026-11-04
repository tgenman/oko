from tests.support.cards import read_all_cards
"""Feed information through HTTP and atomic storage lifecycle boundaries."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import httpx
import pytest

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


def test_unread_includes_hidden_and_viewed_dates_follow_actual_scope(tmp_path):
    clock = [1800000000.]
    with TestClient(create_app(tmp_path, token='test', source=FilterSource(), ai_transport=transport([]), wall_clock=lambda: clock[0]), headers=HEADERS) as client:
        setup_filters(client)
        path = '/api/v3/settings/feed/fixture'
        activity = lambda: client.get(path).json()['activity']
        assert activity()['unread'] == 3
        assert activity()['created'] == stamp(clock[0])
        wait_for(client, lambda: len(read_all_cards(client, '/api/v3/cards')) == 2)
        card = read_all_cards(client, '/api/v3/cards')[0]
        clock[0] += 10
        assert client.get('/api/v3/publication/' + card['id']).status_code == 200
        assert activity()['marked_read'] is None
        assert client.post('/api/v3/materials/' + card['id'] + '/viewed').status_code == 200
        assert activity()['unread'] == 2
        marked = activity()['marked_read']
        assert marked == stamp(clock[0])
        clock[0] += 10
        assert client.post('/api/v3/materials/' + card['id'] + '/viewed').status_code == 200
        assert activity()['marked_read'] == marked
        preview = client.post('/api/v3/folders/fixture/completion/preview').json()
        assert client.post('/api/v3/folders/fixture/completion', json={'token':preview['token']}).status_code == 200
        assert activity()['unread'] == 0
        assert activity()['marked_read'] == stamp(clock[0])
    with TestClient(create_app(tmp_path, token='test', source=FilterSource(), api_only=True), headers=HEADERS) as client:
        assert client.get(path).json()['activity']['marked_read'] == stamp(clock[0])
