"""Native channel publications through actual reader HTTP and temporary storage."""
from tests.support.cards import read_all_cards
import asyncio
import pytest
import json
from tests.support.storage import database
import threading
import time

from tests.support.publication_pipeline import publication_root
from tests.support.app import create_app
from tests.support.reader import HEADERS,wait_for
from tests.support.presence import presence
from tests.support.telegram import Telegram,connect_request
from tests.support.v3 import TestClient


def test_channel_text_is_ready_before_independent_native_media(tmp_path):
    root=publication_root(tmp_path)
    started,release=threading.Event(),threading.Event()
    class Client(Telegram):
        async def info(self,account,peer):
            return dict(peer=-1001234567890,username='fixture',title='Channel',kind='channel',forum=False)
        async def page(self,account,peer,after,since):
            return dict(messages=[dict(id=11,date=time.time()-60,text='Native own text',group=None,
                media=dict(kind='image',extension='jpg',mime='image/jpeg'))],more=False,gap=None)
        async def messages(self,account,peer,ids):
            return (await self.page(account,peer,0,0))['messages']
        async def download(self,account,peer,id):
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return b'native-image'
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client()),headers=HEADERS) as client:
        try:
            presence(client,'reader')
            client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
            result=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
            assert result.status_code==200,result.text
            assert started.wait(3)
            card=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))
            material=client.get('/api/v3/materials/'+card['id']).json()
            assert material['content']['text']=='Native own text'
            with database(root) as db:
                assert db.execute('SELECT platform , external_id FROM items WHERE id = %s',(card['id'],)).fetchone()==('telegram','https://t.me/fixture/11')
                assert db.execute('SELECT adapter FROM feeds WHERE id = %s',(card['feed_id'],)).fetchone()[0]=='telegram_client_adapter'
                content=json.loads(db.execute('SELECT content FROM items WHERE id = %s',(card['id'],)).fetchone()[0])
                media_id=next(b['media_id'] for b in content['blocks'] if b['type']=='media')
                assert db.execute('SELECT status FROM media_files WHERE id = %s',(media_id,)).fetchone()[0]=='pending_download'
            release.set()
            ready=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+card['id']).json())['content']['attachments'] else None))
            assert client.get(ready['content']['attachments'][0]['url']).content==b'native-image'
        finally:
            release.set()


def test_native_media_flood_wait_blocks_only_its_account_and_survives_restart(tmp_path):
    from oko.collection.contracts import SourceError
    root=publication_root(tmp_path)
    tick,wall=[100.],[1_900_000_000.]
    calls=[]
    blocked=[False]
    class Client(Telegram):
        async def info(self,account,peer):
            number=2 if peer in ('@second',-1001234567892) else 1
            return dict(peer=-1001234567890-number,username='first' if number==1 else 'second',title=str(number),kind='channel',forum=False)
        async def page(self,account,peer,after,since):
            return dict(messages=[dict(id=11,date=wall[0]-60,text='Post '+str(peer),group=None,
                media=dict(kind='image',extension='jpg',mime='image/jpeg'))],more=False,gap=None)
        async def messages(self,account,peer,ids):
            return (await self.page(account,peer,0,0))['messages']
        async def download(self,account,peer,id):
            calls.append(peer)
            if not blocked[0]:
                blocked[0]=True
                error=SourceError('Synthetic FloodWait',123)
                error.remote_type='FloodWaitError'
                raise error
            return b'native-image'
    options=dict(token='test',api_only=True,telegram_client=Client(),clock=lambda:tick[0],wall_clock=lambda:wall[0],lease=10000,grace=10000)
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        for name in ('first','second'):
            result=connect_request(client,json=dict(account='fixture',peer='@'+name,initial_history={'mode':'count','count':1}))
            assert result.status_code==200,result.text
        def waiting():
            if calls:
                return True
            tick[0]+=1
            return False
        wait_for(client,waiting)
        time.sleep(.1)
        assert len(calls)==1
    tick[0]=0
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        wall[0]+=122
        tick[0]+=122
        time.sleep(.1)
        assert len(calls)==1
        wall[0]+=1
        tick[0]+=1
        wait_for(client,lambda:len(calls)==3)
        assert len(read_all_cards(client, '/api/v3/cards'))==2




