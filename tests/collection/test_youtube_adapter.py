"""The common YouTube adapter describes external video and independent files."""
import asyncio
from io import BytesIO

import httpx
import pytest

from oko.collection.adapters import AdapterException,AdapterErrorKind,Platform
from oko.collection.youtube.adapter import YouTubeAdapter
from tests.support.youtube import CHANNEL,VIDEO,PNG,feed_xml


class Captions:
    def __init__(self,text='Raw transcript'):
        self.text=text
        self.calls=[]

    async def fetch(self,video):
        self.calls.append(video)
        return self.text


def test_youtube_discovery_describes_video_cover_and_subtitles_without_downloading():
    calls=[]
    captions=Captions()
    def respond(request):
        calls.append(str(request.url))
        assert request.url.path=='/feeds/videos.xml'
        return httpx.Response(200,text=feed_xml())
    adapter=YouTubeAdapter(captions,httpx.MockTransport(respond))
    feed=asyncio.run(adapter.resolve_feed('https://www.youtube.com/feeds/videos.xml?channel_id='+CHANNEL))
    page=asyncio.run(adapter.discover(feed.reference,feed.latest_cursor,10))
    value,=page.publications
    assert feed.reference.key.platform==Platform.youtube and feed.reference.key.external_id==CHANNEL
    assert value.metadata.reference.key.platform==Platform.youtube and value.metadata.reference.key.external_id==VIDEO
    assert value.metadata.reference.url=='https://www.youtube.com/watch?v='+VIDEO
    assert not value.needs_fetch and not value.linked_publications
    blocks=value.content['blocks']
    assert blocks[0]=={'type':'external_media','kind':'youtube','url':'https://www.youtube.com/watch?v='+VIDEO}
    assert [block['role'] for block in blocks if block['type']=='media']==['cover','subtitles']
    assert len(value.media)==2 and captions.calls==[]
    assert all('/feeds/videos.xml?' in url for url in calls)
    assert asyncio.run(adapter.fetch(value.metadata.reference)).metadata.title==value.metadata.title


def test_youtube_files_stream_independently_and_missing_subtitles_are_notice():
    captions=Captions('Оригинальные субтитры')
    calls=[]
    def respond(request):
        calls.append(str(request.url))
        assert request.url.host=='i.ytimg.com'
        return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    adapter=YouTubeAdapter(captions,httpx.MockTransport(respond))
    from oko.collection.youtube.adapter import video_reference
    ref=video_reference(VIDEO)
    value=asyncio.run(adapter.fetch(ref))
    cover,subtitles=value.media
    caption_output=BytesIO()
    actual=asyncio.run(adapter.download_media(ref,subtitles,caption_output))
    assert caption_output.getvalue()=='Оригинальные субтитры'.encode('utf-8')
    assert actual.mime_type=='text/plain' and actual.size_bytes==len(caption_output.getvalue())
    assert calls==[] and captions.calls==[VIDEO]
    cover_output=BytesIO()
    asyncio.run(adapter.download_media(ref,cover,cover_output))
    assert cover_output.getvalue()==PNG
    assert calls==['https://i.ytimg.com/vi/'+VIDEO+'/hqdefault.jpg'] and captions.calls==[VIDEO]
    captions.text=None
    with pytest.raises(AdapterException) as caught:
        asyncio.run(adapter.download_media(ref,subtitles,BytesIO()))
    assert caught.value.issue.kind==AdapterErrorKind.notice and caught.value.issue.code=='subtitles-unavailable'


