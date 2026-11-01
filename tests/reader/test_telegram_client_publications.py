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
            material=client.get('/api/v3/publication/'+card['id']).json()
            assert material['text']=='Native own text'
            with database(root) as db:
                assert db.execute('SELECT platform , external_id FROM publications WHERE id = %s',(card['id'],)).fetchone()==('telegram','https://t.me/fixture/11')
                assert db.execute('SELECT adapter FROM feeds WHERE id = %s',(card['feed_id'],)).fetchone()[0]=='telegram_client_adapter'
                content=json.loads(db.execute('SELECT content FROM publications WHERE id = %s',(card['id'],)).fetchone()[0])
                media_id=next(b['media_id'] for b in content['blocks'] if b['type']=='media')
                assert db.execute('SELECT status FROM media_files WHERE id = %s',(media_id,)).fetchone()[0]=='pending_download'
            release.set()
            ready=wait_for(client,lambda:(m if (m := client.get('/api/v3/publication/'+card['id']).json()) and any(media['role'] == 'attachment' for media in m['media']) else None))
            assert client.get([media for media in ready['media'] if media['role'] == 'attachment'][0]['url']).content==b'native-image'
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
        updated=wait_for(client,lambda:(m if (m:=client.get('/api/v3/publication/'+item).json())['text']=='New access text' else None))
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
            wait_for(client,lambda:client.get('/api/v3/publication/'+item).json()['text']=='New selected web text')
            release.set()
            time.sleep(.1)
            value=client.get('/api/v3/publication/'+item).json()
            assert value['text']=='New selected web text' and [media for media in value['media'] if media['role'] == 'attachment']==[]
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
                wait_for(client,lambda:bool([media for media in client.get('/api/v3/publication/'+card['id']).json()['media'] if media['role'] == 'attachment']))
                if not automatic:
                    time.sleep(.1)
                    assert acknowledgements==[]
                    assert client.post('/api/v3/materials/'+card['id']+'/viewed').status_code==200
                wait_for(client,lambda:acknowledgements==[11])
                read=next(row for row in client.get('/api/v3/status/details').json()['telegram_reads'] if row['feed_id']==connected.json()['feed_id'])
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