def test_explicit_web_to_client_switch_keeps_feed_and_publication(tmp_path):
    import httpx
    from tests.support.telegram_web import page
    from tests.support.telegram import configure,settled,configure_fast_web
    root=publication_root(tmp_path)
    class Client(Telegram):
        async def info(self,account,peer):
            return dict(peer=-1001234567890,username='fixture',title='Verified channel',kind='channel',forum=False)
        async def messages(self,*args):
            return [dict(id=11,date=time.time()-60,text='New access text',group=None,media=None)]
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client(),
            rss_transport=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Old web text')])))),headers=HEADERS) as client:
        presence(client,'reader')
        configure_fast_web(client)
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        initial=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Original folder',initial_history={'mode':'count','count':'1'}))
        assert initial.status_code==200,initial.text
        feed=initial.json()['feed_id']
        item=settled(client)[0]['id']
        configure(client)
        switched=client.post('/api/v3/telegram/connect',json=dict(feed_id=feed,source_locator='@fixture',folder='Ignored folder',access={'kind':'client','profile':'personal'}))
        assert switched.status_code==200,switched.text
        assert switched.json()['feed_id']==feed
        updated=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['text']=='New access text' else None))
        assert updated['id']==item and len(read_all_cards(client, '/api/v3/cards'))==1
        with database(root) as db:
            assert db.execute('SELECT external_id , adapter FROM feeds WHERE id = %s',(feed,)).fetchone()==('-1001234567890','telegram_client_adapter')
        catalog=client.get('/api/v3/subscriptions').json()
        assert next(f for f in catalog['folders'] if f['title']=='Original folder')


def test_switch_rejects_late_native_file_and_keeps_new_web_content(tmp_path):
    import httpx
    from tests.support.telegram_web import page
    from tests.support.telegram import configure_fast_web
    root=publication_root(tmp_path)
    started,release,cancelled=threading.Event(),threading.Event(),threading.Event()
    class Client(Telegram):
        async def info(self,account,peer):
            return dict(peer=-1001234567890,username='fixture',title='Channel',kind='channel',forum=False)
        async def page(self,account,peer,after,since):
            return dict(messages=[dict(id=11,date=time.time()-60,text='Native old text',group=None,
                media=dict(kind='image',extension='jpg',mime='image/jpeg'))],more=False,gap=None)
        async def messages(self,account,peer,ids):
            return (await self.page(account,peer,0,0))['messages']
        async def download(self,*args):
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(.01)
                except asyncio.CancelledError:
                    cancelled.set()
            return b'OLD-NATIVE-MEDIA-MUST-NOT-BE-SAVED'
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client(),
            rss_transport=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'New selected web text')])))),headers=HEADERS) as client:
        try:
            presence(client,'reader')
            configure_fast_web(client)
            client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
            result=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
            assert result.status_code==200,result.text
            assert started.wait(3)
            item=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))['id']
            changed=client.post('/api/v3/telegram/connect',json=dict(feed_id=result.json()['feed_id'],source_locator='@fixture',folder='Keep',access={'kind':'web'}))
            assert changed.status_code==200,changed.text
            assert cancelled.wait(1)
            wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['content']['text']=='New selected web text')
            release.set()
            time.sleep(.1)
            value=client.get('/api/v3/materials/'+item).json()
            assert value['content']['text']=='New selected web text' and value['content']['attachments']==[]
            assert not any(b'OLD-NATIVE-MEDIA-MUST-NOT-BE-SAVED' in p.read_bytes() for p in root.rglob('*') if p.is_file())
        finally:
            release.set()


