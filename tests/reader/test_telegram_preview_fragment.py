"""Forward-only web restrictions keep metadata as an explicitly incomplete excerpt."""
import time
import httpx
import pytest
from tests.support.app import create_app
from tests.support.telegram import configure_fast_web,settled
from tests.support.telegram_web import page, forwarded_unavailable_page, preview_metadata_page
from tests.support.v3 import TestClient
from tests.support.codex import CodexService,configure_codex


@pytest.mark.parametrize('mode',['good','wrong-id','channel','http500','redirect'])
def test_forwarded_fragment_is_incomplete_without_errors_or_automatic_retry(tmp_path,mode):
    requests=[];advance=[0.];codex=CodexService()
    def response(request):
        requests.append(str(request.url))
        if request.url.path=='/fixture/11':
            if mode=='redirect':return httpx.Response(302,headers={'location':'https://t.me/other/99'})
            return httpx.Response(500) if mode=='http500' else httpx.Response(200,text=preview_metadata_page(identity='other/99' if mode=='wrong-id' else 'fixture/11') if mode!='channel' else '<meta property="og:description" content="CHANNEL DESCRIPTION">')
        return httpx.Response(200,text=forwarded_unavailable_page())
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response),clock=lambda:time.monotonic()+advance[0],codex_client=codex)
    headers={'Authorization':'Bearer test'}
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        configure_fast_web(client);configure_codex(client)
        client.patch('/api/v3/settings/oko/global',json={'theses':True,'prompt_filters':'Summarize the full original'})
        feed=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).json()['feed_id']
        identity=settled(client)[0]['id'];path='/api/v3/materials/'+identity
        content=client.get(path).json()['content'];linked=content['linked_content']
        assert content['completeness']['state']=='incomplete' and content['completeness']['reason']=='source_incomplete'
        assert 'предпросмотра' in content['completeness']['message']
        assert linked and 'https://t.me/fixture/11' in linked['post_text']
        assert 'https://t.me/original/42' in linked['post_text']
        assert 'Original author' in linked['post_text']
        if mode=='good':
            assert 'Metadata fragment…' in content['text']
            assert 'Фрагмент обрезан' in linked['post_text']
        else:
            assert 'Metadata fragment' not in content['text'] and 'CHANNEL DESCRIPTION' not in content['text']
            assert '[!load-error]' in linked['post_text']
        assert client.get('/api/v3/status/details').json()['problems']['count']==0
        assert client.get('/api/v3/collection-status/'+feed).json()['source']['state']=='complete'
        assert codex.inputs==[]
        count=sum('/fixture/11' in x for x in requests);advance[0]+=3600
        client.post('/api/v3/collect',json={'scope':'feed','feed_id':feed});time.sleep(.4)
        assert sum('/fixture/11' in x for x in requests)==count
        client.post(path+'/retry');settled(client)
        assert sum('/fixture/11' in x for x in requests)==count+1
        assert not client.get(path).json()['content']['changed']
    count=sum('/fixture/11' in x for x in requests)
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        configure_fast_web(client);time.sleep(.2)
        assert sum('/fixture/11' in x for x in requests)==count
        assert client.get(path).json()['content']['linked_content']==linked


def test_previous_fragment_survives_failed_manual_refresh_and_legacy_error_is_corrected(tmp_path):
    confirmed=[False];broken=[False]
    def response(request):
        if request.url.path=='/fixture/11':
            return httpx.Response(500) if broken[0] else httpx.Response(200,text=preview_metadata_page('Saved short preview'))
        html=forwarded_unavailable_page()
        return httpx.Response(200,text=html if confirmed[0] else html.replace('text_not_supported_wrap','old_unknown_wrapper'))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        path='/api/v3/materials/'+settled(client)[0]['id']
        assert client.get('/api/v3/status/details').json()['problems']['count']>0
        confirmed[0]=True;client.post(path+'/retry');settled(client)
        content=client.get(path).json()['content']
        assert 'Saved short preview' in content['text']
        assert 'https://t.me/original/42' in content['linked_content']['post_text']
        assert 'previous_versions' not in content and not content['changed']
        assert client.get('/api/v3/status/details').json()['problems']['count']==0
        broken[0]=True;client.post(path+'/retry');settled(client)
        failed=client.get(path).json()['content']
        assert failed['text']==content['text']
        assert 'HTTP 500' in failed['linked_content']['post_text']
        assert 'previous_versions' not in failed and not failed['changed']
        assert client.get('/api/v3/status/details').json()['problems']['count']==0
        broken[0]=False;client.post(path+'/retry');settled(client)
        assert 'HTTP 500' not in client.get(path).json()['content']['linked_content']['post_text']


def test_metadata_does_not_replace_a_genuine_full_local_copy(tmp_path):
    restricted=[False]
    def response(request):
        if request.url.path=='/fixture/11':return httpx.Response(200,text=preview_metadata_page('Short snippet'))
        return httpx.Response(200,text=forwarded_unavailable_page() if restricted[0] else page([(11,'Complete original body')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        path='/api/v3/materials/'+settled(client)[0]['id']
        restricted[0]=True;client.post(path+'/retry');settled(client)
        content=client.get(path).json()['content']
        assert content['text']=='Complete original body'
        assert content['post_web_unavailable'] is True
        assert 'previous_versions' not in content and not content['changed']


def test_preview_rate_limit_keeps_runtime_wait_and_saved_fragment(tmp_path):
    limited=[False]
    def response(request):
        if request.url.path=='/fixture/11':
            return httpx.Response(429,headers={'retry-after':'60'}) if limited[0] else httpx.Response(200,text=preview_metadata_page('Saved fragment'))
        return httpx.Response(200,text=forwarded_unavailable_page())
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        feed=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).json()['feed_id']
        path='/api/v3/materials/'+settled(client)[0]['id']
        limited[0]=True;client.post(path+'/retry')
        for _ in range(100):
            source=client.get('/api/v3/collection-status/'+feed).json()['source']
            if source['state']=='waiting':break
            time.sleep(.02)
        assert source['state']=='waiting' and source['operation']['retry_at']
        assert 'Saved fragment' in client.get(path).json()['content']['text']
        assert 'HTTP 429' not in client.get(path).json()['content']['linked_content']['post_text']


@pytest.mark.parametrize('text',[chr(0x2063)+'\nReadable heading\nBody', 'Long first paragraph ' * 20])
def test_fragment_title_is_readable_and_bounded_without_truncating_saved_text(tmp_path,text):
    def response(request):
        return httpx.Response(200,text=preview_metadata_page(text) if request.url.path=='/fixture/11' else forwarded_unavailable_page())
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        card=settled(client)[0]
        material=client.get('/api/v3/materials/'+card['id']).json()
        assert any(char.isalnum() for char in material['title'])
        assert len(material['title'])<=141
        assert text.strip() in material['content']['text']
