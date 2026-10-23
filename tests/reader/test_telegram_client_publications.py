"""Native channel publications through actual reader HTTP and temporary storage."""
import asyncio
import json
import sqlite3
import threading
import time

from tests.support.publication_pipeline import migrated_root
from tests.support.app import create_app
from tests.support.reader import HEADERS,wait_for
from tests.support.presence import presence
from tests.support.telegram import Telegram,connect_request
from tests.support.v3 import TestClient


def test_channel_text_is_ready_before_independent_native_media(tmp_path):
    root=migrated_root(tmp_path)
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
            card=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))
            material=client.get('/api/v3/materials/'+card['id']).json()
            assert material['content']['text']=='Native own text'
            with sqlite3.connect(root/'state.sqlite') as db:
                assert db.execute('SELECT platform,external_id FROM items WHERE id=?',(card['id'],)).fetchone()==('telegram','https://t.me/fixture/11')
                assert db.execute('SELECT adapter FROM feeds WHERE id=?',(card['feed_id'],)).fetchone()[0]=='telegram_client_adapter'
                content=json.loads(db.execute('SELECT content FROM items WHERE id=?',(card['id'],)).fetchone()[0])
                media_id=next(b['media_id'] for b in content['blocks'] if b['type']=='media')
                assert db.execute('SELECT status FROM media_files WHERE id=?',(media_id,)).fetchone()[0]=='pending_download'
            release.set()
            ready=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+card['id']).json())['content']['attachments'] else None))
            assert client.get(ready['content']['attachments'][0]['url']).content==b'native-image'
        finally:
            release.set()


def test_native_media_flood_wait_blocks_only_its_account_and_survives_restart(tmp_path):
    from oko.collection.contracts import SourceError
    root=migrated_root(tmp_path)
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
        assert len(client.get('/api/v3/cards').json())==2


def test_offline_copy_migrates_proven_channel_latest_files_and_keeps_source(tmp_path):
    from oko.storage.migration import migrate_publications
    from tests.reader.test_publication_migration import snapshot
    from tests.support.telegram import settled
    class Client(Telegram):
        async def info(self,account,peer):
            return dict(peer=-1001234567890,username='fixture',title='Channel',kind='channel',forum=False)
        async def page(self,account,peer,after,since):
            return dict(messages=[dict(id=11,date=time.time()-60,text='Native latest',group=None,
                media=dict(kind='image',extension='jpg',mime='image/jpeg'))],more=False,gap=None)
        async def messages(self,account,peer,ids):
            return (await self.page(account,peer,0,0))['messages']
        async def download(self,*args):
            return b'native-image'
    source,destination=tmp_path/'legacy',tmp_path/'migration'
    options=dict(token='test',api_only=True,telegram_client=Client())
    with TestClient(create_app(source,**options),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        result=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        assert result.status_code==200,result.text
        item=settled(client)[0]['id']
    before=snapshot(source)
    report=migrate_publications(source,destination)
    assert report['success'],report
    assert snapshot(source)==before==snapshot(destination/'backup')
    assert any(row['old_id']==item and row['platform']=='telegram' for row in report['publications'])
    with sqlite3.connect(destination/'storage'/'state.sqlite') as db:
        assert db.execute('SELECT platform,external_id FROM items WHERE id=?',(item,)).fetchone()==('telegram','https://t.me/fixture/11')
    with TestClient(create_app(destination/'storage',**options),headers=HEADERS) as client:
        value=client.get('/api/v3/materials/'+item).json()
        assert value['content']['text']=='Native latest'
        assert value['source_url']=='https://t.me/fixture/11'
        assert client.get(value['content']['attachments'][0]['url']).content==b'native-image'
        assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
        settled(client)
        assert client.get('/api/v3/materials/'+item).json()['content']['text']=='Native latest'


def test_explicit_web_to_client_switch_keeps_feed_and_publication(tmp_path):
    import httpx
    from tests.support.telegram_web import page
    from tests.support.telegram import configure,settled,configure_fast_web
    root=migrated_root(tmp_path)
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
        assert updated['id']==item and len(client.get('/api/v3/cards').json())==1
        with sqlite3.connect(root/'state.sqlite') as db:
            assert db.execute('SELECT external_id,adapter FROM feeds WHERE id=?',(feed,)).fetchone()==('-1001234567890','telegram_client_adapter')
        catalog=client.get('/api/v3/subscriptions').json()
        assert next(f for f in catalog['folders'] if f['title']=='Original folder')


def test_switch_rejects_late_native_file_and_keeps_new_web_content(tmp_path):
    import httpx
    from tests.support.telegram_web import page
    from tests.support.telegram import configure_fast_web
    root=migrated_root(tmp_path)
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
            item=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))['id']
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
        root=migrated_root(tmp_path/str(automatic))
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
                card=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))
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
    root=migrated_root(tmp_path)
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


