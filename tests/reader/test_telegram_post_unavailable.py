"""Whole-post web restrictions through the real reader HTTP interface."""
from tests.support.telegram import configure_fast_web
import httpx

from tests.support.app import create_app
from tests.support.codex import CodexService, configure_codex
from tests.support.telegram import settled
from tests.support.telegram_web import external_page, AUDIO, VIDEO, mark_post_unavailable
from tests.support.v3 import TestClient


def unavailable_page(before=0, text='', media='', restricted=True):
    html = external_page([(11, text+media)], before=before)
    return mark_post_unavailable(html, 'fixture/11') if restricted else html


def test_whole_post_restriction_is_saved_separately_without_errors_or_ai(tmp_path):
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
        material = client.get(url).json()
        assert client.get('/api/v3/status').json()['problems']['count'] == 0
        assert material['content']['post_web_unavailable'] is True
        assert material['content']['completeness']['reason'] == 'post_web_unavailable'
        assert material['content']['text'] == ''
        assert material['content']['external_attachments'] == []
        assert material['content']['attachments'] == []
        frame = client.post('/api/v3/reading/check', json=dict(storage_id=None, resources=[dict(kind='material',id=cards[0]['id'],known_version=None)])).json()
        assert frame['resources'][0]['data']['content']['post_web_unavailable'] is True
        assert cards[0]['post_web_unavailable'] is True
        assert cards[0]['title'] == 'Пост доступен только в Telegram'
        assert material['filter']['attempt']['state'] == 'disabled'
        assert material['theses']['attempt']['state'] == 'disabled'
        assert codex.inputs == []
        baseline = len(fetches)
        client.post('/api/v3/collect'); settled(client)
        assert len(fetches) == baseline
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        configure_fast_web(client)
        material = client.get(url).json()
        assert material['content']['post_web_unavailable'] is True
        assert client.get('/api/v3/status').json()['problems']['count'] == 0


import pytest
import time


@pytest.mark.parametrize('text,media,restricted,expected', [
    ('', AUDIO, True, 'external_attachment'),
    ('', VIDEO, True, 'external_attachment'),
    ('Own text', '', True, 'legacy'),
    ('', '', False, 'legacy'),
])
def test_whole_post_marker_does_not_override_other_cases(tmp_path, text, media, restricted, expected):
    def response(request):
        if request.url.host == 'cdn.example':
            return httpx.Response(200, content=b'poster', headers={'content-type':'image/jpeg'})
        return httpx.Response(200, text=unavailable_page(int(request.url.params.get('before', '0')), text, media, restricted))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        content = client.get('/api/v3/materials/'+settled(client)[0]['id']).json()['content']
        assert content['post_web_unavailable'] is False
        assert content['completeness']['reason'] == expected
        if media:
            assert content['external_attachments'][0]['kind'] == ('audio' if media == AUDIO else 'video')
        else:
            assert client.get('/api/v3/status').json()['problems']['count'] > 0


def test_previous_local_text_is_kept_when_whole_post_becomes_restricted(tmp_path):
    restricted = [False]
    def response(request):
        return httpx.Response(200, text=unavailable_page(int(request.url.params.get('before', '0')), '' if restricted[0] else 'Saved own text', restricted=restricted[0]))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        url = '/api/v3/materials/'+settled(client)[0]['id']
        assert client.get(url).json()['content']['text'] == 'Saved own text'
        restricted[0] = True
        client.post(url+'/retry'); settled(client)
        content = client.get(url).json()['content']
        assert content['text'] == 'Saved own text'
        assert content['post_web_unavailable'] is True
        assert content['changed'] is False
        assert client.get('/api/v3/status').json()['problems']['count'] == 0