def test_native_auto_read_waits_for_saved_file_and_manual_mode_waits_for_view(tmp_path):
    for automatic in (True,False):
        root=publication_root(tmp_path/str(automatic))
        started,release=threading.Event(),threading.Event()
        acknowledgements=[]
        class Client(Telegram):
            async def info(self,*args):
                return dict(peer=-1001234567890,username='fixture',title='Channel',kind='channel',forum=False)
            async def page(self,*args):
                return dict(messages=[dict(id=11,date=time.time()-60,text='Saved before read',group=None,
                    media=dict(kind='image',extension='jpg',mime='image/jpeg'))],more=False,gap=None)
            async def messages(self,*args):
                return (await self.page())['messages']
            async def download(self,*args):
                started.set()
                while not release.is_set():
                    await asyncio.sleep(.01)
                return b'native-image'
            async def read_state(self,*args):
                return dict(read_max=acknowledgements[-1] if acknowledgements else 10,initial=0)
            async def read_window(self,account,peer,after,until):
                return dict(ids=[n for n in (11,) if after<n<=until],more=False)
            async def acknowledge(self,account,peer,max_id):
                acknowledgements.append(max_id)
        with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client()),headers=HEADERS) as client:
            try:
                presence(client,'reader')
                client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
                connected=connect_request(client,json=dict(account='fixture',peer='@fixture',auto_read=automatic,initial_history={'mode':'count','count':1}))
                assert connected.status_code==200,connected.text
                assert started.wait(3)
                assert acknowledgements==[]
                card=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))
                release.set()
                wait_for(client,lambda:bool(client.get('/api/v3/materials/'+card['id']).json()['content']['attachments']))
                if not automatic:
                    time.sleep(.1)
                    assert acknowledgements==[]
                    assert client.post('/api/v3/materials/'+card['id']+'/viewed').status_code==200
                wait_for(client,lambda:acknowledgements==[11])
                read=next(row for row in client.get('/api/v3/status').json()['telegram_reads'] if row['feed_id']==connected.json()['feed_id'])
                assert read['initial']=='10'
            finally:
                release.set()


def test_changing_native_profile_does_not_reuse_other_accounts_read_frontier(tmp_path):
    root=publication_root(tmp_path)
    acknowledgements=[]
    class Client(Telegram):
        async def identity(self,account):
            return {'one':10001,'two':20002}[account]
        async def accounts(self):
            return ['one','two']
        async def info(self,*args):
            return dict(peer=-1001234567890,username='fixture',title='Channel',kind='channel',forum=False)
        async def messages(self,*args):
            return (await self.page('',0,0,0))['messages']
        async def read_state(self,account,*args):
            return dict(read_max=max([n for name,n in acknowledgements if name==account],default=0),initial=10)
        async def read_window(self,account,peer,after,until):
            return dict(ids=[n for n in (11,) if after<n<=until],more=False)
        async def acknowledge(self,account,peer,max_id):
            acknowledgements.append((account,max_id))
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client()),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='one',peer='@fixture',auto_read=True,initial_history={'mode':'count','count':1}))
        assert connected.status_code==200,connected.text
        wait_for(client,lambda:('one',11) in acknowledgements)
        assert client.put('/api/v3/telegram/profiles/collection',json={'configuration_ref':'two'}).status_code==200
        changed=client.post('/api/v3/telegram/connect',json=dict(feed_id=connected.json()['feed_id'],source_locator='@fixture',folder='Keep',access={'kind':'client','profile':'collection'}))
        assert changed.status_code==200,changed.text
        wait_for(client,lambda:('two',11) in acknowledgements)
        assert acknowledgements==[('one',11),('two',11)]


def test_group_cannot_be_connected_before_new_pagination(tmp_path):
    root=publication_root(tmp_path)
    class Client(Telegram):
        async def info(self,*args):
            return dict(peer=-1001234567890,username='fixture',title='Group',kind=kind,forum=forum)
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client()),headers=HEADERS) as client:
        presence(client,'reader')
        for kind,forum in [('group',False),('group',True),('channel',True)]:
            connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
            assert connected.status_code==400,connected.text
            assert connected.json()['code']=='source_connection_invalid'
            assert connected.json()['details']['reason']=='unsupported_group'
            assert client.get('/api/v3/subscriptions').json()['feeds']==[]


