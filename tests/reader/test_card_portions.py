"""Card hydration is bounded independently of the complete reading composition."""
import pytest
from fastapi.testclient import TestClient
from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app


@pytest.mark.parametrize('sort', ['newest', 'oldest', 'importance'])
def test_portions_keep_complete_order_and_only_hydrate_requested_cards(tmp_path, sort):
    feeds, _ = library(tmp_path, [53, 52])
    with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
        options = dict(target=dict(scope='all'), sort=sort, show_hidden=False)
        request = dict(kind='list', options=options, known_version=None, known_cards={}, card_limit=40)
        def read():
            response = client.post('/api/v3/reading/check', json=dict(storage_id=None, resources=[request]))
            assert response.status_code == 200, response.text
            resources = response.json()['resources']
            return next(r for r in resources if r['kind']=='list'), [r for r in resources if r['kind']=='card']
        listing, cards = read()
        ids = listing['data']['ids']
        assert len(ids) == 105
        assert [c['id'] for c in cards] == ids[:40]
        assert all(c['state']=='changed' for c in cards)
        expected = client.get('/api/v3/cards', params=dict(sort=sort)).json()
        assert ids == [card['id'] for card in expected]
        request.update(known_version=listing['version'], card_limit=80,
                       known_cards={id:next((c['version'] for c in cards if c['id']==id), None) for id in ids[:80]})
        repeated, next_cards = read()
        assert repeated['state']=='unchanged'
        assert [c['id'] for c in next_cards if c['state']=='changed'] == ids[40:80]
        assert [c['id'] for c in next_cards if c['state']=='unchanged'] == ids[:40]
        # A mutation publishes a new full composition but never expands hydration
        # to the unrequested tail. Existing card versions remain reusable.
        assert client.post('/api/v3/materials/'+ids[0]+'/viewed').status_code == 200
        request.update(card_limit=40, known_cards={c['id']:c['version'] for c in cards})
        changed, after = read()
        assert changed['data']['ids'] == ids[1:]
        assert len(after)==40
        assert [c['id'] for c in after if c['state']=='changed'] == [ids[40]]
        # Range confirmation can obtain exact captured tail titles without
        # hydrating the whole list or changing list membership.
        tail=ids[-5:]
        response=client.post('/api/v3/reading/check',json=dict(storage_id=None,resources=[dict(kind='card',id=id,known_version=None) for id in tail]))
        assert [r['id'] for r in response.json()['resources']]==tail
        request.update(options=dict(options,target=dict(scope='feed',feed_id=feeds[1])),known_version=None,known_cards={},card_limit=0)
        empty,cards=read()
        assert len(empty['data']['ids'])==52-(1 if ids[0] in {c['id'] for c in expected if c['feed_id']==feeds[1]} else 0)
        assert cards==[]
