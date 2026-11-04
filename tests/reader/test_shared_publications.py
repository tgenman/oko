"""Shared external identities and reading lifetime through the reader HTTP."""
from tests.support.cards import read_all_cards
import httpx
import pytest

from tests.support.publication_pipeline import publication_root
from tests.support.app import create_app
from tests.support.reader import HEADERS,wait_for
from tests.support.presence import presence
from tests.support.subscriptions import add_sources
from tests.support.youtube import CHANNEL,VIDEO,PNG,feed_xml
from tests.support.v3 import TestClient
from tests.support.models import model_transport


@pytest.fixture
def shared(tmp_path):
    root=publication_root(tmp_path)
    def response(request):
        return httpx.Response(200,text=feed_xml().replace(VIDEO,'lmnopqrstuv') if any(value.endswith('c'*22) for value in request.url.params.values()) else feed_xml()) if request.url.path=='/feeds/videos.xml' else httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    class Captions:
        async def fetch(self,video):
            return 'Shared captions'
    models=[]
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(response),youtube_captions=Captions(),ai_transport=model_transport(models)),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        feeds=[]
        for n,channel in enumerate((CHANNEL,'UC'+'b'*22)):
            url='https://www.youtube.com/feeds/videos.xml?channel_id='+channel
            result=add_sources(client,[(url,'Feed '+str(n),'Folder '+str(n))])
            assert result.status_code==200,result.text
            feed=next(f for f in result.json()['feeds'] if url in f['original_urls']);feeds.append(feed)
            client.patch('/api/v3/feeds/'+feed['id'],json={'theses':False})
        wait_for(client,lambda:'Publication ownership conflict' in client.get('/api/v3/status/details').text)
        yield client,feeds,root,models


def test_same_video_conflict_is_reported_without_moving_the_publication(shared):
    client,feeds,_,_=shared
    cards=read_all_cards(client, '/api/v3/cards')
    assert len(cards)==1
    item=cards[0]['id']
    original=client.get('/api/v3/publication/'+item).json()
    owner=original['feed_id']
    other=next(feed['id'] for feed in feeds if feed['id']!=owner)
    assert [card['id'] for card in read_all_cards(client,'/api/v3/cards',params={'feed':owner})]==[item]
    assert read_all_cards(client,'/api/v3/cards',params={'feed':other})==[]
    details=client.get('/api/v3/status/details').text
    assert all(value in details for value in ('Publication ownership conflict',item,owner,other))
    assert sorted(feed['count'] for feed in client.get('/api/v3/reading-composition').json()['feeds'])==[0,1]


def test_deleting_owner_does_not_transfer_publication_to_conflicting_feed(shared):
    client,feeds,_,_=shared
    item=read_all_cards(client,'/api/v3/cards')[0]['id']
    original=wait_for(client,lambda:(m if (m:=client.get('/api/v3/publication/'+item).json()) and any(media['role']=='attachment' for media in m['media']) else None))
    owner=original['feed_id']
    other=next(feed['id'] for feed in feeds if feed['id']!=owner)
    assert client.delete('/api/v3/feeds/'+owner).status_code==200
    wait_for(client,lambda:not any(feed['id']==owner for feed in client.get('/api/v3/subscriptions').json()['feeds']))
    assert read_all_cards(client,'/api/v3/cards',params={'feed':other})==[]
    assert client.get('/api/v3/publication/'+item).status_code==404


def test_other_feed_permission_does_not_override_owning_feed(shared):
    client,feeds,_,models=shared
    item=read_all_cards(client,'/api/v3/cards')[0]['id']
    owner=client.get('/api/v3/publication/'+item).json()['feed_id']
    other=next(feed['id'] for feed in feeds if feed['id']!=owner)
    client.put('/api/v3/processors/llamacpp',json={'endpoint':'https://public.example','model':'local:latest','local_execution':False})
    client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp'})
    client.patch('/api/v3/feeds/'+other,json={'public_ai':False})
    client.patch('/api/v3/feeds/'+owner,json={'public_ai':True})
    client.patch('/api/v3/settings/feed/'+owner,json={'theses':True})
    wait_for(client,lambda:client.get('/api/v3/publication/'+item).json()['theses']['result'])
    assert len([request for request in models if request.url.path=='/v1/chat/completions'])==1


def test_unreachable_link_cycle_does_not_retain_files_after_last_root_is_viewed(tmp_path):
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
    # old/corrupt cycles still must not keep an unreachable local file alive.
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
        assert not cached.exists()
        assert client.get('/api/v3/publication/'+item).status_code==404