def test_group_keeps_legacy_collection_and_cannot_enter_channel_switch(tmp_path):
    from tests.support.telegram import settled
    root=migrated_root(tmp_path)
    class Client(Telegram):
        async def info(self,*args):
            return dict(peer=-1001234567890,username='fixture',title='Group',kind='group',forum=False)
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client()),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        assert connected.status_code==200,connected.text
        item=settled(client)[0]['id']
        with sqlite3.connect(root/'state.sqlite') as db:
            assert db.execute('SELECT platform FROM items WHERE id=?',(item,)).fetchone()[0] is None
        changed=client.post('/api/v3/telegram/connect',json=dict(feed_id=connected.json()['feed_id'],source_locator='@fixture',folder='Keep',access={'kind':'web'}))
        assert changed.status_code==409 and changed.json()['code']=='access_immutable'
        assert client.get('/api/v3/materials/'+item).json()['content']['text']=='Текст канала'


def test_true_feed_id_conflict_refuses_switch_without_merging_or_overwriting(tmp_path):
    import httpx
    from tests.support.telegram_web import page
    from tests.support.telegram import configure_fast_web,settled
    root=migrated_root(tmp_path)
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
        before={card['id'] for card in client.get('/api/v3/cards').json()}
        changed=client.post('/api/v3/telegram/connect',json=dict(feed_id=web.json()['feed_id'],source_locator='@fixture',folder='Keep',access={'kind':'client','profile':'personal'}))
        assert changed.status_code==409 and changed.json()['code']=='source_identity_conflict'
        assert {card['id'] for card in client.get('/api/v3/cards').json()}==before
        catalog=client.get('/api/v3/subscriptions').json()
        assert len(catalog['feeds'])==2 and next(f for f in catalog['feeds'] if f['id']==web.json()['feed_id'])['access']['kind']=='web'


def test_unsupported_native_media_keeps_text_and_reports_incompleteness(tmp_path):
    root=migrated_root(tmp_path)
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
        card=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))
        content=client.get('/api/v3/materials/'+card['id']).json()['content']
        assert content['text']=='Readable native text'
        assert content['completeness']['state']=='incomplete'
        assert 'Тип вложения пока не поддерживается' in content['completeness']['message']


def test_native_peer_proof_handles_known_rename_and_keeps_publication_after_restart(tmp_path):
    root=migrated_root(tmp_path)
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
        item=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))['id']
        renamed[0]=True
        assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
        wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['content']['text']=='New native text')
        value=client.get('/api/v3/materials/'+item).json()
        assert value['source_url']=='https://t.me/renamed/11'
        assert len(client.get('/api/v3/cards').json())==1
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
        wait_for(client,lambda:not client.get('/api/v3/materials/'+item).json()['content']['loading'])
        assert client.get('/api/v3/materials/'+item).json()['content']['text']=='New native text'



