"""A broken media stream saves a usable post and waits for an explicit retry."""
import time
import httpx
import pytest
from tests.support.app import create_app
from tests.support.telegram import configure_fast_web, settled
from tests.support.telegram_web import page, BrokenMediaStream
from tests.support.v3 import TestClient

@pytest.mark.parametrize('article', [False, True])
@pytest.mark.parametrize('retained', [False, True])
@pytest.mark.parametrize('failure', ['disconnect', 'http500'])
def test_partial_download_is_saved_without_automatic_retries_and_can_be_retried(tmp_path, retained, failure, article):
    broken, calls, advance = [not retained], [], [0.]
    def response(request):
        if request.url.host == 'api.telegra.ph':
            return httpx.Response(500) if broken[0] else httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[{'tag':'p','children':['Saved article body']}]}})
        if request.url.host == 'cdn.example':
            calls.append(str(request.url))
            if request.url.path.endswith('cover.jpg'):
                return httpx.Response(200, content=b'cover', headers={'content-type':'image/jpeg'})
            if broken[0]:
                return httpx.Response(500) if failure=='http500' else httpx.Response(200,stream=BrokenMediaStream(),headers={'content-type':'video/mp4'})
            return httpx.Response(200,content=b'complete-video',headers={'content-type':'video/mp4'})
        media='<a class="tgme_widget_message_video_player"><video src="https://cdn.example/video.mp4"></video></a><a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/cover.jpg\')"></a>'
        if article:
            media += ' https://telegra.ph/Article'
        return httpx.Response(200,text=page([(11,'Post text <a href="https://example.org">Original link</a>'+media)]))
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response),clock=lambda:time.monotonic()+advance[0])
    with TestClient(create_app(tmp_path,**options),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        feed=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox')).json()['feed_id']
        identity=settled(client)[0]['id'];path='/api/v3/materials/'+identity
        if retained:
            broken[0]=True
            client.post(path+'/retry');settled(client)
        content=client.get(path).json()['content']
        assert not content['loading']
        assert 'Post text' in content['text']
        assert '[Original link](https://example.org)' in content['text']
        linked=content['linked_content']
        assert linked and '[!load-error]' in linked['post_text']
        assert '<https://t.me/fixture/11>' in linked['post_text']
        assert ('HTTP 500' if failure=='http500' else 'соединение') in linked['post_text']
        assert len(content['attachments']) == (2 if retained else 1)
        for a in content['attachments']:
            assert client.get(a['url']).content in (b'cover',b'complete-video')
        assert client.get('/api/v3/collection-status/'+feed).json()['source']['state']=='complete'
        assert client.get('/api/v3/status').json()['problems']['count']==0
        count=len(calls);advance[0]+=3600
        client.post('/api/v3/collect',json={'scope':'feed','feed_id':feed});time.sleep(.4)
        assert len(calls)==count
        broken[0]=False
        client.post(path+'/retry');settled(client)
        recovered=client.get(path).json()['content']
        assert recovered['completeness']['state']=='complete'
        assert (recovered['linked_content'] is None) == (not article)
        if article:
            assert '[!load-error]' not in recovered['linked_content']['post_text']
            assert recovered['linked_content']['articles'][0]['text']=='Saved article body'
        assert len(recovered['attachments'])==2
        assert not recovered['changed'] and recovered['previous_versions']==[]


def test_accepted_partial_survives_restart_and_source_refresh(tmp_path):
    calls=[]
    def response(request):
        if request.url.host=='cdn.example':
            calls.append(1)
            return httpx.Response(200,stream=BrokenMediaStream())
        return httpx.Response(200,text=page([(11,'Saved own text<a class="tgme_widget_message_video_player"><video src="https://cdn.example/video.mp4"></video></a>')]))
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response))
    headers={'Authorization':'Bearer test'}
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        configure_fast_web(client)
        feed=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox')).json()['feed_id']
        identity=settled(client)[0]['id'];path='/api/v3/materials/'+identity
        assert 'соединение' in client.get(path).json()['content']['linked_content']['post_text']
    count=len(calls)
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        configure_fast_web(client)
        client.post('/api/v3/collect',json={'scope':'feed','feed_id':feed});time.sleep(.4)
        assert len(calls)==count
        assert client.get('/api/v3/collection-status/'+feed).json()['source']['state']=='complete'
        assert not client.get(path).json()['content']['loading']
