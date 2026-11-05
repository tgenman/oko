"""Public web collection through the reader, without any Telegram configuration."""
from tests.support.publications import body_text,collected,reasons
from tests.support.telegram import configure_fast_web
import time

import httpx
import pytest

from tests.support.app import create_app
from tests.support.telegram import settled
from tests.support.v3 import TestClient


from tests.support.telegram_web import page


def test_web_connect_collects_text_and_links(tmp_path):
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
        material = client.get('/api/v3/publication/'+cards[0]['id']).json()
        assert body_text(material) == 'Hello\nWorld [article](https://example.org/article?a=1&b=2)'
        assert collected(material)
        assert all(url.startswith('https://t.me/') for url in requests)


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
        material = client.get('/api/v3/publication/'+settled(client)[0]['id']).json()
        assert collected(material)
        assert len([media for media in material['media'] if media['url'] is not None]) == 1
        attachment = [media for media in material['media'] if media['url'] is not None][0]
        assert attachment['kind'] == 'image'
        assert client.get(attachment['url']).content == b'fixture-photo'
        assert any(url.startswith('https://cdn.example/') for url,_,_ in requests)
        assert all(auth is None and cookie is None for _,auth,cookie in requests)
    assert not list((tmp_path/'staging').rglob('incoming-*'))


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
        material = client.get('/api/v3/publication/'+settled(client)[0]['id']).json()
        assert not collected(material)
        assert any('Небезопасный адрес' in reason for reason in reasons(material))
        assert not [media for media in material['media'] if media['url'] is not None]
        assert '127.0.0.1' not in calls


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
        material = client.get('/api/v3/publication/'+settled(client)[0]['id']).json()
        assert not collected(material) and any('Небезопасный адрес' in reason for reason in reasons(material))
        assert not connections


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
        assert body_text(client.get('/api/v3/publication/'+card['id']).json()) == 'Valid text'
        state = client.get('/api/v3/status/details').json()
        assert any(history['history']['progress']['warnings'] for history in state['initial_histories'])


def test_web_parsing_does_not_hold_up_an_unrelated_async_callback(monkeypatch):
    import asyncio
    import threading
    from oko.collection.telegram import web

    async def check():
        loop = asyncio.get_running_loop()
        release, callbacks = threading.Event(), []
        original = web.parse_page
        def parsing(*args, **kwargs):
            loop.call_soon_threadsafe(callbacks.append, True)
            loop.call_soon_threadsafe(release.set)
            fallback = threading.Timer(2, release.set)
            fallback.start()
            try:
                assert release.wait(3)
                assert callbacks, 'Unrelated callback could not run while parsing was held'
                return original(*args, **kwargs)
            finally:
                fallback.cancel()
                fallback.join()
        monkeypatch.setattr(web, 'parse_page', parsing)
        result, _ = await web.load('fixture', 0, httpx.MockTransport(lambda _: httpx.Response(200, text=page([(11, 'Text')]))))
        assert len(result.posts) == 1
    asyncio.run(check())