def test_native_album_growth_updates_same_publication_and_its_members(tmp_path):
    root=migrated_root(tmp_path)
    expanded=[False]
    class Client(Telegram):
        async def info(self,*args):
            return dict(peer=-1001234567890,username='fixture',title='Channel',kind='channel',forum=False)
        async def page(self,*args):
            rows=[dict(id=11,date=time.time()-60,text='First album part',group=77,media=None)]
            if expanded[0]:
                rows.append(dict(id=12,date=time.time()-60,text='Second album part',group=77,media=None))
            return dict(messages=rows,more=False,gap=None)
        async def messages(self,*args):
            return (await self.page())['messages']
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=Client()),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        assert connected.status_code==200,connected.text
        item=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))['id']
        expanded[0]=True
        assert client.post('/api/v3/collect').status_code==200
        wait_for(client,lambda:'Second album part' in client.get('/api/v3/materials/'+item).json()['content']['text'])
        value=client.get('/api/v3/materials/'+item).json()
        assert value['source']['message_ids']==['11','12']
        assert [card['id'] for card in client.get('/api/v3/cards').json()]==[item]


def test_native_media_change_refreshes_description_and_retries_same_file_after_backoff(tmp_path):
    from oko.collection.contracts import SourceError
    root=migrated_root(tmp_path)
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
        with sqlite3.connect(root/'state.sqlite') as db:
            identity=db.execute('SELECT id FROM media_files').fetchone()[0]
        def refreshed():
            with sqlite3.connect(root/'state.sqlite') as db:
                row=db.execute("SELECT m.status,j.status,j.retry_delay FROM media_files m JOIN jobs j ON j.item=m.id AND j.kind='download_media' WHERE m.id=?",(identity,)).fetchone()
                return row and row[0]=='pending_download' and row[1]=='pending' and row[2]>=60
        wait_for(client,refreshed)
        tick[0]+=60
        wall[0]+=60
        item=client.get('/api/v3/cards').json()[0]['id']
        ready=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
        assert client.get(ready['content']['attachments'][0]['url']).content==b'fresh-file'
        assert ready['content']['attachments'][0]['url'].endswith('/'+identity) and len(calls)==2


def test_native_account_wait_does_not_hold_separate_telegraph_articles(tmp_path):
    import httpx
    from oko.collection.contracts import SourceError
    root=migrated_root(tmp_path)
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
        card=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))
        wait_for(client,lambda:'Second independent article' in client.get('/api/v3/materials/'+card['id']).json()['content']['text'])
        assert len(calls)==1


def test_offline_native_migration_preserves_accepted_results_and_reprocesses_new_input(tmp_path):
    from oko.storage.migration import migrate_publications
    from tests.support.models import model_transport
    text=['Latest native text']
    requests=[]
    class Client(Telegram):
        async def info(self,*args):
            return dict(peer=-1001234567890,username='fixture',title='Channel',kind='channel',forum=False)
        async def page(self,*args):
            return dict(messages=[dict(id=11,date=time.time()-60,text=text[0],group=None,media=None)],more=False,gap=None)
        async def messages(self,*args):
            return (await self.page())['messages']
    source,destination=tmp_path/'legacy',tmp_path/'migration'
    options=dict(token='test',api_only=True,telegram_client=Client(),ai_transport=model_transport(requests))
    with TestClient(create_app(source,**options),headers=HEADERS) as client:
        presence(client,'reader')
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        assert connected.status_code==200,connected.text
        card=wait_for(client,lambda:next((card for card in client.get('/api/v3/cards').json() if card['theses']['result']),None))
        item=card['id']
        before=client.get('/api/v3/materials/'+item).json()['theses']['result']
    count=sum(r.url.path=='/v1/chat/completions' for r in requests)
    report=migrate_publications(source,destination)
    assert report['success'] and any(row['old_id']==item and row['results']==['theses'] for row in report['publications'])
    with TestClient(create_app(destination/'storage',**options),headers=HEADERS) as client:
        current=client.get('/api/v3/materials/'+item).json()['theses']['result']
        assert [p['text'] for p in current['points']]==[p['text'] for p in before['points']]
        assert current['provenance']==before['provenance']
        assert all(ref['material_id']==item and ref['source_url']=='https://t.me/fixture/11' for p in current['points'] for ref in p['references'])
        assert sum(r.url.path=='/v1/chat/completions' for r in requests)==count
        presence(client,'reader')
        text[0]='Changed native input'
        client.post('/api/v3/materials/'+item+'/retry')
        wait_for(client,lambda:sum(r.url.path=='/v1/chat/completions' for r in requests)==count+1 and client.get('/api/v3/materials/'+item).json()['theses']['result'])
        latest=next(r for r in reversed(requests) if r.url.path=='/v1/chat/completions')
        task=json.loads(json.loads(latest.content)['messages'][1]['content'])
        assert task['sources'][0]['text']=='Changed native input'


