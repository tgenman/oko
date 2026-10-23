"""Shared external identities and reading lifetime through the reader HTTP."""
import httpx
import pytest

from tests.reader.test_independent_media import migrated_root
from tests.support.app import create_app
from tests.support.reader import HEADERS,wait_for
from tests.support.presence import presence
from tests.support.subscriptions import add_sources
from tests.support.youtube import CHANNEL,VIDEO,PNG,feed_xml
from tests.support.v3 import TestClient
from tests.support.models import model_transport


@pytest.fixture
def shared(tmp_path):
    root=migrated_root(tmp_path)
    def response(request):
        return httpx.Response(200,text=feed_xml()) if request.url.path=='/feeds/videos.xml' else httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
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
        wait_for(client,lambda:all(client.get('/api/v3/cards',params={'feed':f['id']}).json() for f in feeds))
        yield client,feeds,root,models


def test_same_video_is_readable_in_both_feeds_and_viewed_everywhere(shared):
    client,feeds,_,_=shared
    cards=client.get('/api/v3/cards').json(); assert len(cards)==1
    for f in feeds:
        assert [c['id'] for c in client.get('/api/v3/cards',params={'feed':f['id']}).json()]==[cards[0]['id']]
    composition=client.get('/api/v3/reading-composition').json()
    assert composition['count']==1 and [f['count'] for f in composition['feeds']]==[1,1]
    assert client.post('/api/v3/materials/'+cards[0]['id']+'/viewed').status_code==200
    assert client.get('/api/v3/cards').json()==[]
    assert client.get('/api/v3/materials/'+cards[0]['id']).status_code==404


def test_deleting_primary_feed_keeps_shared_publication_and_file(shared):
    client,feeds,_,_=shared
    item=client.get('/api/v3/cards').json()[0]['id']
    original=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
    primary=next(f for f in feeds if f['id']==original['feed_id'])
    remaining=next(f for f in feeds if f['id']!=primary['id'])
    media=original['content']['attachments'][0]['url']
    assert client.delete('/api/v3/feeds/'+primary['id']).status_code==200
    wait_for(client,lambda:not any(f['id']==primary['id'] for f in client.get('/api/v3/subscriptions').json()['feeds']))
    assert [c['id'] for c in client.get('/api/v3/cards',params={'feed':remaining['id']}).json()]==[item]
    assert client.get('/api/v3/materials/'+item).status_code==200
    assert client.get(media).content==PNG


def test_viewed_native_publication_can_be_collected_again_from_explicit_history(tmp_path):
    from tests.support.telegram import Telegram,connect_request
    root=migrated_root(tmp_path)
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Telegram()),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        feed=connected.json()['feed_id']
        first=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))
        wait_for(client,lambda:client.get('/api/v3/settings/feed/'+feed).json()['feed']['history']['state']=='complete')
        client.post('/api/v3/materials/'+first['id']+'/viewed')
        assert client.get('/api/v3/cards').json()==[]
        result=client.post('/api/v3/feeds/'+feed+'/history',json={'mode':'count','count':'1'})
        assert result.status_code==200,result.text
        again=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))
        assert again['id']!=first['id']
        assert client.get('/api/v3/materials/'+again['id']).json()['content']['text']=='Текст канала'


def test_shared_publication_respects_all_source_permissions_and_uses_one_result(shared):
    client,feeds,_,models=shared
    item=client.get('/api/v3/cards').json()[0]['id']
    client.put('/api/v3/processors/llamacpp',json={'endpoint':'https://public.example','model':'local:latest','local_execution':False})
    client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp'})
    first=client.get('/api/v3/materials/'+item).json()['feed_id']
    other=next(f['id'] for f in feeds if f['id']!=first)
    client.patch('/api/v3/feeds/'+other,json={'public_ai':False})
    client.patch('/api/v3/feeds/'+first,json={'public_ai':True})
    client.patch('/api/v3/settings/feed/'+first,json={'theses':True})
    wait_for(client,lambda:any(w.get('reason')=='public_ai_not_allowed' for w in client.get('/api/v3/status').json()['work']))
    assert not [r for r in models if r.url.path=='/v1/chat/completions']
    client.patch('/api/v3/feeds/'+other,json={'public_ai':True})
    wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['theses']['result'])
    assert len([r for r in models if r.url.path=='/v1/chat/completions'])==1
    client.patch('/api/v3/settings/feed/'+other,json={'prompt_theses':'Other unused rules'})
    client.get('/api/v3/status')
    assert client.get('/api/v3/materials/'+item).json()['theses']['result']
    assert len([r for r in models if r.url.path=='/v1/chat/completions'])==1


def test_cancelled_native_history_skips_archive_but_collects_future_posts(tmp_path):
    import time,sqlite3
    from tests.support.telegram import Telegram,connect_request
    root=migrated_root(tmp_path)
    tick=[100.]
    numbers=[1,2,3]
    class Client(Telegram):
        async def page(self,*args):
            return dict(messages=[dict(id=n,date=time.time()-60,text='Post '+str(n),group=None,media=None) for n in numbers],more=False,gap=None)
        async def messages(self,account,peer,ids):
            return [m for m in (await self.page())['messages'] if m['id'] in ids]
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client(),clock=lambda:tick[0],lease=10000,grace=10000),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':2}))
        assert connected.status_code==200,connected.text
        feed=connected.json()['feed_id']
        def cursor_saved():
            with sqlite3.connect(root/'state.sqlite') as db:
                return bool(db.execute('SELECT cursor FROM feeds WHERE id=?',(feed,)).fetchone()[0])
        wait_for(client,cursor_saved)
        assert client.get('/api/v3/cards').json()==[]
        assert client.delete('/api/v3/feeds/'+feed+'/history').status_code==200
        tick[0]+=1
        wait_for(client,lambda:client.get('/api/v3/settings/feed/'+feed).json()['feed']['history']['progress']['scan_complete'])
        numbers.append(4)
        tick[0]+=1
        assert client.post('/api/v3/collect').status_code==200
        card=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))
        assert client.get('/api/v3/materials/'+card['id']).json()['content']['text']=='Post 4'
        assert len(client.get('/api/v3/cards').json())==1
