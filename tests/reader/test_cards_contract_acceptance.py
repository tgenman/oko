"""Behavior of the agreed card-list and viewed interfaces against synthetic PostgreSQL."""
from fastapi.testclient import TestClient

from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app


def test_pages_are_compact_scoped_stable_and_validate_etag(tmp_path):
    feeds, _ = library(tmp_path, [5, 3])
    with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
        listing=client.get('/api/v3/cards',params={'feed':feeds[0],'limit':2})
        assert listing.status_code == 200
        page=listing.json()
        assert page['total']==5 and len(page['items'])==2
        assert set(page['items'][0]) == {'id','title','feed_id','published_at','source_url','preview_url','loading','changed','hidden','post_web_unavailable','external_attachment_kinds'}
        cursor=page['next_cursor']
        second=client.get('/api/v3/cards',params={'feed':feeds[0],'limit':2,'cursor':cursor})
        assert second.status_code==200 and second.json()['items'][0]['id']==cursor
        assert listing.headers['ETag'] != second.headers['ETag']
        assert client.get('/api/v3/cards',params={'feed':feeds[0],'limit':2},headers={'If-None-Match':listing.headers['ETag']}).status_code==304
        assert client.get('/api/v3/cards',params={'feed':feeds[1],'limit':2},headers={'If-None-Match':listing.headers['ETag']}).status_code==200
        assert client.get('/api/v3/cards',params={'feed':feeds[0],'folder':'example'}).status_code==422
        assert client.get('/api/v3/cards',params={'sort':'importance'}).status_code==422
        assert client.get('/api/v3/cards',params={'limit':0}).status_code==422
        assert client.get('/api/v3/cards',params={'feed':feeds[0],'cursor':'absent'}).json()=={'code':'cursor_expired'}


def test_oldest_and_newest_use_id_to_break_equal_time_ties(tmp_path):
    library(tmp_path, [4, 4])
    with TestClient(traced_app(tmp_path),headers=HEADERS) as client:
        def pages(order):
            result=[]
            params={'sort':order,'limit':1}
            while True:
                response=client.get('/api/v3/cards',params=params)
                assert response.status_code==200
                page=response.json();result.extend(page['items'])
                if page['next_cursor'] is None:return result
                params['cursor']=page['next_cursor']
        newest,oldest=pages('newest'),pages('oldest')
        assert len(newest)==len(oldest)==8
        assert {card['id'] for card in newest} == {card['id'] for card in oldest}
        assert len({card['id'] for card in newest})==8
        assert [card['published_at'] for card in newest] == sorted((card['published_at'] for card in newest),reverse=True)
        assert [card['published_at'] for card in oldest] == sorted(card['published_at'] for card in oldest)
        for result in (newest,oldest):
            for stamp in {card['published_at'] for card in result}:
                ids=[card['id'] for card in result if card['published_at']==stamp]
                assert ids==sorted(ids)


def test_viewed_id_list_rolls_back_on_database_failure_and_can_be_repeated(tmp_path):
    _, _=library(tmp_path,[4])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        first=client.get('/api/v3/cards').json()['items']
        ids=[card['id'] for card in first]
        def inject_failure():
            store=app.state.host.require_store()
            with store.db.access_lock:
                store.db._db.execute("CREATE FUNCTION pg_temp.reject_card_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic write failure'; END $$")
                store.db._db.execute('CREATE TRIGGER synthetic_card_failure BEFORE DELETE ON feed_publications FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_card_delete()')
        def remove_failure():
            store=app.state.host.require_store()
            with store.db.access_lock:
                store.db._db.execute('DROP TRIGGER synthetic_card_failure ON feed_publications')
        client.portal.call(inject_failure)
        try:
            failed=client.post('/api/v3/cards/read',json={'ids':ids[:2]})
            assert failed.status_code==500
            assert [card['id'] for card in client.get('/api/v3/cards').json()['items']]==ids
        finally:
            client.portal.call(remove_failure)
        for _ in range(2):
            done=client.post('/api/v3/cards/read',json={'ids':[ids[0],ids[1],'missing']})
            assert done.status_code==204 and done.content==b''
        assert [card['id'] for card in client.get('/api/v3/cards').json()['items']]==ids[2:]
        assert client.post('/api/v3/cards/read',json={'ids':ids[2:]}).status_code==204
        empty=client.get('/api/v3/cards').json()
        assert empty=={'items':[], 'total':0, 'next_cursor':None}
