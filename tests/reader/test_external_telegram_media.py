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
        first=client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json();video=next(a for a in [media for media in first['media'] if media['url'] is not None] if a['kind']=='video')
        assert client.get(video['url']).content==b'local-video'
        restricted[0]=True
        client.post(url+'/retry');settled(client,timeout=15)
        second=client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        assert second['external_attachments']==[]
        assert client.get(next(a['url'] for a in [media for media in second['media'] if media['url'] is not None] if a['kind']=='video')).content==b'local-video'
        problems=client.get('/api/v3/status/details').json()['problems']
        assert problems['count']==0,problems


import pytest


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
        attachment=[media for media in client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()['media'] if media['url'] is not None][0]
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
        with database(tmp_path) as db:
            row=db.execute("SELECT status,reason_message FROM jobs WHERE kind='download_media' AND item=%s",(media_id,)).fetchone()
            assert row and tuple(row)==('unavailable','Локальный файл вложения недоступен')


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