def test_true_feed_id_conflict_refuses_switch_without_merging_or_overwriting(tmp_path):
    import httpx
    from tests.support.telegram_web import page
    from tests.support.telegram import configure_fast_web,settled
    root=publication_root(tmp_path)
    alias=['other']
    class Client(Telegram):
        async def info(self,account,peer):
            if peer=='@fixture':
                alias[0]='fixture'
            return dict(peer=-1001234567890,username=alias[0],title='Native channel',kind='channel',forum=False)
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client(),
            rss_transport=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Web copy')])))),headers=HEADERS) as client:
        presence(client,'reader')
        configure_fast_web(client)
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        native=connect_request(client,json=dict(account='fixture',peer='@other',initial_history={'mode':'count','count':1}))
        assert native.status_code==200,native.text
        web=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Web',initial_history={'mode':'count','count':'1'}))
        assert web.status_code==200,web.text
        settled(client,2)
        before={card['id'] for card in read_all_cards(client, '/api/v3/cards')}
        changed=client.post('/api/v3/telegram/connect',json=dict(feed_id=web.json()['feed_id'],source_locator='@fixture',folder='Keep',access={'kind':'client','profile':'personal'}))
        assert changed.status_code==409 and changed.json()['code']=='source_identity_conflict'
        assert {card['id'] for card in read_all_cards(client, '/api/v3/cards')}==before
        catalog=client.get('/api/v3/subscriptions').json()
        assert len(catalog['feeds'])==2 and next(f for f in catalog['feeds'] if f['id']==web.json()['feed_id'])['access']['kind']=='web'


def test_unsupported_native_media_keeps_text_and_reports_incompleteness(tmp_path):
    root=publication_root(tmp_path)
    class Client(Telegram):
        async def page(self,*args):
            return dict(messages=[dict(id=11,date=time.time()-60,text='Readable native text',group=None,
                media=dict(kind='unsupported',extension='bin',mime='application/octet-stream'))],more=False,gap=None)
        async def download(self,*args):
            raise AssertionError('Unsupported resource must not be downloaded')
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client()),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        result=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        assert result.status_code==200,result.text
        card=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))
        content=client.get('/api/v3/materials/'+card['id']).json()['content']
        assert content['text']=='Readable native text'
        assert content['completeness']['state']=='incomplete'
        assert 'Тип вложения пока не поддерживается' in content['completeness']['message']


def test_native_peer_proof_handles_known_rename_and_keeps_publication_after_restart(tmp_path):
    root=publication_root(tmp_path)
    renamed=[False]
    class Client(Telegram):
        async def info(self,*args):
            return dict(peer=-1001234567890,username='renamed' if renamed[0] else 'fixture',title='Channel',kind='channel',forum=False)
        async def page(self,*args):
            return dict(messages=[dict(id=11,date=time.time()-60,text='New native text' if renamed[0] else 'Old native text',group=None,media=None)],more=False,gap=None)
        async def messages(self,*args):
            return (await self.page())['messages']
    options=dict(token='test',api_only=True,telegram_client=Client())
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        assert connected.status_code==200,connected.text
        item=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))['id']
        renamed[0]=True
        assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
        wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['content']['text']=='New native text')
        value=client.get('/api/v3/materials/'+item).json()
        assert value['source_url']=='https://t.me/renamed/11'
        assert len(read_all_cards(client, '/api/v3/cards'))==1
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
        wait_for(client,lambda:not client.get('/api/v3/materials/'+item).json()['content']['loading'])
        assert client.get('/api/v3/materials/'+item).json()['content']['text']=='New native text'



