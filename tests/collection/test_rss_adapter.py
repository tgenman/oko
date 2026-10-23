"""RSS entries and their linked articles use separate external identities."""
import asyncio
import json

import httpx

from oko.collection.adapters import Platform
from oko.collection.rss_adapter import RSSAdapter


def test_rss_discovery_is_ready_and_links_a_separate_article_without_fetching_it():
    calls=[]
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200,text='<rss><channel><title>Feed</title><description>Description</description><item><guid>entry/1</guid><link>https://web.example/article</link><title>Announcement</title><description>Excerpt</description></item></channel></rss>')
    adapter=RSSAdapter(httpx.MockTransport(respond))
    feed=asyncio.run(adapter.resolve_feed('https://rss.example/feed'))
    page=asyncio.run(adapter.discover(feed.reference,feed.latest_cursor,10))
    assert feed.title=='Feed' and feed.description=='Description'
    assert feed.reference.key.platform==Platform.rss
    value,=page.publications
    assert value.metadata.reference.key.platform==Platform.rss
    assert json.loads(value.metadata.reference.key.external_id)==['https://rss.example/feed','entry/1']
    assert value.metadata.reference.url=='https://web.example/article'
    assert not value.needs_fetch and value.content['blocks'][0]['text']=='Excerpt'
    article,=value.linked_publications
    assert article.key.platform==Platform.other_web and article.key.external_id=='https://web.example/article'
    assert not page.has_more and set(calls)=={'https://rss.example/feed'}
    assert asyncio.run(adapter.fetch(value.metadata.reference))==value
    assert len(asyncio.run(adapter.discover(feed.reference,'',10)).publications)==1


def test_rss_identity_accepts_guid_without_url_and_reports_missing_key_and_collisions():
    from oko.collection.adapters import AdapterException, AdapterErrorKind
    import pytest
    raw=['<item><guid>only-id</guid><description>Text</description></item><item><title>No identity</title></item><item><link>https://web.example/fallback</link></item>']
    adapter=RSSAdapter(httpx.MockTransport(lambda _:httpx.Response(200,text='<rss><channel>'+raw[0]+'</channel></rss>')))
    feed=asyncio.run(adapter.resolve_feed('https://rss.example/feed'))
    start=asyncio.run(adapter.cursor_before(feed.reference,feed.latest_cursor,10))
    page=asyncio.run(adapter.discover(feed.reference,start,10))
    assert len(page.publications)==2
    first=page.publications[0]
    assert json.loads(first.metadata.reference.key.external_id)[1]=='https://web.example/fallback'
    second=page.publications[1]
    assert second.metadata.reference.url=='https://rss.example/feed' and second.linked_publications==()
    assert any(issue.code=='rss-missing-key' for issue in page.issues)
    assert any(issue.code=='history-shortfall' for issue in page.issues)
    raw[0]='<item><guid>same</guid><link>https://web.example/one</link></item><item><guid>same</guid><link>https://web.example/two</link></item>'
    with pytest.raises(AdapterException) as caught:
        asyncio.run(adapter.resolve_feed('https://rss.example/feed'))
    assert caught.value.issue.kind==AdapterErrorKind.permanent
    assert caught.value.issue.code=='rss-identity-conflict'


def test_rss_archive_window_is_inclusive_old_to_new_and_survives_restart():
    current=['3','2']
    def item(key):
        return '<item><guid>'+key+'</guid><link>https://web.example/'+key+'</link></item>'
    def respond(request):
        if request.url.path=='/archive':
            content=item('1')
        else:
            content=''.join(item(k) for k in current)+'<atom:link rel="next" href="/archive"/>'
        return httpx.Response(200,text='<rss xmlns:atom="http://www.w3.org/2005/Atom"><channel>'+content+'</channel></rss>')
    transport=httpx.MockTransport(respond)
    adapter=RSSAdapter(transport)
    feed=asyncio.run(adapter.resolve_feed('https://rss.example/feed'))
    start=asyncio.run(adapter.cursor_before(feed.reference,feed.latest_cursor,2))
    current.insert(0,'4')
    first=asyncio.run(adapter.discover(feed.reference,start,2))
    assert [json.loads(p.metadata.reference.key.external_id)[1] for p in first.publications]==['1','2']
    assert first.has_more
    adapter=RSSAdapter(transport)
    second=asyncio.run(adapter.discover(feed.reference,first.next_cursor,2))
    assert [json.loads(p.metadata.reference.key.external_id)[1] for p in second.publications]==['3']
    assert not second.has_more
    replay=asyncio.run(adapter.discover(feed.reference,second.next_cursor,10))
    assert [json.loads(p.metadata.reference.key.external_id)[1] for p in replay.publications]==['2','3','4']
    assert not replay.has_more


