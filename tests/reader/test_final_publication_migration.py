"""Final offline conversion and removed capabilities at the reader boundary."""
import httpx
import pytest
from tests.support.app import create_app
from tests.support.presence import presence
from tests.support.reader import HEADERS,wait_for
from tests.support.subscriptions import add_sources
from tests.support.v3 import TestClient
from tests.support.youtube import PNG
from oko.storage.migration import migrate_publications


@pytest.mark.parametrize('enabled',[True,False])
@pytest.mark.parametrize('partial',[False,True])
def test_rss_cached_article_and_image_survive_offline_conversion(tmp_path,enabled,partial):
    source=tmp_path/'legacy'
    def response(request):
        if request.url.path=='/feed':
            return httpx.Response(200,text='<rss><channel><item><guid>entry</guid><link>https://web.test/article</link><title>Entry</title><description>Announcement</description></item></channel></rss>')
        if request.url.path=='/missing.png':
            return httpx.Response(503)
        if request.url.path=='/image.png':
            return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
        return httpx.Response(200,text='<article>Cached article<img src="/image.png">'+('<img src="/missing.png">' if partial else '')+'</article>')
    options=dict(token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(source,**options),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        add_sources(client,[('https://source.test/feed','RSS','Inbox')])
        card=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))
        wait_for(client,lambda:bool(client.get('/api/v3/materials/'+card['id']).json()['content']['images']))
        if not enabled:
            assert client.patch('/api/v3/feeds/'+card['feed_id'],json={'transport':None}).status_code==200
    destination=tmp_path/'converted'
    report=migrate_publications(source,destination)
    assert report['success'],report
    assert any(f['platform']=='rss' for f in report['feeds'])
    assert report['history_boundaries'][0]['selection']['mode']=='date'
    assert 'count' not in report['history_boundaries'][0]['selection']
    def offline(request):
        raise AssertionError('Reading transferred latest data must not require a network')
    with TestClient(create_app(destination/'storage',token='test',api_only=True,rss_transport=httpx.MockTransport(offline)),headers=HEADERS) as client:
        material=client.get('/api/v3/materials/'+card['id']).json()
        assert 'Cached article' in material['content']['text']
        assert client.get(material['content']['attachments'][0]['url']).content==PNG
        assert len(client.get('/api/v3/cards').json())==1


def test_migration_refuses_unknown_native_source_kind_instead_of_skipping_it(tmp_path):
    from oko.storage.store import Store
    from tests.support.telegram import Telegram,connect_request
    source=tmp_path/'legacy'
    with TestClient(create_app(source,token='test',api_only=True,telegram_client=Telegram()),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
    store=Store(source,lambda _:None)
    try:
        with store.db:
            store.db.execute("UPDATE feeds SET config='{}'")
    finally:
        store.db.close()
    report=migrate_publications(source,tmp_path/'converted')
    assert not report['success']
    assert any('source_kind' in entry['reason'] for entry in report['conflicts'])
    assert not (tmp_path/'converted'/'storage').exists()
