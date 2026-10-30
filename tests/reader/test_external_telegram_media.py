"""External Telegram media are readable saved publications, not download errors."""
from tests.support.telegram import configure_fast_web
import httpx

from tests.support.app import create_app
from tests.support.telegram import settled
from tests.support.telegram_web import external_page as page, AUDIO, VIDEO
from tests.support.v3 import TestClient



def test_external_audio_is_saved_without_errors_and_is_typed_for_reader(tmp_path):
    def response(request):
        return httpx.Response(200, text=page([(11, 'Own text'+AUDIO)],before=int(request.url.params.get('before','0'))))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        result = client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        assert result.status_code == 200, result.text
        cards = settled(client)
        content = client.get('/api/v3/publication/'+cards[0]['id']).json()
        assert client.get('/api/v3/status/details').json()['problems']['count'] == 0
        assert content['completeness']['reason'] == 'external_attachment'
        assert content['external_attachments'] == [dict(kind='audio', title='A recorded lesson', author='Fixture author', size_label=None, duration=None, poster_url=None, source_url='https://t.me/fixture/11', reason='public_preview_unavailable', restriction=None)]
        assert cards[0]['external_attachment_kinds'] == ['audio']



def test_web_video_size_limit_preserves_cover_and_real_cover_failure(tmp_path):
    broken = [False]
    def response(request):
        if request.url.host=='cdn.example':
            return httpx.Response(503 if broken[0] else 200, content=b'poster', headers={'content-type':'image/jpeg'})
        return httpx.Response(200,text=page([(11,'Video text'+VIDEO)],before=int(request.url.params.get('before','0'))))
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response))
    headers={'Authorization':'Bearer test'}
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        card=settled(client)[0]
        url='/api/v3/materials/'+card['id']
        content=client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        media=content['external_attachments'][0]
        assert media['kind']=='video' and media['restriction']=='size_limit' and media['duration']=='0:58'
        assert client.get(media['poster_url']).content==b'poster'
        assert client.get('/api/v3/status/details').json()['problems']['count']==0
    # Persisted availability and covers survive a restart, without a personal source.
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        configure_fast_web(client)
        assert client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()['external_attachments'][0]==media
        assert client.get('/api/v3/status/details').json()['problems']['count']==0
    broken[0]=True
    with TestClient(create_app(tmp_path/'failed',**options),headers=headers) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        card=settled(client)[0]
        content=client.get('/api/v3/publication/'+card['id']).json()
        assert content['external_attachments'][0]['poster_url'] is None
        assert content['external_attachments'][0]['restriction']=='size_limit'
        assert content['completeness']['reason']!='external_attachment'
        assert client.get('/api/v3/status/details').json()['problems']['count']>0
        assert 'HTTP 503' in content['completeness']['message']


def test_external_metadata_changes_keep_old_version_context_without_false_updated(tmp_path):
    phase=[0]
    def response(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=b'poster',headers={'content-type':'image/jpeg'})
        video=VIDEO if phase[0]!=1 else VIDEO.replace('Media is too big','Not supported')
        return httpx.Response(200,text=page([(11,('Original text' if phase[0]<2 else 'Edited own text')+video)],before=int(request.url.params.get('before','0'))))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        card=settled(client)[0];url='/api/v3/materials/'+card['id']
        phase[0]=1
        client.post(url+'/retry')
        second=client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        import time
        for _ in range(150):
            second=client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
            if not second['loading'] and second['external_attachments'][0]['restriction'] is None:break
            time.sleep(.02)
        assert second['external_attachments'][0]['restriction'] is None
        assert second['changed'] is False
        phase[0]=2
        client.post(url+'/retry')
        for _ in range(150):
            third=client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
            if not third['loading'] and third['text'].startswith('Edited own text'):break
            time.sleep(.02)
        assert third['changed'] is True
        assert 'previous_versions' not in third
        assert client.get('/api/v3/status/details').json()['problems']['count']==0