def test_rss_cursor_rejects_another_feed_and_invalid_external_reference():
    import pytest
    from oko.collection.adapters import AdapterException, ExternalKey, ExternalReference
    adapter=RSSAdapter(httpx.MockTransport(lambda _:httpx.Response(200,text='<rss><channel/></rss>')))
    feed=asyncio.run(adapter.resolve_feed('https://rss.example/feed'))
    other=ExternalReference(ExternalKey(Platform.rss,'https://rss.example/other'),'https://rss.example/other')
    with pytest.raises(AdapterException) as caught:
        asyncio.run(adapter.discover(other,feed.latest_cursor,10))
    assert caught.value.issue.code=='invalid-cursor'
    malformed=ExternalReference(ExternalKey(Platform.other_web,'[]'),'https://web.example/a')
    with pytest.raises(AdapterException) as caught:
        asyncio.run(adapter.fetch(malformed))
    assert caught.value.issue.code=='invalid-reference'


def test_rss_enclosures_are_individual_media_and_are_downloaded_only_on_request():
    from io import BytesIO
    calls=[]
    def respond(request):
        calls.append(str(request.url))
        if request.url.path=='/file.pdf':
            return httpx.Response(200,content=b'pdf',headers={'content-type':'application/pdf'})
        return httpx.Response(200,text='<rss><channel><item><guid>1</guid><description>Text</description><enclosure url="/file.pdf" type="application/pdf" length="3"/></item></channel></rss>')
    adapter=RSSAdapter(httpx.MockTransport(respond))
    feed=asyncio.run(adapter.resolve_feed('https://rss.example/feed'))
    page=asyncio.run(adapter.discover(feed.reference,feed.latest_cursor,10))
    value,=page.publications
    media,=value.media
    assert media.locator=='https://rss.example/file.pdf' and media.mime_type=='application/pdf' and media.size_bytes==3
    assert value.content['blocks'][-1]['role']=='attachment'
    assert all('/file.pdf' not in url for url in calls)
    output=BytesIO()
    actual=asyncio.run(adapter.download_media(value.metadata.reference,media,output))
    assert actual.size_bytes==3 and output.getvalue()==b'pdf'


def test_rss_source_bridge_keeps_adapter_keys_and_ready_content_across_pages():
    from oko.collection.rss_source import RSSAdapterSource
    transport=httpx.MockTransport(lambda _:httpx.Response(200,text='<rss><channel><title>News</title><item><guid>2</guid><link>https://web.example/2</link><description>Second</description></item><item><guid>1</guid><link>https://web.example/1</link><description>First</description></item></channel></rss>'))
    source=RSSAdapterSource('https://rss.example/feed',1,transport)
    source.initial_history={'mode':'count','count':2}
    boundary=asyncio.run(source.discover(None))
    assert boundary.publications==() and boundary.continuation is not None
    page=asyncio.run(source.discover(boundary.cursor))
    assert [p.teaser.text for p in page.publications]==['First','Second']
    assert all(p.adapter_reference.key.platform==Platform.rss for p in page.publications)
    assert all(p.teaser.adapter_content.needs_fetch is False for p in page.publications)
    assert page.continuation is None and page.observation.description==''
    replay=asyncio.run(source.discover(page.cursor))
    assert [p.adapter_reference for p in replay.publications]==[p.adapter_reference for p in page.publications]


