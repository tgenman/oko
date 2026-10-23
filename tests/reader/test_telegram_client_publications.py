"""Native channel publications through actual reader HTTP and temporary storage."""
import asyncio
import json
import sqlite3
import threading
import time

from tests.reader.test_independent_media import migrated_root
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
