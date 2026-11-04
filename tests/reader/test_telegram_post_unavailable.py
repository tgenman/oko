"""Whole-post web restrictions through the real reader HTTP interface."""
from tests.support.telegram import configure_fast_web
import httpx

from tests.support.app import create_app
from tests.support.codex import CodexService, configure_codex
from tests.support.telegram import settled
from tests.support.telegram_web import external_page, mark_post_unavailable
from tests.support.v3 import TestClient


def unavailable_page(before=0, text='', media='', restricted=True):
    html = external_page([(11, text+media)], before=before)
    return mark_post_unavailable(html, 'fixture/11') if restricted else html


def test_whole_post_restriction_is_saved_as_unavailable_without_ai(tmp_path):
    fetches = []
    def response(request):
        if request.url.params.get('before'): fetches.append(str(request.url))
        return httpx.Response(200, text=unavailable_page(int(request.url.params.get('before', '0'))))
    codex = CodexService()
    options = dict(codex_client=codex, token='test', api_only=True, rss_transport=httpx.MockTransport(response))
    headers = {'Authorization':'Bearer test'}
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        configure_fast_web(client)
        configure_codex(client)
        client.patch('/api/v3/settings/oko/global', json=dict(theses=True, prompt_filters='Filter the post'))
        result = client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        assert result.status_code == 200, result.text
        cards = settled(client)
        url = '/api/v3/materials/'+cards[0]['id']
        material = client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        from tests.support.storage import database
        with database(tmp_path) as db:
            job=db.execute("SELECT p.status,j.status,j.reason_code FROM publications p JOIN jobs j ON j.kind='fetch' AND j.item=p.id WHERE p.id=%s",(cards[0]['id'],)).fetchone()
            assert job and tuple(job)==('pending_collection','unavailable','post-web-unavailable')
        assert client.get('/api/v3/status/details').json()['problems']['count'] > 0
        assert material['post_web_unavailable'] is True, material
        assert 'недоступна' in material['completeness']['message'].lower()
        assert material['text'] == ''
        assert material['external_attachments'] == []
        assert [media for media in material['media'] if media['role'] == 'attachment'] == []
        frame = client.get('/api/v3/publication/'+cards[0]['id']).json()
        assert frame['post_web_unavailable'] is True
        assert cards[0]['post_web_unavailable'] is True
        assert cards[0]['title'] == 'Пост доступен только в Telegram'
        assert material['filter']['state'] == 'disabled'
        assert material['theses']['state'] == 'disabled'
        assert codex.inputs == []
        baseline = len(fetches)
        client.post('/api/v3/collect'); settled(client)
        assert len(fetches) == baseline
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        configure_fast_web(client)
        material = client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        assert material['post_web_unavailable'] is True
        assert client.get('/api/v3/status/details').json()['problems']['count'] > 0


import pytest
import time


def test_previous_local_text_is_kept_when_whole_post_becomes_restricted(tmp_path):
    restricted = [False]
    def response(request):
        return httpx.Response(200, text=unavailable_page(int(request.url.params.get('before', '0')), '' if restricted[0] else 'Saved own text', restricted=restricted[0]))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        url = '/api/v3/materials/'+settled(client)[0]['id']
        assert client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()['text'] == 'Saved own text'
        restricted[0] = True
        client.post(url+'/retry'); settled(client)
        content = client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        assert content['text'] == 'Saved own text'
        assert content['post_web_unavailable'] is True
        assert content['changed'] is False
        assert client.get('/api/v3/status/details').json()['problems']['count'] > 0


def test_missing_saved_file_reason_is_recorded_in_its_job(tmp_path):
    from tests.support.reader import wait_for
    from tests.support.storage import database

    restricted=[False]
    photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
    def response(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=b'saved photo',headers={'content-type':'image/jpeg'})
        return httpx.Response(200,text=unavailable_page(int(request.url.params.get('before','0')),
            '' if restricted[0] else 'Saved text', '' if restricted[0] else photo,restricted=restricted[0]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=settled(client)[0]['id']
        wait_for(client,lambda: (value if (value:=client.get('/api/v3/publication/'+item).json()) and value.get('media') else None))
        with database(tmp_path) as db:
            media=db.execute("SELECT id,storage_key FROM media_files WHERE publication_id=%s AND status='ready'",(item,)).fetchone()
            assert media and media['storage_key']
            media_id=media['id']
            (tmp_path/media['storage_key']).unlink()
        restricted[0]=True
        client.post('/api/v3/materials/'+item+'/retry')
        def saved_reason():
            with database(tmp_path) as db:
                return db.execute("SELECT status,reason_code,reason_message FROM jobs WHERE kind='download_media' AND item=%s",(media_id,)).fetchone()
        try:
            reason=wait_for(client,lambda: (row if (row:=saved_reason()) and row['reason_message']=='Локальный файл вложения недоступен' else None))
        except AssertionError as error:
            raise AssertionError(f"Job: {dict(saved_reason() or {})}; material: {client.get('/api/v3/publication/'+item).json()}") from error
        assert reason['status']=='unavailable'
