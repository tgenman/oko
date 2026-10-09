import base64
import json
import time
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from oko.app import create_app
from tests.test_theses import model_transport

HEADERS = {'Authorization': 'Bearer test'}
CHANNEL = 'UC' + 'a' * 22
VIDEO = 'abcdefghijk'
# Valid synthetic thumbnail, not a downloaded video frame.
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAUAAAAC0CAIAAABqhmJGAAACJ0lEQVR4nO3TMQ0AIBDAwPcviwkBCMIDC2lyyQno0tlnAVHzvQB4ZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIRdj069LYHQXNAAAAAASUVORK5CYII=')


def feed_xml():
    stamp = datetime.now(timezone.utc).isoformat()
    return f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/">
    <entry><id>yt:video:{VIDEO}</id><yt:videoId>{VIDEO}</yt:videoId><title>О границах модели</title><published>{stamp}</published><link href="https://www.youtube.com/watch?v={VIDEO}"/>
    <media:group><media:thumbnail url="https://i.ytimg.com/vi/{VIDEO}/hqdefault.jpg"/></media:group></entry></feed>'''


def transport(calls):
    def reply(request):
        calls.append(str(request.url))
        if request.url.path == '/feeds/videos.xml':
            return httpx.Response(200, text=feed_xml())
        if request.url.host == 'i.ytimg.com':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        raise AssertionError('Video/audio must never be downloaded: ' + str(request.url))
    return httpx.MockTransport(reply)


def subscribe(client):
    opml = f'<opml><body><outline text="Наука"><outline text="Канал" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}"/></outline></body></opml>'
    first = client.post('/api/import', json={'opml': opml}).json()['feeds'][0]
    again = client.post('/api/import', json={'opml': opml}).json()['feeds']
    assert len(again) == 1 and again[0]['id'] == first['id']
    return first['id']


def wait(client, predicate):
    for _ in range(200):
        cards = client.get('/api/cards').json()
        if len(cards) == 1:
            material = client.get('/api/materials/' + cards[0]['id']).json()
            if predicate(material):
                return material
        time.sleep(.02)
    raise AssertionError(client.get('/api/status').json())


class Captions:
    def __init__(self):
        self.calls = []

    async def fetch(self, video):
        self.calls.append(video)
        return 'Модель принимает только текст. Источники сохраняются точно.'


def test_normal_feed_waits_for_mode_and_missing_captions_never_reach_ai(tmp_path):
    from oko.rss import SourceError
    class Missing(Captions):
        outcome = 'missing'
        async def fetch(self, video):
            self.calls.append(video)
            if self.outcome == 'error':
                raise SourceError('Ограничение частоты')
            return None if self.outcome == 'missing' else 'Проверенные субтитры.'
    captions, ai = Missing(), []
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=captions, ai_transport=model_transport(ai)), headers=HEADERS) as client:
        feed = subscribe(client)
        client.post('/api/tabs/reader/heartbeat')
        material = wait(client, lambda m: m.get('captions') == 'not_requested')
        assert not captions.calls and not ai
        client.patch('/api/settings/feed/' + feed, json={'theses': True, 'processor': 'llamacpp'})
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True})
        material = wait(client, lambda m: m.get('captions') == 'unavailable')
        assert material['reason'] == 'Субтитры недоступны' and material['text'] == ''
        assert material['ai'] is None and not ai
        captions.outcome = 'error'
        client.post('/api/materials/' + material['id'] + '/retry')
        material = wait(client, lambda m: m.get('captions') == 'error')
        assert 'Ограничение' in material['reason'] and not ai
        captions.outcome = 'ok'
        client.post('/api/materials/' + material['id'] + '/retry')
        material = wait(client, lambda m: m.get('theses'))
        assert material['text'] == 'Проверенные субтитры.'


def test_high_imported_youtube_gets_captions_theses_and_deletes_everything(tmp_path):
    captions, downloads, ai = Captions(), [], []
    kwargs = dict(token='test', rss_enabled=True, rss_transport=transport(downloads),
                  youtube_captions=captions, ai_transport=model_transport(ai))
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        feed = subscribe(client)
        client.patch('/api/feeds/' + feed, json={'importance': 'high'})
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'prompt_theses': 'Сохрани границы.'})
        client.post('/api/tabs/reader/heartbeat')
        material = wait(client, lambda m: m.get('theses'))
        assert material['text'] == 'Модель принимает только текст. Источники сохраняются точно.'
        assert material['url'] == f'https://www.youtube.com/watch?v={VIDEO}'
        assert captions.calls == [VIDEO]

        chat = next(r for r in ai if r.url.path == '/v1/chat/completions')
        assert 'Сохрани границы.' in json.loads(chat.content)['messages'][0]['content']
        image = material['images'][0]
        assert client.get(image).content == PNG
        assert client.post('/api/materials/' + material['id'] + '/viewed').status_code == 200
        assert client.get(image).status_code == 404
        assert not list((tmp_path / 'content').rglob('*.*'))
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        time.sleep(.2)
        assert client.get('/api/cards').json() == []
        assert captions.calls == [VIDEO]


def test_codex_permission_and_ready_captions_survive_restart(tmp_path):
    from tests.test_codex import CodexService
    captions, codex = Captions(), CodexService()
    kwargs = dict(token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=captions, codex_client=codex)
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        feed = subscribe(client)
        client.patch('/api/feeds/' + feed, json={'importance': 'high'})
        client.post('/api/tabs/reader/heartbeat')
        material = wait(client, lambda m: (m.get('ai') or {}).get('error'))
        assert 'Запрещена публичная' in material['ai']['error']
        assert not codex.inputs
        client.patch('/api/feeds/' + feed, json={'public_ai': True})
        material = wait(client, lambda m: m.get('theses'))
        item = material['id']
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        material = wait(client, lambda m: m.get('theses'))
        assert material['id'] == item and captions.calls == [VIDEO]
        assert len(codex.inputs) == 1



@pytest.mark.parametrize('action', ['viewed', 'stop', 'close'])
def test_late_captions_cannot_publish_after_reader_action(tmp_path, action):
    import asyncio
    import threading
    class Late(Captions):
        started = threading.Event()
        release = threading.Event()
        async def fetch(self, video):
            self.started.set()
            while not self.release.is_set():
                try:
                    await asyncio.sleep(.01)
                except asyncio.CancelledError:
                    pass
            return 'Late captions must not return.'
    captions = Late()
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=captions, lease=.1, grace=.5), headers=HEADERS) as client:
        feed = subscribe(client)
        client.patch('/api/feeds/' + feed, json={'importance': 'high'})
        client.post('/api/tabs/reader/heartbeat')
        assert captions.started.wait(2)
        item = client.get('/api/cards').json()[0]['id']
        if action == 'viewed':
            client.post('/api/materials/' + item + '/viewed')
        elif action == 'stop':
            client.post('/api/stop')
        else:
            client.post('/api/tabs/reader/close')
            time.sleep(.15)
        captions.release.set()
        time.sleep(.05)
        assert not list((tmp_path / 'content').rglob('image-*'))
        assert not any('Late captions' in p.read_text() for p in (tmp_path / 'content').rglob('*.md'))