def test_local_video_copy_wins_over_later_web_restriction(tmp_path):
    restricted=[False]
    def response(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=b'local-video',headers={'content-type':'video/mp4'})
        body=VIDEO if restricted[0] else '<a class="tgme_widget_message_video_player"><video src="https://cdn.example/video.mp4"></video></a>'
        return httpx.Response(200,text=page([(11,'Same own text'+body)],before=int(request.url.params.get('before','0'))))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        card=settled(client)[0];url='/api/v3/materials/'+card['id']
        first=client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json();video=next(a for a in [media for media in first['media'] if media['role']=='attachment'] if a['kind']=='video')
        assert client.get(video['url']).content==b'local-video'
        restricted[0]=True
        client.post(url+'/retry');settled(client)
        second=client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        assert second['external_attachments']==[]
        assert client.get(next(a['url'] for a in [media for media in second['media'] if media['role']=='attachment'] if a['kind']=='video')).content==b'local-video'
        assert client.get('/api/v3/status/details').json()['problems']['count']==0


import pytest

@pytest.mark.parametrize('failure',[403,404,429,'timeout','local-limit','not-file'])
def test_real_download_failures_are_not_reclassified_as_external(tmp_path,failure):
    def response(request):
        if request.url.host=='cdn.example':
            if failure=='timeout':raise httpx.ReadTimeout('fixture timeout',request=request)
            if failure=='local-limit':return httpx.Response(200,content=b'x',headers={'content-length':'999999999999'})
            if failure=='not-file':return httpx.Response(200,text='<html>not a file</html>',headers={'content-type':'text/html'})
            return httpx.Response(failure,headers={'retry-after':'60'})
        return httpx.Response(200,text=page([(11,'Own text'+AUDIO+'<audio src="https://cdn.example/media.ogg"></audio>')],before=int(request.url.params.get('before','0'))))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        card=settled(client)[0]
        content=client.get('/api/v3/publication/'+card['id']).json()
        assert client.get('/api/v3/status/details').json()['problems']['count']>0
        assert 'Own text' in content['text']
        assert content['completeness']['reason']!='external_attachment'
        assert all(a['source_url']=='https://t.me/fixture/11' for a in content['external_attachments'])


def test_reassessment_closes_only_the_recovered_material_problem(tmp_path):
    recognized=[False]
    def response(request):
        if request.url.host=='cdn.example':return httpx.Response(404)
        own=AUDIO if recognized[0] else '<iframe src="https://example.org/embed"></iframe>'
        return httpx.Response(200,text=page([(11,'Own text'+own),(12,'Other broken post<audio src="https://cdn.example/media.ogg"></audio>')],before=int(request.url.params.get('before','0'))))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        cards=settled(client,2)
        materials=[client.get('/api/v3/publication/'+card['id']).json() for card in cards]
        target=next(m for m in materials if m['source']['message_ids']==['11'])
        other=next(m for m in materials if m['source']['message_ids']==['12'])
        assert client.get('/api/v3/status/details').json()['problems']['count']>=2
        recognized[0]=True
        result=client.post('/api/v3/materials/'+target['id']+'/retry')
        assert result.status_code==200, result.text
        import time
        for _ in range(150):
            problems=client.get('/api/v3/status/details').json()['problems']
            affected={r['material_id'] for p in problems['current'] for r in p['affected'] if r['kind']=='material'}
            if target['id'] not in affected:break
            time.sleep(.02)
        assert target['id'] not in affected, str(problems)
        assert other['id'] in affected
        assert problems['recent']


def test_new_external_attachment_after_complete_text_is_not_a_failure(tmp_path):
    external=[False]
    def response(request):
        return httpx.Response(200,text=page([(11,'Same own text'+(AUDIO if external[0] else ''))],before=int(request.url.params.get('before','0'))))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        url='/api/v3/materials/'+settled(client)[0]['id']
        assert client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()['completeness']['state']=='complete'
        external[0]=True
        client.post(url+'/retry');settled(client)
        content=client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        assert content['external_attachments'][0]['kind']=='audio'
        assert client.get('/api/v3/status/details').json()['problems']['count']==0


