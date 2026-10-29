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
        wait_for(client,lambda:all(read_all_cards(client, '/api/v3/cards', params={'feed':f['id']}) for f in feeds))
        yield client,feeds,root,models


def test_same_video_is_readable_in_both_feeds_and_viewed_everywhere(shared):
    client,feeds,_,_=shared
    cards=read_all_cards(client, '/api/v3/cards'); assert len(cards)==1
    for f in feeds:
        assert [c['id'] for c in read_all_cards(client, '/api/v3/cards', params={'feed':f['id']})]==[cards[0]['id']]
    composition=client.get('/api/v3/reading-composition').json()
    assert composition['count']==1 and [f['count'] for f in composition['feeds']]==[1,1]
    assert client.post('/api/v3/materials/'+cards[0]['id']+'/viewed').status_code==200
    assert read_all_cards(client, '/api/v3/cards')==[]
    assert client.get('/api/v3/materials/'+cards[0]['id']).status_code==404


def test_deleting_primary_feed_keeps_shared_publication_and_file(shared):
    client,feeds,_,_=shared
    item=read_all_cards(client, '/api/v3/cards')[0]['id']
    original=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
    primary=next(f for f in feeds if f['id']==original['feed_id'])
    remaining=next(f for f in feeds if f['id']!=primary['id'])
    media=original['content']['attachments'][0]['url']
    assert client.delete('/api/v3/feeds/'+primary['id']).status_code==200
    wait_for(client,lambda:not any(f['id']==primary['id'] for f in client.get('/api/v3/subscriptions').json()['feeds']))
    assert [c['id'] for c in read_all_cards(client, '/api/v3/cards', params={'feed':remaining['id']})]==[item]
    assert client.get('/api/v3/materials/'+item).status_code==200
    assert client.get(media).content==PNG


def test_deleting_secondary_feed_preserves_primary_and_shared_media(shared):
    client,feeds,_,_=shared
    item=read_all_cards(client, '/api/v3/cards')[0]['id']
    material=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
    primary=next(f for f in feeds if f['id']==material['feed_id'])
    secondary=next(f for f in feeds if f['id']!=primary['id'])
    media=material['content']['attachments'][0]['url']
    assert client.delete('/api/v3/feeds/'+secondary['id']).status_code==200
    wait_for(client,lambda:not any(f['id']==secondary['id'] for f in client.get('/api/v3/subscriptions').json()['feeds']))
    assert [card['id'] for card in read_all_cards(client, '/api/v3/cards', params={'feed':primary['id']})]==[item]
    assert client.get('/api/v3/materials/'+item).json()['feed_id']==primary['id']
    assert client.get(media).content==PNG


def test_viewed_native_publication_can_be_collected_again_from_explicit_history(tmp_path):
    from tests.support.telegram import Telegram,connect_request
    root=publication_root(tmp_path)
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Telegram()),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        feed=connected.json()['feed_id']
        first=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))
        wait_for(client,lambda:client.get('/api/v3/settings/feed/'+feed).json()['feed']['history']['state']=='complete')
        client.post('/api/v3/materials/'+first['id']+'/viewed')
        assert read_all_cards(client, '/api/v3/cards')==[]
        result=client.post('/api/v3/feeds/'+feed+'/history',json={'mode':'count','count':'1'})
        assert result.status_code==200,result.text
        again=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))
        assert again['id']!=first['id']
        assert client.get('/api/v3/materials/'+again['id']).json()['content']['text']=='Текст канала'


def test_shared_publication_respects_all_source_permissions_and_uses_one_result(shared):
    client,feeds,_,models=shared
    item=read_all_cards(client, '/api/v3/cards')[0]['id']
    client.put('/api/v3/processors/llamacpp',json={'endpoint':'https://public.example','model':'local:latest','local_execution':False})
    client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp'})
    first=client.get('/api/v3/materials/'+item).json()['feed_id']
    other=next(f['id'] for f in feeds if f['id']!=first)
    client.patch('/api/v3/feeds/'+other,json={'public_ai':False})
    client.patch('/api/v3/feeds/'+first,json={'public_ai':True})
    client.patch('/api/v3/settings/feed/'+first,json={'theses':True})
    wait_for(client,lambda:any(w.get('reason')=='public_ai_not_allowed' for w in client.get('/api/v3/status/details').json()['work']))
    assert not [r for r in models if r.url.path=='/v1/chat/completions']
    client.patch('/api/v3/feeds/'+other,json={'public_ai':True})
    wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['theses']['result'])
    assert len([r for r in models if r.url.path=='/v1/chat/completions'])==1
    client.patch('/api/v3/settings/feed/'+other,json={'prompt_theses':'Other unused rules'})
    client.get('/api/v3/status/details')
    assert client.get('/api/v3/materials/'+item).json()['theses']['result']
    assert len([r for r in models if r.url.path=='/v1/chat/completions'])==1