def test_partial_native_album_reload_keeps_saved_content_until_members_return(tmp_path):
    root=migrated_root(tmp_path)
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
        item=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))['id']
        partial[0]=True
        client.post('/api/v3/materials/'+item+'/retry')
        wait_for(client,lambda:any('Часть альбома недоступна' in problem['message'] for problem in client.get('/api/v3/status').json()['problems']['current']))
        value=client.get('/api/v3/materials/'+item).json()
        assert value['content']['text']=='First saved part\n\nSecond saved part'
        assert value['source']['message_ids']==['11','12']


def test_native_migration_keeps_cached_telegraph_text_and_image_without_network(tmp_path):
    import httpx
    from oko.storage.migration import migrate_publications
    from tests.support.youtube import PNG
    from tests.support.telegram import settled
    class Client(Telegram):
        async def info(self,*args):
            return dict(peer=-1001234567890,username='fixture',title='Channel',kind='channel',forum=False)
        async def page(self,*args):
            return dict(messages=[dict(id=11,date=time.time()-60,text='Own [Article](https://telegra.ph/LegacyNative)',group=None,media=None)],more=False,gap=None)
        async def messages(self,*args):
            return (await self.page())['messages']
    def response(request):
        if request.url.host=='files.example':
            return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
        assert request.url.host=='api.telegra.ph'
        return httpx.Response(200,json={'ok':True,'result':{'title':'Cached article','content':[{'tag':'p','children':['Cached article body']},{'tag':'img','attrs':{'src':'https://files.example/cached.png'}}]}})
    source,destination=tmp_path/'legacy',tmp_path/'migration'
    with TestClient(create_app(source,token='test',api_only=True,telegram_client=Client(),rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connected=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        assert connected.status_code==200,connected.text
        item=settled(client)[0]['id']
        assert 'Cached article body' in client.get('/api/v3/materials/'+item).json()['content']['text']
    report=migrate_publications(source,destination)
    assert report['success'],report
    def offline(request):
        raise AssertionError('Reading the migrated cache must not fetch anything')
    with TestClient(create_app(destination/'storage',token='test',api_only=True,rss_transport=httpx.MockTransport(offline)),headers=HEADERS) as client:
        value=client.get('/api/v3/materials/'+item).json()
        assert 'Cached article body' in value['content']['text']
        attachment=value['content']['attachments'][0]
        assert client.get(attachment['url']).content==PNG
        with sqlite3.connect(destination/'storage'/'state.sqlite') as db:
            article=db.execute("SELECT id FROM items WHERE platform='telegraph'").fetchone()[0]
            assert db.execute('SELECT publication_id FROM media_files').fetchone()[0]==article


def test_native_auto_read_waits_for_linked_article_image(tmp_path):
    import httpx
    from tests.support.youtube import PNG
    root=migrated_root(tmp_path)
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
            card=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))
            wait_for(client,lambda:'Article body' in client.get('/api/v3/materials/'+card['id']).json()['content']['text'])
            assert acknowledgements==[]
            release.set()
            wait_for(client,lambda:acknowledgements==[11])
        finally:
            release.set()