def test_unchanged_external_only_post_is_not_refetched_on_collect(tmp_path):
    fetches=[]
    def response(request):
        if request.url.params.get('before'):fetches.append(str(request.url))
        return httpx.Response(200,text=page([(11,'Same own text'+AUDIO)],before=int(request.url.params.get('before','0'))))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'));settled(client)
        # The first fetch establishes the saved external-only state.
        baseline=len(fetches)
        client.post('/api/v3/collect')
        import time
        for _ in range(150):
            state=client.get('/api/v3/status/details').json()
            if state['ready_sources']==state['total_sources'] and not any(w['phase']=='collecting' for w in state['work']):break
            time.sleep(.02)
        assert len(fetches)==baseline
        assert state['problems']['count']==0


def test_lost_local_file_does_not_turn_into_a_normal_external_notice(tmp_path):
    restricted=[False]
    def response(request):
        if request.url.host=='cdn.example':return httpx.Response(200,content=b'local-video',headers={'content-type':'video/mp4'})
        video=VIDEO if restricted[0] else '<a class="tgme_widget_message_video_player"><video src="https://cdn.example/video.mp4"></video></a>'
        return httpx.Response(200,text=page([(11,'Own text'+video)],before=int(request.url.params.get('before','0'))))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        url='/api/v3/materials/'+settled(client)[0]['id']
        attachment=[media for media in client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()['media'] if media['role'] == 'attachment'][0]
        from tests.support.storage import database
        media_id=attachment['url'].rsplit('/',1)[-1]
        with database(tmp_path) as db:
            key=db.execute('SELECT storage_key FROM media_files WHERE id=%s',(media_id,)).fetchone()[0]
        local_file=tmp_path/key
        assert local_file.is_file()
        local_file.unlink()
        restricted[0]=True
        client.post(url+'/retry');settled(client)
        assert client.get('/api/v3/status/details').json()['problems']['count']>0


def test_external_document_metadata_text_and_restart(tmp_path):
    from tests.support.telegram_web import DOCUMENT
    requests = []
    def response(request):
        requests.append(str(request.url))
        return httpx.Response(200, text=page([(13,'Own caption'+DOCUMENT), (14,DOCUMENT.replace('Бесы.txt','').replace('36.2 KB','')), (15,'<a class="tgme_widget_message_reply" href="https://t.me/fixture/1"><span class="tgme_widget_message_author_name">Author</span><div class="js-message_reply_text">Reply context</div></a>'+DOCUMENT)],before=int(request.url.params.get('before','0'))))
    options = dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response))
    headers = {'Authorization':'Bearer test'}
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        configure_fast_web(client)
        assert client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox')).status_code==200
        cards=settled(client,count=3)
        assert any(c['title']=='Бесы.txt' for c in cards)
        card=next(c for c in cards if c['title']=='Own caption')
        url='/api/v3/materials/'+card['id']
        content=client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        assert content['text']=='Own caption'
        assert card['external_attachment_kinds']==['document']
        media=content['external_attachments'][0]
        assert media['title']=='Бесы.txt' and media['size_label']=='36.2 KB'
        assert media['author'] is None and media['source_url']=='https://t.me/fixture/13'
        assert content['completeness']['reason']=='external_attachment'
        empty=next(c for c in cards if c['title']=='Файл доступен в Telegram')
        empty_content=client.get('/api/v3/publication/'+empty['id']).json()
        assert empty_content['text']==''
        assert empty_content['external_attachments'][0]['size_label'] is None
        assert client.get('/api/v3/status/details').json()['problems']['count']==0
        assert all('/s/' in request for request in requests)
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        configure_fast_web(client)
        assert client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()['external_attachments'][0]==media
        assert client.get('/api/v3/status/details').json()['problems']['count']==0