def test_native_album_growth_keeps_media_members_and_card_through_retry_and_collect(tmp_path):
    root=publication_root(tmp_path)
    parts=[dict(id=11,date=time.time()-60,text='First album part',group=77,
        media=dict(kind='image',extension='jpg',mime='image/jpeg'))]
    class Client(Telegram):
        async def info(self,*args):
            return dict(peer=-1001234567890,username='fixture',title='Channel',kind='channel',forum=False)
        async def page(self,*args):
            return dict(messages=parts,more=False,gap=None)
        async def messages(self,*args):
            return parts
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client()),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        assert connected.status_code==200,connected.text
        card=wait_for(client,lambda:next((c for c in read_all_cards(client, '/api/v3/cards') if len(client.get('/api/v3/materials/'+c['id']).json()['content'].get('attachments',[]))==1),None))
        item=card['id']
        parts.append(dict(id=12,date=time.time()-60,text='Second album part',group=77,
            media=dict(kind='video',extension='mp4',mime='video/mp4')))
        assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
        value=wait_for(client,lambda:m if len((m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'])==2 else None)
        assert value['content']['completeness']['state']=='complete'
        assert value['source']['message_ids']==['11','12']
        assert 'First album part' in value['content']['text'] and 'Second album part' in value['content']['text']
        assert [c['id'] for c in read_all_cards(client, '/api/v3/cards')]==[item]
        parts.append(dict(id=13,date=time.time()-60,text='Third album part',group=77,media=None))
        assert client.post('/api/v3/collect').status_code==200
        value=wait_for(client,lambda:m if 'Third album part' in (m:=client.get('/api/v3/materials/'+item).json())['content']['text'] else None)
        assert value['source']['message_ids']==['11','12','13']
        assert value['content']['completeness']['state']=='complete' and len(value['content']['attachments'])==2
        assert [c['id'] for c in read_all_cards(client, '/api/v3/cards')]==[item]


def test_native_media_change_refreshes_description_and_retries_same_file_after_backoff(tmp_path):
    from oko.collection.contracts import SourceError
    root=publication_root(tmp_path)
    tick,wall=[100.],[1_900_000_000.]
    calls=[]
    class Client(Telegram):
        async def page(self,*args):
            return dict(messages=[dict(id=11,date=wall[0]-60,text='Native text',group=None,
                media=dict(kind='image',extension='jpg',mime='image/jpeg',identity='photo:1'))],more=False,gap=None)
        async def messages(self,*args):
            return (await self.page())['messages']
        async def download(self,*args):
            calls.append(True)
            if len(calls)==1:
                error=SourceError('Refresh native file reference')
                error.remote_type='ChangedMedia'
                raise error
            return b'fresh-file'
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client(),clock=lambda:tick[0],wall_clock=lambda:wall[0],lease=10000,grace=10000),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        assert connected.status_code==200,connected.text
        def attempted():
            if calls:
                return True
            tick[0]+=1
            return False
        wait_for(client,attempted)
        with database(root) as db:
            identity=db.execute('SELECT id FROM media_files').fetchone()[0]
        def refreshed():
            with database(root) as db:
                row=db.execute("SELECT m . status , j . status , j . retry_delay FROM media_files m JOIN jobs j ON j . item = m . id AND j . kind = 'download_media' WHERE m . id = %s",(identity,)).fetchone()
                return row and row[0]=='pending_download' and row[1]=='pending' and row[2]>=60
        wait_for(client,refreshed)
        tick[0]+=60
        wall[0]+=60
        item=read_all_cards(client, '/api/v3/cards')[0]['id']
        ready=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
        assert client.get(ready['content']['attachments'][0]['url']).content==b'fresh-file'
        assert ready['content']['attachments'][0]['url'].endswith('/'+identity) and len(calls)==2


def test_native_account_wait_does_not_hold_separate_telegraph_articles(tmp_path):
    import httpx
    from oko.collection.contracts import SourceError
    root=publication_root(tmp_path)
    paused=threading.Event()
    calls=[]
    class Client(Telegram):
        async def page(self,*args):
            return dict(messages=[dict(id=11,date=time.time()-60,text='Own [First](https://telegra.ph/NativeFirst) [Second](https://telegra.ph/NativeSecond)',group=None,
                media=dict(kind='image',extension='jpg',mime='image/jpeg'))],more=False,gap=None)
        async def messages(self,*args):
            return (await self.page())['messages']
        async def download(self,*args):
            calls.append(True)
            paused.set()
            error=SourceError('Native account is paused',900)
            error.remote_type='FloodWaitError'
            raise error
    async def response(request):
        assert request.url.host=='api.telegra.ph'
        if request.url.path.endswith('NativeFirst'):
            while not paused.is_set():
                await asyncio.sleep(.01)
        text='Second independent article' if request.url.path.endswith('NativeSecond') else 'First independent article'
        return httpx.Response(200,json={'ok':True,'result':{'title':text,'content':[{'tag':'p','children':[text]}]}})
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client(),rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        assert connected.status_code==200,connected.text
        assert paused.wait(3)
        card=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))
        wait_for(client,lambda:'Second independent article' in client.get('/api/v3/materials/'+card['id']).json()['content']['text'])
        assert len(calls)==1




