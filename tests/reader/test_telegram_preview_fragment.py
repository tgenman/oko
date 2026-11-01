"""Forward-only web restrictions keep metadata as an explicitly incomplete excerpt."""
import time
import httpx
import pytest
from tests.support.app import create_app
from tests.support.telegram import configure_fast_web,settled
from tests.support.telegram_web import page, forwarded_unavailable_page, preview_metadata_page
from tests.support.v3 import TestClient


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
        content=client.get(path.replace('/api/v3/materials/', '/api/v3/publication/')).json()
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
        assert 'Saved fragment' in client.get(path.replace('/api/v3/materials/', '/api/v3/publication/')).json()['text']
        assert 'HTTP 429' not in client.get(path.replace('/api/v3/materials/', '/api/v3/publication/')).json()['linked_content']['post_text']
