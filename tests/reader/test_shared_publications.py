"""Saved linked publications remain available after a root is viewed."""
from tests.support.cards import read_all_cards
from tests.support.publication_pipeline import publication_root
from tests.support.app import create_app
from tests.support.reader import HEADERS,wait_for
from tests.support.presence import presence
from tests.support.v3 import TestClient


def test_link_cycle_and_files_survive_when_root_is_viewed(tmp_path):
    from tests.support.storage import Store
    import httpx
    from tests.support.telegram_web import page
    root=publication_root(tmp_path)
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(lambda request:httpx.Response(200,text=page([(11,'Public post')]))))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox',initial_history={'mode':'count','count':'1'}))
        item=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))['id']
    # Synthetic pre-existing graph. New adapters only add direct article links;
    # old/corrupt cycles and their retained files remain saved.
    store=Store(root,lambda _:None)
    try:
        with store.db:
            for identity in ('cycle-a','cycle-b'):
                store.db.execute("INSERT INTO publications ( id , url , platform , external_id , discovered_at ) VALUES ( %s , %s , 'other_web' , %s , 1 )",(identity,'https://example.test/'+identity,'https://example.test/'+identity))
            store.db.executemany('INSERT INTO publication_links VALUES ( %s , %s )',[(item,'cycle-a'),('cycle-a','cycle-b'),('cycle-b','cycle-a')])
    finally:
        store.db.close()
    cached=root/'media'/'cycle-a'/'file';cached.parent.mkdir(parents=True);cached.write_bytes(b'Unneeded cycle data')
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        assert client.post('/api/v3/materials/'+item+'/viewed').status_code==200
        assert cached.read_bytes() == b'Unneeded cycle data'
        assert client.get('/api/v3/publication/'+item).status_code==200
