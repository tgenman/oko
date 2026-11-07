"""Behavior of the agreed card-list and viewed interfaces against synthetic PostgreSQL."""
from fastapi.testclient import TestClient

from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app


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
                store.db._db.execute('CREATE TRIGGER synthetic_card_failure BEFORE UPDATE OF viewed ON publications FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_card_delete()')
        def remove_failure():
            store=app.state.host.require_store()
            with store.db.access_lock:
                store.db._db.execute('DROP TRIGGER synthetic_card_failure ON publications')
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
