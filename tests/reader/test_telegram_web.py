"""Public web collection through the reader, without any Telegram configuration."""
from tests.support.telegram import configure_fast_web
from oko.collection.contracts import DiscoveryResult, FetchResult
import time

import httpx
import pytest

from tests.support.app import create_app
from tests.support.telegram import settled
from tests.support.v3 import TestClient


from tests.support.telegram_web import page


def test_web_connect_collects_text_and_links_without_client_or_read_sync(tmp_path):
    requests = []
    def response(request):
        requests.append(str(request.url))
        return httpx.Response(200,text=page([(11,'Hello<br>World <a href="https://example.org/article?a=1&amp;b=2">article</a>')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        connected = client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        assert connected.status_code == 200, connected.text
        cards = settled(client)
        material = client.get('/api/v3/materials/'+cards[0]['id']).json()
        assert material['content']['text'] == 'Hello\nWorld [article](https://example.org/article?a=1&b=2)'
        assert (material['content']['completeness']['state']=='complete') is True
        assert client.get('/api/v3/status').json()['telegram_reads'] == []
        assert all(url.startswith('https://t.me/') for url in requests)


def test_web_catchup_keeps_old_frontier_and_resumes_remaining_pages_after_restart(tmp_path):
    import asyncio
    import threading
    phase = [0]
    held = threading.Event()
    calls = []
    async def response(request):
        before = int(request.url.params.get('before','0'))
        calls.append((phase[0],before))
        if phase[0] == 1 and before == 5:
            held.set()
            await asyncio.sleep(30)
        maximum = 1 if phase[0] == 0 else 6
        ids = [i for i in range(1,maximum+1) if not before or i<before][-2:]
        more = min(ids) if ids and min(ids)>1 else None
        return httpx.Response(200,text=page([(i,'Message '+str(i)) for i in ids],more))
    options = dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response))
    headers = {'Authorization':'Bearer test'}
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox',
                    initial_history={'mode':'count','count':'1'}))
        old = settled(client)[0]['id']
        client.post('/api/v3/materials/'+old+'/viewed')
        phase[0] = 1
        client.post('/api/v3/collect')
        assert held.wait(3)
    phase[0] = 2
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        configure_fast_web(client)
        cards = settled(client,5)
        assert {m for c in cards for m in [int(identity) for identity in c['source']['message_ids']]} == {2,3,4,5,6}
        assert len({c['id'] for c in cards}) == 5
        assert client.get('/api/v3/materials/'+old).status_code == 404
        assert (2,5) in calls and (2,3) in calls
        assert client.get('/api/v3/status').json()['telegram_reads'] == []


def test_recorded_album_anchors_are_one_web_publication(tmp_path):
    from pathlib import Path
    fixture = Path(__file__).parents[1]/'fixtures/telegram'
    from bs4 import BeautifulSoup
    document = BeautifulSoup((fixture/'album.html').read_bytes(), 'html.parser')
    # Keep the recorded target album intact, without downloading unrelated posts.
    for message in document.select('.tgme_widget_message[data-post]'):
        if str(message['data-post']) != 'durov/373':
            message.decompose()
    album = str(document).encode()
    empty = (fixture/'empty.html').read_bytes()
    async def response(request):
        if request.url.host not in ('t.me','telegram.me'):
            return httpx.Response(200,content=b'fixture-recorded-media')
        before = int(request.url.params.get('before','0'))
        return httpx.Response(200,content=empty if before and before<=373 else album)
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        result = client.post('/api/v3/telegram/connect',json=dict(source_locator='@durov',folder='Inbox',initial_history={'mode':'count','count':'1'}))
        assert result.status_code == 200, result.text
        cards = settled(client,1,timeout=10)
        albums = [c for c in cards if 373 in [int(identity) for identity in c['source']['message_ids']]]
        assert len(albums) == 1
        assert [int(identity) for identity in albums[0]['source']['message_ids']] == list(range(373,382))
        assert not any([int(identity) for identity in c['source']['message_ids']]==[i] for c in cards for i in range(374,382))
        material = client.get('/api/v3/materials/'+albums[0]['id']).json()
        assert (material['content']['completeness']['state']=='complete') is True
        assert sum(a['kind']=='video' for a in material['content']['attachments']) == 9