def test_cancelled_native_history_skips_archive_but_collects_future_posts(tmp_path):
    import time,asyncio,threading
    from tests.support.storage import database
    from tests.support.telegram import Telegram,connect_request
    root=publication_root(tmp_path)
    tick=[100.]
    numbers=[1,2,3]
    locating=threading.Event()
    class Client(Telegram):
        async def page(self,*args):
            return dict(messages=[dict(id=n,date=time.time()-60,text='Post '+str(n),group=None,media=None) for n in numbers],more=False,gap=None)
        async def history_page(self,account,peer,upper,before,since):
            if before:
                locating.set()
                await asyncio.Event().wait()
            rows=(await self.page())['messages']
            latest=max(rows,key=lambda row:row['id'])
            return dict(messages=[latest],upper=latest['id'],next_before=latest['id'],more=True)
        async def messages(self,account,peer,ids):
            return [m for m in (await self.page())['messages'] if m['id'] in ids]
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client(),clock=lambda:tick[0],lease=10000,grace=10000),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':2}))
        assert connected.status_code==200,connected.text
        feed=connected.json()['feed_id']
        def cursor_saved():
            with database(root) as db:
                return bool(db.execute('SELECT cursor FROM feeds WHERE id = %s',(feed,)).fetchone()[0])
        wait_for(client,cursor_saved)
        tick[0]+=1
        assert locating.wait(2)
        assert read_all_cards(client, '/api/v3/cards')==[]
        assert client.delete('/api/v3/feeds/'+feed+'/history').status_code==200
        tick[0]+=1
        def cancelled_cursor_saved():
            from oko.collection.telegram.contracts import HistoryEnvelope
            with database(root) as db:
                token=db.execute('SELECT cursor FROM feeds WHERE id = %s',(feed,)).fetchone()[0]
            return HistoryEnvelope.decode(token,adapter='client').position.get('initial_complete')
        wait_for(client,cancelled_cursor_saved)
        numbers.append(4)
        tick[0]+=1
        assert client.post('/api/v3/collect').status_code==200
        card=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))
        assert client.get('/api/v3/materials/'+card['id']).json()['content']['text']=='Post 4'
        assert len(read_all_cards(client, '/api/v3/cards'))==1


def test_shared_media_download_continues_after_its_original_feed_is_deleted(tmp_path):
    import asyncio,threading
    root=publication_root(tmp_path)
    started,release=threading.Event(),threading.Event()
    async def response(request):
        if request.url.path=='/feeds/videos.xml':
            return httpx.Response(200,text=feed_xml())
        started.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        try:
            presence(client,'reader')
            client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
            for channel in (CHANNEL,'UC'+'b'*22):
                add_sources(client,[('https://www.youtube.com/feeds/videos.xml?channel_id='+channel,channel,'Inbox')])
            assert started.wait(3)
            wait_for(client,lambda:all(f['count']==1 for f in client.get('/api/v3/reading-composition').json()['feeds']))
            card=read_all_cards(client, '/api/v3/cards')[0]
            assert client.delete('/api/v3/feeds/'+card['feed_id']).status_code==200
            release.set()
            material=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+card['id']).json())['content']['attachments'] else None))
            assert client.get(material['content']['attachments'][0]['url']).content==PNG
        finally:
            release.set()


def test_shared_card_owner_and_time_order_do_not_change_with_feed_importance(shared):
    client,feeds,_,_=shared
    item=read_all_cards(client, '/api/v3/cards')[0]
    primary=item['feed_id']
    secondary=next(f for f in feeds if f['id']!=primary)
    client.patch('/api/v3/feeds/'+secondary['id'],json={'importance':'high'})
    result=add_sources(client,[('https://www.youtube.com/feeds/videos.xml?channel_id=UC'+'c'*22,'Third','Folder 1')])
    third=next(f for f in result.json()['feeds'] if f['title']=='Third')
    client.patch('/api/v3/feeds/'+third['id'],json={'importance':'elevated','folder_id':secondary['folder_id']})
    wait_for(client,lambda:len(read_all_cards(client, '/api/v3/cards'))==2)
    before=read_all_cards(client, '/api/v3/cards',params={'folder':secondary['folder_id']})
    client.patch('/api/v3/feeds/'+primary,json={'importance':'high'})
    after=read_all_cards(client, '/api/v3/cards',params={'folder':secondary['folder_id']})
    assert [card['id'] for card in before]==[card['id'] for card in after]
    assert next(card for card in after if card['id']==item['id'])['feed_id']==primary



def test_unreachable_link_cycle_does_not_retain_files_after_last_root_is_viewed(tmp_path):
    from tests.support.storage import Store
    from tests.support.telegram import Telegram,connect_request
    root=publication_root(tmp_path)
    options=dict(token='test',api_only=True,telegram_client=Telegram())
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        item=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))['id']
    # Synthetic pre-existing graph. New adapters only add direct article links;
    # old/corrupt cycles still must not keep an unreachable local file alive.
    store=Store(root,lambda _:None)
    try:
        with store.db:
            for identity in ('cycle-a','cycle-b'):
                store.db.execute("INSERT INTO items ( id , source_id , url , platform , external_id ) VALUES ( %s , %s , %s , 'other_web' , %s )",(identity,identity,'https://example.test/'+identity,'https://example.test/'+identity))
            store.db.executemany('INSERT INTO publication_links VALUES ( %s , %s )',[(item,'cycle-a'),('cycle-a','cycle-b'),('cycle-b','cycle-a')])
    finally:
        store.db.close()
    cached=root/'media'/'cycle-a'/'file';cached.parent.mkdir(parents=True);cached.write_bytes(b'Unneeded cycle data')
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        assert client.post('/api/v3/materials/'+item+'/viewed').status_code==200
        assert not cached.exists()
        assert client.get('/api/v3/materials/'+item).status_code==404