def test_partial_native_album_reload_keeps_saved_content_until_members_return(tmp_path):
    root=publication_root(tmp_path)
    partial=[False]
    class Client(Telegram):
        async def page(self,*args):
            rows=[dict(id=11,date=time.time()-60,text='First saved part',group=77,media=None)]
            if not partial[0]:
                rows.append(dict(id=12,date=time.time()-60,text='Second saved part',group=77,media=None))
            return dict(messages=rows,more=False,gap=None)
        async def messages(self,*args):
            return (await self.page())['messages']
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client()),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        assert connected.status_code==200,connected.text
        item=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))['id']
        partial[0]=True
        client.post('/api/v3/materials/'+item+'/retry')
        wait_for(client,lambda:any('Часть альбома недоступна' in problem['message'] for problem in client.get('/api/v3/status').json()['problems']['current']))
        value=client.get('/api/v3/materials/'+item).json()
        assert value['content']['text']=='First saved part\n\nSecond saved part'
        assert value['source']['message_ids']==['11','12']




def test_native_auto_read_waits_for_linked_article_image(tmp_path):
    import httpx
    from tests.support.youtube import PNG
    root=publication_root(tmp_path)
    started,release=threading.Event(),threading.Event()
    acknowledgements=[]
    class Client(Telegram):
        async def page(self,*args):
            return dict(messages=[dict(id=11,date=time.time()-60,text='Own [Article](https://telegra.ph/ReadArticle)',group=None,media=None)],more=False,gap=None)
        async def messages(self,*args):
            return (await self.page())['messages']
        async def read_state(self,*args):
            return dict(read_max=acknowledgements[-1] if acknowledgements else 10,initial=0)
        async def read_window(self,account,peer,after,until):
            return dict(ids=[n for n in (11,) if after<n<=until],more=False)
        async def acknowledge(self,account,peer,max_id):
            acknowledgements.append(max_id)
    async def response(request):
        if request.url.host=='files.example':
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
        return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[{'tag':'p','children':['Article body']},{'tag':'img','attrs':{'src':'https://files.example/photo.png'}}]}})
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client(),rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        try:
            presence(client,'reader')
            client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
            connected=connect_request(client,json=dict(account='fixture',peer='@fixture',auto_read=True,initial_history={'mode':'count','count':1}))
            assert connected.status_code==200,connected.text
            assert started.wait(3)
            card=wait_for(client,lambda:next(iter(read_all_cards(client, '/api/v3/cards')),None))
            wait_for(client,lambda:'Article body' in client.get('/api/v3/materials/'+card['id']).json()['content']['text'])
            assert acknowledgements==[]
            release.set()
            wait_for(client,lambda:acknowledgements==[11])
        finally:
            release.set()