def test_web_media_is_downloaded_locally_with_no_reader_credentials(tmp_path):
    requests = []
    photo = '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg?signature=first\')"></a>'
    async def response(request):
        requests.append((str(request.url),request.headers.get('authorization'),request.headers.get('cookie')))
        if request.url.host == 'cdn.example':
            return httpx.Response(200,content=b'fixture-photo',headers={'content-type':'image/jpeg'})
        return httpx.Response(200,text=page([(11,'Photo'+photo)]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        material = client.get('/api/v3/materials/'+settled(client)[0]['id']).json()
        assert (material['content']['completeness']['state']=='complete') is True
        assert len(material['content']['attachments']) == 1
        attachment = material['content']['attachments'][0]
        assert attachment['kind'] == 'image'
        assert client.get(attachment['url']).content == b'fixture-photo'
        assert any(url.startswith('https://cdn.example/') for url,_,_ in requests)
        assert all(auth is None and cookie is None for _,auth,cookie in requests)
    assert not list((tmp_path/'staging').rglob('incoming-*'))


def test_media_completion_is_not_author_edit_but_changed_known_bytes_are(tmp_path):
    phase = [0]
    async def response(request):
        if request.url.host == 'cdn.example':
            return httpx.Response(503) if phase[0]==0 else httpx.Response(200,content=b'first' if phase[0]<3 else b'changed')
        photo = '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/'+('photo-edited' if phase[0]>=3 else 'photo')+'?signature='+str(phase[0])+'\')"></a>'
        return httpx.Response(200,text=page([(11,'Photo'+photo)]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        deadline = time.monotonic()+2
        failed = None
        while time.monotonic()<deadline:
            cards = client.get('/api/v3/cards').json()
            if cards:
                failed = client.get('/api/v3/materials/'+cards[0]['id']).json()
                if 'Не удалось загрузить' in failed['content'].get('completeness',{}).get('message',''):
                    break
            time.sleep(.02)
        assert failed and 'Не удалось загрузить' in failed['content']['completeness'].get('message','')
        identity = failed['id']
        phase[0] = 1
        assert client.post('/api/v3/materials/'+identity+'/retry').status_code == 200
        settled(client)
        loaded = client.get('/api/v3/materials/'+identity).json()
        assert (loaded['content']['completeness']['state']=='complete') is True
        assert loaded['content']['revision_id'] == failed['content']['revision_id']
        assert loaded['content']['changed'] is False
        assert 'previous_versions' not in loaded['content']
        name = loaded['content']['attachments'][0]['url'].split('/')[-1]
        phase[0] = 2
        client.post('/api/v3/materials/'+identity+'/retry')
        settled(client)
        rotated = client.get('/api/v3/materials/'+identity).json()
        assert rotated['content']['revision_id'] == loaded['content']['revision_id']
        assert rotated['content']['attachments'][0]['url'].split('/')[-1] == name
        phase[0] = 3
        client.post('/api/v3/materials/'+identity+'/retry')
        settled(client)
        changed = client.get('/api/v3/materials/'+identity).json()
        assert changed['content']['revision_id'] != loaded['content']['revision_id']
        assert 'previous_versions' not in changed['content']


def test_media_redirect_cannot_reach_a_local_service(tmp_path):
    calls = []
    async def response(request):
        calls.append(request.url.host)
        if request.url.host == 'cdn.example':
            return httpx.Response(302,headers={'location':'http://127.0.0.1:80/private'})
        assert request.url.host == 't.me'
        return httpx.Response(200,text=page([(11,'Photo<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/start\')"></a>')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        material = client.get('/api/v3/materials/'+settled(client)[0]['id']).json()
        assert (material['content']['completeness']['state']=='complete') is False
        assert 'Небезопасный адрес' in material['content']['completeness'].get('message','')
        assert not material['content']['attachments']
        assert '127.0.0.1' not in calls


def test_stream_without_length_stops_at_200_mib_without_automatic_retry(tmp_path):
    count = [0]
    class Huge(httpx.AsyncByteStream):
        async def __aiter__(self):
            chunk = b'x'*(1024*1024)
            for _ in range(202):
                count[0] += 1
                yield chunk
    async def response(request):
        if request.url.host == 'cdn.example':
            return httpx.Response(200,stream=Huge())
        return httpx.Response(200,text=page([(11,'Video<video src="https://cdn.example/large"></video>')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        deadline = time.monotonic()+5
        material = None
        while time.monotonic()<deadline:
            cards = client.get('/api/v3/cards').json()
            if cards:
                material = client.get('/api/v3/materials/'+cards[0]['id']).json()
                if '200 МиБ' in material['content'].get('completeness',{}).get('message',''):
                    break
            time.sleep(.02)
        assert material and '200 МиБ' in material['content']['completeness'].get('message','')
        assert (material['content']['completeness']['state']=='complete') is False and not material['content']['attachments']
        assert count[0] == 201
        time.sleep(.1)
        assert count[0] == 201
    assert not list((tmp_path/'staging').rglob('incoming-*'))


def test_dns_private_answer_is_rejected_before_connecting(tmp_path,monkeypatch):
    import httpcore
    import socket
    from oko.collection.telegram.media import PublicTransport
    connections = []
    def resolve(host,port,*args,**kwargs):
        return [(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',('127.0.0.1',port))]
    async def connect(*args,**kwargs):
        connections.append(args)
        raise AssertionError('must not open a private socket')
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    monkeypatch.setattr(httpcore.AnyIOBackend,'connect_tcp',connect)
    class FixtureTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.public = PublicTransport()
        async def handle_async_request(self,request):
            if request.url.host != 't.me':
                return await self.public.handle_async_request(request)
            return httpx.Response(200,text=page([(11,'Photo<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://private.example/photo\')"></a>')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=FixtureTransport()),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        material = client.get('/api/v3/materials/'+settled(client)[0]['id']).json()
        assert (material['content']['completeness']['state']=='complete') is False and 'Небезопасный адрес' in material['content']['completeness'].get('message','')
        assert not connections


@pytest.mark.parametrize('telegraph',[False,True])
def test_failed_oversized_refresh_keeps_file_and_exposes_manual_problem(tmp_path,telegraph):
    phase = [0]
    calls = [0]
    async def response(request):
        if request.url.host == 'api.telegra.ph':
            return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[{'tag':'p','children':['Saved article']}]}})
        if request.url.host == 'cdn.example':
            calls[0] += 1
            return httpx.Response(200,headers={'content-length':str(201*1024*1024)}) if phase[0] else httpx.Response(200,content=b'known-file')
        return httpx.Response(200,text=page([(11,('Photo https://telegra.ph/Article ' if telegraph else 'Photo')+'<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo\')"></a>')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        original = client.get('/api/v3/materials/'+settled(client)[0]['id']).json()
        phase[0] = 1
        client.post('/api/v3/materials/'+original['id']+'/retry')
        deadline = time.monotonic()+2
        while time.monotonic()<deadline:
            state = client.get('/api/v3/status').json()
            if any('200 МиБ' in error for error in [p['message'] for p in state['problems']['current'] if p['code'] in ('source_unavailable','material_save_failed','material_read_failed')]):
                break
            time.sleep(.02)
        else:
            raise AssertionError('Oversized failed refresh was hidden')
        current = client.get('/api/v3/materials/'+original['id']).json()
        assert client.get(current['content']['attachments'][0]['url']).content == b'known-file'
        assert 'previous_versions' not in current['content'] and current['content']['revision_id']==original['content']['revision_id']
        assert any('200 МиБ' in str(problem) for problem in state['problems']['current'])
        assert any(intent['kind']=='retry_material' for p in state['problems']['current'] for intent in p['recovery'])
        from tests.support.reader import wait_for
        wait_for(client,lambda:not any(work['phase']=='collecting' for work in client.get('/api/v3/status').json()['work']))
        client.post('/api/v3/collect')
        time.sleep(.7)
        assert calls[0] == 2
        client.post('/api/v3/materials/'+original['id']+'/retry')
        wait_for(client,lambda:calls[0]==3)
        deadline = time.monotonic()+2
        while time.monotonic()<deadline and any(work['phase']=='collecting' for work in client.get('/api/v3/status').json()['work']):
            time.sleep(.02)
        assert calls[0] == 3


def test_album_first_part_deletion_and_new_url_keep_one_card_and_tombstone(tmp_path):
    phase = [0]
    async def response(request):
        first = 11 if phase[0]==0 else 12
        anchors = ''.join(f'<a class="grouped_media_wrap" href="https://t.me/fixture/{i}?single"></a>' for i in ([11,12] if phase[0]==0 else [12,13]))
        return httpx.Response(200,text=page([(first,'Album caption'+anchors)]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox',initial_history={'mode':'count','count':'1'}))
        original = settled(client)[0]
        assert [int(identity) for identity in original['source']['message_ids']] == [11,12]
        phase[0] = 1
        client.post('/api/v3/collect')
        client.post('/api/v3/materials/'+original['id']+'/retry')
        deadline = time.monotonic()+4
        while time.monotonic()<deadline:
            cards = client.get('/api/v3/cards').json()
            if len(cards)==1 and [int(identity) for identity in cards[0]['source']['message_ids']]==[12,13] and not cards[0].get('loading'):
                break
            time.sleep(.02)
        assert len(cards)==1 and cards[0]['id']==original['id'] and [int(identity) for identity in cards[0]['source']['message_ids']]==[12,13]
        assert client.get('/api/v3/materials/'+original['id']).json()['source_url']=='https://t.me/fixture/12'
        client.post('/api/v3/materials/'+original['id']+'/viewed')
        client.post('/api/v3/collect')
        time.sleep(.1)
        assert client.get('/api/v3/cards').json()==[]




def test_html_quotes_poll_voice_sticker_and_unavailable_document_are_preserved(tmp_path):
    markup = '''<a class="tgme_widget_message_reply" href="https://t.me/fixture/1"><span class="tgme_widget_message_author_name">Reply author</span><div class="tgme_widget_message_text">Quoted text</div></a>
<div class="tgme_widget_message_forwarded_from"><a href="https://t.me/source">Forwarded source</a></div>
<div class="tgme_widget_message_poll"><div class="tgme_widget_message_poll_question">Choose?</div><div class="tgme_widget_message_poll_option">50% Yes</div></div>
<audio src="https://cdn.example/voice.ogg"></audio>
<i class="tgme_widget_message_sticker" data-webp="https://cdn.example/sticker.webp"></i>
<div class="tgme_widget_message_document_wrap"><div class="tgme_widget_message_document_title">report.pdf</div><div class="tgme_widget_message_document_extra">12 KB</div></div>
<div class="tgme_widget_message_video_player"><div class="tgme_widget_message_video_thumb" style="background-image:url('https://cdn.example/cover.jpg')"></div></div>'''
    async def response(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=b'fixture-media')
        return httpx.Response(200,text=page([(11,'Original<br>Paragraph'+markup)]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        material = client.get('/api/v3/materials/'+settled(client)[0]['id']).json()
        assert all(text in material['content']['text'] for text in ('Original','Paragraph','Forwarded source','Choose?','50% Yes','report.pdf','12 KB'))
        assert material['content']['reply_preview'] == dict(author='Reply author', text='Quoted text', source_url='https://t.me/fixture/1')
        assert 'Quoted text' not in material['content']['text']
        assert 'https://t.me/source' in material['content']['text']
        assert (material['content']['completeness']['state']=='complete') is False
        assert material['content']['completeness']['reason']=='external_attachment'
        assert {a['kind'] for a in material['content']['external_attachments']}=={'video','document'}
        assert sorted(a['kind'] for a in material['content']['attachments'])==['audio','image','image']




def test_web_connect_view_and_remove_never_create_a_worker_or_register_read_sync(tmp_path,monkeypatch):
    from oko.collection.telegram.source import _TelegramWorker
    from oko.storage.read_sync import ReadSync
    def forbidden(*args,**kwargs):
        raise AssertionError('Web must never allocate a Telegram worker or register read eligibility')
    monkeypatch.setattr(_TelegramWorker,'__init__',forbidden)
    monkeypatch.setattr(ReadSync,'observed',forbidden)
    with TestClient(create_app(tmp_path,token='test',api_only=True,
            rss_transport=httpx.MockTransport(lambda r:httpx.Response(200,text=page([(11,'Public')])))),
            headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        connected=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Web'))
        assert connected.status_code==200,connected.text
        card=settled(client)[0]
        assert client.post('/api/v3/materials/'+card['id']+'/viewed').status_code==200
        assert client.get('/api/v3/status').json()['telegram_reads']==[]
        assert client.delete('/api/v3/feeds/'+connected.json()['feed_id']).status_code==200


def test_malformed_web_record_does_not_discard_valid_neighbor_and_is_diagnosed(tmp_path):
    from tests.support.publication_pipeline import publication_root
    tmp_path=publication_root(tmp_path)
    html = page([(11, 'Valid text'), (12, 'Broken identity')]).replace('data-post="fixture/12"', 'data-post="broken"')
    with TestClient(create_app(tmp_path, token='test', api_only=True,
            rss_transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html))),
            headers={'Authorization': 'Bearer test'}) as client:
        configure_fast_web(client)
        result = client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        assert result.status_code == 200, result.text
        card = settled(client)[0]
        assert client.get('/api/v3/materials/'+card['id']).json()['content']['text'] == 'Valid text'
        state = client.get('/api/v3/status').json()
        assert any(history['history']['progress']['warnings'] for history in state['initial_histories'])