def test_youtube_history_keeps_upper_boundary_and_titles_after_restart():
    from datetime import datetime,timezone
    def ident(n):
        return f'v{n:010d}'
    videos=[ident(n) for n in (9,7,5,3)]
    class History:
        async def fetch(self,channel,count,state):
            assert channel==CHANNEL
            upper=state.get('anchors',{}).get('UU',videos[0])
            chosen=videos[videos.index(upper):][:count]
            return dict(entries=[dict(id=video,title='Video '+video) for video in chosen],
                        state=state,complete=True,error=None,requests=3)
    def respond(request):
        rows=''.join('<entry><id>yt:video:'+video+'</id><title>Video '+video+'</title><published>'+datetime(2026,10,int(video[1:]),tzinfo=timezone.utc).isoformat()+'</published><link href="https://www.youtube.com/watch?v='+video+'"/></entry>' for video in videos if video in (ident(11),ident(9),ident(7)))
        return httpx.Response(200,text='<feed xmlns="http://www.w3.org/2005/Atom">'+rows+'</feed>')
    transport=httpx.MockTransport(respond)
    adapter=YouTubeAdapter(Captions(),transport,History())
    feed=asyncio.run(adapter.resolve_feed('https://www.youtube.com/feeds/videos.xml?channel_id='+CHANNEL))
    videos.insert(0,ident(11))
    start=asyncio.run(adapter.cursor_before(feed.reference,feed.latest_cursor,3))
    first=asyncio.run(adapter.discover(feed.reference,start,1))
    assert [p.metadata.reference.key.external_id for p in first.publications]==[ident(3)]
    assert first.has_more
    adapter=YouTubeAdapter(Captions(),transport,History())
    rest=asyncio.run(adapter.discover(feed.reference,first.next_cursor,10))
    assert [p.metadata.reference.key.external_id for p in rest.publications]==[ident(5),ident(7),ident(9)]
    assert rest.publications[0].metadata.title=='Video '+ident(5)
    assert not rest.has_more
    following=asyncio.run(adapter.discover(feed.reference,rest.next_cursor,10))
    assert [p.metadata.reference.key.external_id for p in following.publications]==[ident(7),ident(9),ident(11)]


def test_youtube_partial_history_keeps_provider_progress_and_waits_before_delivery():
    def ident(n):
        return f'v{n:010d}'
    class History:
        def __init__(self):
            self.calls=0
        async def fetch(self,channel,count,state):
            self.calls+=1
            if self.calls>1:
                assert state['done']==['UULF']
            entries_=[dict(id=ident(n),title=str(n)) for n in (9,7,5,3)]
            return dict(entries=entries_ if self.calls==3 else entries_[:2],complete=self.calls==3,error=None,
                        state={**state,'done':['UULF'],'lists':{'UULF':[{'id':ident(9)}]}},requests=8)
    history=History()
    raw='<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>yt:video:'+ident(9)+'</id><link href="https://www.youtube.com/watch?v='+ident(9)+'"/></entry></feed>'
    adapter=YouTubeAdapter(Captions(),httpx.MockTransport(lambda _:httpx.Response(200,text=raw)),history)
    feed=asyncio.run(adapter.resolve_feed('https://www.youtube.com/feeds/videos.xml?channel_id='+CHANNEL))
    start=asyncio.run(adapter.cursor_before(feed.reference,feed.latest_cursor,3))
    pending=asyncio.run(adapter.discover(feed.reference,start,2))
    assert pending.publications==() and pending.has_more
    ready=asyncio.run(adapter.discover(feed.reference,pending.next_cursor,2))
    assert [p.metadata.reference.key.external_id for p in ready.publications]==[ident(3),ident(5)]


def test_cover_rejects_non_image_and_oversize_without_accepting_partial_bytes():
    from oko.collection.youtube.adapter import video_reference
    for raw,mime in [(b'<html>blocked</html>','image/jpeg'),(PNG+b'x'*2_000_000,'image/png')]:
        adapter=YouTubeAdapter(Captions(),httpx.MockTransport(lambda _:httpx.Response(200,content=raw,headers={'content-type':mime})))
        ref=video_reference(VIDEO)
        cover=asyncio.run(adapter.fetch(ref)).media[0]
        output=BytesIO()
        with pytest.raises(AdapterException):
            asyncio.run(adapter.download_media(ref,cover,output))
        assert output.getvalue()==b''


def test_paused_history_never_advances_and_cancel_opens_next_normal_window():
    import json
    from oko.collection.youtube.compatibility import YouTubeAdapterSource
    calls=[]
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200,text=feed_xml())
    source=YouTubeAdapterSource('https://www.youtube.com/feeds/videos.xml?channel_id='+CHANNEL,0,Captions(),httpx.MockTransport(respond))
    source.initial_history=dict(count=10,run='one',paused=True)
    paused=asyncio.run(source.discover(None))
    assert paused.publications==() and calls==[]
    source.initial_history=dict(count=10,run='one',cancelled=True)
    cancelled=asyncio.run(source.discover(paused.cursor))
    assert json.loads(cancelled.cursor)['initial_complete'] and calls==[]
    source.initial_history=dict(count=10,run='one')
    following=asyncio.run(source.discover(cancelled.cursor))
    assert [p.id for p in following.publications]==[VIDEO]