def test_later_content_is_collected_and_previous_version_keeps_post_restriction(tmp_path):
    text = ['']
    def response(request):
        return httpx.Response(200, text=unavailable_page(int(request.url.params.get('before', '0')), text[0], restricted=not text[0]))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        url = '/api/v3/materials/'+settled(client)[0]['id']
        text[0] = 'Now provided by Telegram'
        client.post('/api/v3/collect')
        for _ in range(150):
            content = client.get(url).json()['content']
            if content['text'] == text[0] and not content['loading']: break
            time.sleep(.02)
        assert content['text'] == text[0]
        assert content['post_web_unavailable'] is False
        assert content['completeness']['state'] == 'complete'
        old = client.get(content['previous_versions'][0]['url']).json()
        assert old['post_web_unavailable'] is True
        assert old['text'] == ''
        assert old['external_attachments'] == []
        assert old['completeness']['reason'] == 'post_web_unavailable'
        assert client.get('/api/v3/status').json()['problems']['count'] == 0


def test_unknown_unsupported_post_is_reclassified_only_after_source_confirmation(tmp_path):
    confirmed = [False]
    def response(request):
        return httpx.Response(200, text=unavailable_page(int(request.url.params.get('before', '0')), restricted=confirmed[0]))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        url = '/api/v3/materials/'+settled(client)[0]['id']
        assert client.get(url).json()['content']['post_web_unavailable'] is False
        assert client.get('/api/v3/status').json()['problems']['count'] > 0
        confirmed[0] = True
        client.post(url+'/retry'); settled(client)
        assert client.get(url).json()['content']['post_web_unavailable'] is True
        assert client.get(url).json()['content']['changed'] is False
        assert client.get('/api/v3/status').json()['problems']['count'] == 0


@pytest.mark.parametrize('lost',[False,True])
def test_whole_post_restriction_keeps_local_video_but_does_not_hide_lost_file(tmp_path,lost):
    restricted = [False]
    def response(request):
        if request.url.host == 'cdn.example':
            return httpx.Response(200,content=b'saved-video',headers={'content-type':'video/mp4'})
        media = '' if restricted[0] else '<a class="tgme_widget_message_video_player"><video src="https://cdn.example/video.mp4"></video></a>'
        return httpx.Response(200,text=unavailable_page(int(request.url.params.get('before','0')),media=media,restricted=restricted[0]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        url='/api/v3/materials/'+settled(client)[0]['id']
        video=client.get(url).json()['content']['attachments'][0]
        if lost:
            for path in tmp_path.rglob(video['url'].rsplit('/',1)[-1]):path.unlink()
        restricted[0]=True
        client.post(url+'/retry');settled(client)
        content=client.get(url).json()['content']
        if lost:
            assert client.get('/api/v3/status').json()['problems']['count']>0
        else:
            assert content['post_web_unavailable'] is True
            assert client.get(content['attachments'][0]['url']).content==b'saved-video'
            assert content['changed'] is False
            assert client.get('/api/v3/status').json()['problems']['count']==0


def test_restricted_post_does_not_hide_neighbour_failure_or_block_explicit_viewed(tmp_path):
    def response(request):
        if request.url.host == 'cdn.example': return httpx.Response(404)
        html = external_page([(11,''),(12,'Broken file<audio src="https://cdn.example/audio.ogg"></audio>')],before=int(request.url.params.get('before','0')))
        return httpx.Response(200,text=mark_post_unavailable(html,'fixture/11'))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        cards=settled(client,2)
        post=next(c for c in cards if c['post_web_unavailable'])
        broken=next(c for c in cards if not c['post_web_unavailable'])
        problems=client.get('/api/v3/status').json()['problems']
        affected={obj['material_id'] for p in problems['current'] for obj in p['affected'] if obj['kind']=='material'}
        assert post['id'] not in affected
        assert broken['id'] in affected
        assert client.post('/api/v3/materials/'+post['id']+'/viewed').status_code==200
        assert client.get('/api/v3/materials/'+post['id']).status_code==404
        assert client.get('/api/v3/materials/'+broken['id']).status_code==200
