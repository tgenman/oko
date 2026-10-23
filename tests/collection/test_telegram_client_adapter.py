"""Channel metadata and individual files through the external adapter contract."""
import asyncio
from io import BytesIO
import time

from tests.support.telegram import Telegram
from oko.collection.adapters import Platform


def test_channel_adapter_keeps_public_album_identity_and_defers_file_download():
    from oko.collection.telegram.client_adapter import TelegramClientAdapter
    calls=[]
    class Client(Telegram):
        async def info(self,account,peer):
            assert account=='fixture'
            return dict(peer=-1001234567890,title='Channel',description='About',kind='channel',forum=False,username='fixture')
        async def page(self,account,peer,after,since):
            return dict(messages=[dict(id=n,date=time.time()-60,text='Album' if n==11 else '',group=77,
                media=dict(kind='image',extension='jpg',mime='image/jpeg',id=str(n),size=3)) for n in (12,11)],more=False,gap=None)
        async def messages(self,account,peer,ids):
            return (await self.page(account,peer,0,0))['messages'] if set(ids)&{11,12} else []
        async def download(self,account,peer,id):
            calls.append(id)
            return b'img'
    adapter=TelegramClientAdapter(Client(),'fixture',-1001234567890)
    feed=asyncio.run(adapter.resolve_feed('https://t.me/s/fixture'))
    assert feed.reference.key.platform==Platform.telegram
    assert feed.reference.key.external_id=='-1001234567890'
    assert feed.title=='Channel' and feed.description=='About'
    page=asyncio.run(adapter.discover(feed.reference,feed.latest_cursor,10))
    publication,=page.publications
    assert publication.metadata.reference.key.external_id=='https://t.me/fixture/11'
    assert publication.content['extra']['telegram']['members']==[11,12]
    assert publication.content['blocks'][0]==dict(type='text',role='body',text='Album')
    assert len(publication.media)==2 and calls==[]
    assert all('media_id' not in block for block in publication.content['blocks'])
    refreshed=asyncio.run(adapter.fetch(publication.metadata.reference))
    assert refreshed.metadata.reference==publication.metadata.reference
    output=BytesIO()
    actual=asyncio.run(adapter.download_media(publication.metadata.reference,publication.media[0],output))
    assert output.getvalue()==b'img' and len(calls)==1 and actual.size_bytes==3
    second=BytesIO()
    asyncio.run(adapter.download_media(publication.metadata.reference,publication.media[1],second))
    assert second.getvalue()==b'img' and calls==[11,12]


def test_client_rejects_publication_from_another_channel_before_reading_messages():
    import pytest
    from oko.collection.adapters import AdapterException,ExternalKey,ExternalReference
    from oko.collection.telegram.client_adapter import TelegramClientAdapter
    class Client(Telegram):
        async def info(self,account,peer):
            return dict(peer=-1001234567890,title='Channel',kind='channel',username='fixture')
        async def messages(self,*args):
            raise AssertionError('Foreign publication must not read messages')
    adapter=TelegramClientAdapter(Client(),'fixture',-1001234567890)
    ref=ExternalReference(ExternalKey(Platform.telegram,'https://t.me/othername/11'),'https://t.me/othername/11')
    with pytest.raises(AdapterException):
        asyncio.run(adapter.fetch(ref))


def test_client_history_counts_publications_including_albums_and_keeps_upper_boundary():
    from oko.collection.telegram.client_adapter import TelegramClientAdapter
    newest=[20]
    class Client(Telegram):
        async def info(self,account,peer):
            return dict(peer=-1001234567890,title='Private',kind='channel')
        async def page(self,account,peer,after,since):
            return dict(messages=[dict(id=n,date=time.time()-60,text=str(n),group=77 if n in (10,11) else None,media=None)
                for n in [*newest,11,10,3]],more=False,gap=None)
    adapter=TelegramClientAdapter(Client(),'fixture',-1001234567890)
    feed=asyncio.run(adapter.resolve_feed('-1001234567890'))
    newest.insert(0,30)
    cursor=asyncio.run(adapter.cursor_before(feed.reference,feed.latest_cursor,1))
    page=asyncio.run(adapter.discover(feed.reference,cursor,1))
    assert [p.metadata.reference.url for p in page.publications]==['https://t.me/c/1234567890/10']
    assert page.has_more
    rest=asyncio.run(adapter.discover(feed.reference,page.next_cursor,10))
    assert [p.metadata.reference.url for p in rest.publications]==['https://t.me/c/1234567890/20']
    live=asyncio.run(adapter.discover(feed.reference,rest.next_cursor,10))
    assert [p.metadata.reference.url for p in live.publications]==['https://t.me/c/1234567890/30']


def test_empty_channel_reports_shortfall_without_fabricated_publication():
    from oko.collection.telegram.client_adapter import TelegramClientAdapter
    class Client(Telegram):
        async def page(self,*args):
            return dict(messages=[],more=False)
    adapter=TelegramClientAdapter(Client(),'fixture',-1001234567890)
    feed=asyncio.run(adapter.resolve_feed('-1001234567890'))
    cursor=asyncio.run(adapter.cursor_before(feed.reference,feed.latest_cursor,4))
    page=asyncio.run(adapter.discover(feed.reference,cursor,10))
    assert page.publications==() and not page.has_more
    assert [issue.code for issue in page.issues]==['history-shortfall']
