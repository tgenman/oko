from tests.support.youtube import VIDEO, PNG, subscribe, feed_xml
import json
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from oko.reader.app import create_app
from tests.support.models import model_transport

HEADERS = {'Authorization': 'Bearer test'}
# Valid synthetic thumbnail, not a downloaded video frame.


def test_reader_gets_captions_through_owned_worker(tmp_path, monkeypatch):
    # Replace only the external SDK in the child; run the real captions pipe.
    import os
    package = tmp_path / 'sdk' / 'youtube_transcript_api'
    package.mkdir(parents=True)
    (package / '_errors.py').write_text('class TranscriptsDisabled(Exception): pass\nclass NoTranscriptFound(Exception): pass\n')
    (package / '__init__.py').write_text('''
from types import SimpleNamespace
class Track:
    language_code = 'ru'
    is_generated = False
    def fetch(self):
        return [SimpleNamespace(text='Текст через настоящий worker.')]
class YouTubeTranscriptApi:
    def __init__(self, http_client): pass
    def list(self, video):
        assert video == 'abcdefghijk'
        return [Track()]
''')
    monkeypatch.setenv('PYTHONPATH', str(package.parent) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    with TestClient(create_app(tmp_path / 'data', token='test', rss_enabled=True, rss_transport=transport([])), headers=HEADERS) as client:
        feed = subscribe(client)
        client.patch('/api/feeds/' + feed, json={'importance': 'high'})
        client.post('/api/tabs/reader/heartbeat')
        material = wait(client, lambda m: m.get('captions') == 'available')
        assert material['text'] == 'Текст через настоящий worker.'
        client.post('/api/materials/' + material['id'] + '/viewed')
        assert client.get('/api/cards').json() == []


def transport(calls):
    def reply(request):
        calls.append(str(request.url))
        if request.url.path == '/feeds/videos.xml':
            return httpx.Response(200, text=feed_xml())
        if request.url.host == 'i.ytimg.com':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        raise AssertionError('Video/audio must never be downloaded: ' + str(request.url))
    return httpx.MockTransport(reply)


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
    from oko.collection.contracts import SourceError
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
    from tests.support.codex import CodexService
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


def test_owned_caption_worker_preserves_original_exception_in_debug(tmp_path, monkeypatch):
    import os
    package = tmp_path / 'sdk' / 'youtube_transcript_api'
    package.mkdir(parents=True)
    (package / '_errors.py').write_text('class TranscriptsDisabled(Exception): pass\nclass NoTranscriptFound(Exception): pass\n')
    (package / '__init__.py').write_text('''
class YouTubeTranscriptApi:
    def __init__(self, http_client): pass
    def list(self, video):
        raise RuntimeError('SDK failed password=private-caption-key')
''')
    monkeypatch.setenv('PYTHONPATH', str(package.parent) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    with TestClient(create_app(tmp_path / 'data', token='test', rss_enabled=True, rss_transport=transport([])), headers=HEADERS) as client:
        feed = subscribe(client)
        client.patch('/api/feeds/' + feed, json={'importance': 'high'})
        client.post('/api/tabs/reader/heartbeat')
        wait(client, lambda m: m.get('captions') == 'error')
        groups = client.get('/api/debug').json()['groups']
        matching = [g for g in groups if 'SDK failed' in g['message']]
        assert len(matching) == 1 and matching[0]['count'] == 1
        assert matching[0]['error_type'] == 'RuntimeError'
        assert 'Worker traceback' in matching[0]['traceback']
        assert 'private-caption-key' not in str(groups)


def test_youtube_added_through_checked_url_uses_existing_collector(tmp_path):
    captions = Captions()
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=captions), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        url = 'https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw'
        checked = client.post('/api/feeds/verify', json={'url': url}).json()
        added = client.post('/api/feeds', json={'url': url, 'verification': checked['verification'], 'title': '3Blue1Brown', 'folder': 'Учёба'})
        assert added.status_code == 200, added.text
        feed = added.json()
        assert feed['kind'] == feed['transport'] == 'youtube'
        client.patch('/api/feeds/' + feed['id'], json={'importance': 'high'})
        material = wait(client, lambda m: m.get('captions') == 'available')
        assert material['url'] == f'https://www.youtube.com/watch?v={VIDEO}'
        assert captions.calls == [VIDEO]