def test_malformed_record_keeps_its_key_and_does_not_drop_healthy_peers():
    from oko.collection.adapters import AdapterErrorKind, AdapterException
    import pytest
    raw='<rss><channel><item><guid>bad</guid><description><![CDATA[<a href="https://[bad/">Bad</a>]]></description></item><item><guid>good</guid><description>Good</description></item></channel></rss>'
    adapter=RSSAdapter(httpx.MockTransport(lambda _:httpx.Response(200,text=raw)))
    feed=asyncio.run(adapter.resolve_feed('https://rss.example/feed'))
    cursor=asyncio.run(adapter.cursor_before(feed.reference,feed.latest_cursor,1))
    page=asyncio.run(adapter.discover(feed.reference,cursor,10))
    assert len(page.publications)==2
    good,bad=page.publications
    assert good.content['blocks'][0]['text']=='Good' and not good.needs_fetch
    assert json.loads(bad.metadata.reference.key.external_id)[1]=='bad' and bad.needs_fetch
    assert bad.issues[0].kind==AdapterErrorKind.permanent
    with pytest.raises(AdapterException) as caught:
        asyncio.run(adapter.fetch(bad.metadata.reference))
    assert caught.value.issue.kind==AdapterErrorKind.permanent


def test_latest_rss_metadata_does_not_fetch_history_until_the_offset_needs_it():
    from oko.collection.adapters import AdapterException, AdapterErrorKind
    import pytest
    calls=[]
    def respond(request):
        calls.append(request.url.path)
        if request.url.path=='/archive':
            return httpx.Response(503)
        return httpx.Response(200,text='<rss xmlns:atom="http://www.w3.org/2005/Atom"><channel><item><guid>2</guid></item><item><guid>1</guid></item><atom:link rel="next" href="/archive"/></channel></rss>')
    adapter=RSSAdapter(httpx.MockTransport(respond))
    feed=asyncio.run(adapter.resolve_feed('https://rss.example/feed'))
    page=asyncio.run(adapter.discover(feed.reference,feed.latest_cursor,1))
    assert len(page.publications)==1 and calls==['/feed','/feed']
    with pytest.raises(AdapterException) as caught:
        asyncio.run(adapter.cursor_before(feed.reference,feed.latest_cursor,10))
    assert caught.value.issue.kind==AdapterErrorKind.temporary
    assert calls[-1]=='/archive'


def test_new_rss_history_run_freezes_its_count_and_resolves_an_archive_offset():
    from oko.collection.rss_source import RSSAdapterSource
    def respond(request):
        text=('<item><guid>one</guid><description>One</description></item>' if request.url.path=='/archive' else
              '<item><guid>two</guid><description>Two</description></item><atom:link rel="next" href="/archive"/>')
        return httpx.Response(200,text='<rss xmlns:atom="http://www.w3.org/2005/Atom"><channel>'+text+'</channel></rss>')
    source=RSSAdapterSource('https://rss.example/feed',1,httpx.MockTransport(respond))
    source.initial_history={'mode':'count','count':1,'run':'first'}
    boundary=asyncio.run(source.discover(None))
    first=asyncio.run(source.discover(boundary.cursor))
    assert [p.teaser.text for p in first.publications]==['Two']
    source.initial_history={'mode':'count','count':2,'run':'second'}
    boundary=asyncio.run(source.discover(first.cursor))
    assert boundary.publications==() and boundary.continuation is not None
    # The already admitted pass keeps its selection even if live settings move.
    source.initial_history={'mode':'count','count':1,'run':'second'}
    second=asyncio.run(source.discover(boundary.cursor))
    assert [p.teaser.text for p in second.publications]==['One','Two']


def test_invalid_rss_permalink_keeps_a_known_guid_and_healthy_records():
    raw='<rss><channel><item><guid>bad-link</guid><link>https://[bad/</link><description>Usable excerpt</description></item><item><guid>good</guid><description>Good</description></item></channel></rss>'
    adapter=RSSAdapter(httpx.MockTransport(lambda _:httpx.Response(200,text=raw)))
    feed=asyncio.run(adapter.resolve_feed('https://rss.example/feed'))
    cursor=asyncio.run(adapter.cursor_before(feed.reference,feed.latest_cursor,1))
    page=asyncio.run(adapter.discover(feed.reference,cursor,10))
    assert [value.content['blocks'][0]['text'] for value in page.publications]==['Good','Usable excerpt']
    assert page.publications[1].metadata.reference.url=='https://rss.example/feed'
    assert any(issue.code=='rss-invalid-link' for issue in page.issues)
