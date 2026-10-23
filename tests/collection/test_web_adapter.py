"""Feedless web publications use the shared external adapter contract."""
import asyncio
from io import BytesIO

import httpx
import pytest

from oko.collection.adapters import AdapterException,AdapterErrorKind,Platform
from oko.collection.web_adapter import WebAdapter,reference


def test_web_keeps_real_links_and_separate_media_without_following_links():
    calls=[]
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200,text='<title>Title</title><nav>Ignore menu</nav><article><p>Before <a href="/other?q=1">link</a></p><img src="/image.png"><p>After</p></article>')
    adapter=WebAdapter(httpx.MockTransport(respond))
    ref=reference('HTTPS://WEB.EXAMPLE/post?q=1#fragment')
    value=asyncio.run(adapter.fetch(ref))
    assert ref.key.platform==Platform.other_web and ref.key.external_id=='https://web.example/post?q=1'
    assert value.metadata.reference==ref and value.metadata.title=='Title'
    assert not value.needs_fetch and value.linked_publications==()
    blocks=value.content['blocks']
    assert [b['type'] for b in blocks]==['text','media','text']
    assert blocks[0]['text']=='Before [link](https://web.example/other?q=1)'
    assert blocks[2]['text']=='After'
    assert blocks[1]['ref']=={'$media':value.media[0].key}
    assert value.media[0].locator=='https://web.example/image.png'
    assert calls==['https://web.example/post?q=1']


def test_telegraph_is_feedless_and_its_links_do_not_start_another_fetch():
    calls=[]
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[{'tag':'p','children':['Own text']},{'tag':'a','attrs':{'href':'https://telegra.ph/Other'},'children':['Another']}]}})
    value=asyncio.run(WebAdapter(httpx.MockTransport(respond)).fetch(reference('https://telegra.ph/Fixture')))
    assert value.metadata.reference.key.platform==Platform.telegraph
    assert value.linked_publications==()
    assert len(calls)==1 and calls[0].startswith('https://api.telegra.ph/getPage/Fixture?')
    assert 'https://telegra.ph/Other' in value.content['blocks'][0]['text']


def test_web_media_stream_and_changed_locator():
    current=['a']
    def respond(request):
        if request.url.path.endswith('.png'):
            return httpx.Response(200,content=b'image',headers={'content-type':'image/png','content-disposition':'attachment; filename=original.png'})
        return httpx.Response(200,text='<article><p>Text</p><img src="/'+current[0]+'.png"></article>')
    adapter=WebAdapter(httpx.MockTransport(respond))
    ref=reference('https://web.example/post')
    value=asyncio.run(adapter.fetch(ref))
    output=BytesIO()
    actual=asyncio.run(adapter.download_media(ref,value.media[0],output))
    assert output.getvalue()==b'image' and actual.size_bytes==5
    assert actual.mime_type=='image/png' and actual.name=='original.png'
    current[0]='b'
    with pytest.raises(AdapterException) as caught:
        asyncio.run(adapter.download_media(ref,value.media[0],BytesIO()))
    assert caught.value.issue.kind==AdapterErrorKind.media_changed


def test_web_missing_article_has_a_typed_unavailable_error():
    adapter=WebAdapter(httpx.MockTransport(lambda _:httpx.Response(404)))
    with pytest.raises(AdapterException) as caught:
        asyncio.run(adapter.fetch(reference('https://web.example/missing')))
    assert caught.value.issue.kind==AdapterErrorKind.unavailable


def test_web_failure_describes_article_and_keeps_source_wait_metadata():
    adapter=WebAdapter(httpx.MockTransport(lambda _:httpx.Response(429,headers={'retry-after':'120'})))
    with pytest.raises(AdapterException) as caught:
        asyncio.run(adapter.fetch(reference('https://web.example/limited')))
    issue=caught.value.issue
    assert issue.kind==AdapterErrorKind.source_wait and issue.retry_at is not None
    assert issue.scope=='web:web.example' and issue.message=='Не удалось загрузить статью'


def test_telegraph_key_uses_its_page_path_across_source_tracking_links():
    assert reference('https://www.telegra.ph/Fixture?utm_source=rss#section')==reference('https://telegra.ph/Fixture')
    assert reference('https://telegra.ph/Статья').key==reference('https://telegra.ph/%D0%A1%D1%82%D0%B0%D1%82%D1%8C%D1%8F').key
