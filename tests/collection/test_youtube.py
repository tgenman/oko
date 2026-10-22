from tests.support.presence import presence, close_presence
from oko.collection.youtube.source import LocalCaptions
from tests.support.youtube import VIDEO, PNG, subscribe, feed_xml
import json
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.models import model_transport

HEADERS = {'Authorization': 'Bearer test'}
# Valid synthetic thumbnail, not a downloaded video frame.


def test_reader_gets_captions_through_owned_worker(tmp_path, monkeypatch):
    # Replace only the external SDK in the child; run the real captions pipe.
    import os
    package = tmp_path / 'sdk' / 'youtube_transcript_api'
    package.mkdir(parents=True)
    (package / '_errors.py').write_text('class TranscriptsDisabled(Exception): pass\nclass NoTranscriptFound(Exception): pass\nclass VideoUnplayable(Exception): pass\n')
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
    with TestClient(create_app(tmp_path / 'data', token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=LocalCaptions()), headers=HEADERS) as client:
        # The foreground reader must outlive the short setup session used by subscribe.
        # Otherwise closing setup legitimately cancels the in-flight captions request.
        presence(client, 'reader')
        feed = subscribe(client)
        # Default theses already requests captions. Changing importance here
        # can cancel the job started by the v3 connection and defer its retry.
        presence(client, 'reader')
        material = wait(client, lambda m: m['source'].get('captions') == 'available')
        assert material['content']['text'] == 'Текст через настоящий worker.'
        client.post('/api/v3/materials/' + material['id'] + '/viewed')
        assert client.get('/api/v3/cards').json() == []


def transport(calls):
    def reply(request):
        calls.append(str(request.url))
        if request.url.path == '/feeds/videos.xml':
            return httpx.Response(200, text=feed_xml())
        if request.url.host == 'i.ytimg.com':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        raise AssertionError('Video/audio must never be downloaded: ' + str(request.url))
    return httpx.MockTransport(reply)


def wait(client, predicate, *, keep_alive=False):
    for _ in range(200):
        if keep_alive:
            assert presence(client, 'reader').status_code==200
        cards = client.get('/api/v3/cards').json()
        if len(cards) == 1:
            material = client.get('/api/v3/materials/' + cards[0]['id']).json()
            if predicate(material):
                return material
        time.sleep(.02)
    raise AssertionError(client.get('/api/v3/status').json())


class Captions:
    def __init__(self):
        self.calls = []

    async def fetch(self, video):
        self.calls.append(video)
        return 'Модель принимает только текст. Источники сохраняются точно.'


def test_saved_captions_are_never_requested_again(tmp_path):
    captions, downloads = Captions(), []
    kwargs = dict(token='test', rss_enabled=True, rss_transport=transport(downloads), youtube_captions=captions)
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        feed = subscribe(client)
        client.patch('/api/v3/feeds/' + feed, json={'importance': 'high'})
        presence(client, 'reader')
        material = wait(client, lambda m: m['source'].get('captions') == 'available' and not m['content']['loading'])
        item, text = material['id'], material['content']['text']
        assert captions.calls == [VIDEO]
        # Both manual material reload and feed collection must use saved text.
        for endpoint in ('/api/v3/materials/' + item + '/retry', '/api/v3/collect'):
            before = len(downloads)
            assert client.post(endpoint).status_code == 200
            material = wait(client, lambda m: len(downloads) > before and not m['content']['loading'])
            assert material['source']['captions'] == 'available' and material['content']['text'] == text
            assert captions.calls == [VIDEO]
        client.patch('/api/v3/settings/feed/' + feed, json={'theses': False})
        client.patch('/api/v3/settings/feed/' + feed, json={'theses': True})
    # Persistence, rather than an in-memory cache, must prevent another request.
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'reader')
        before = len(downloads)
        assert client.post('/api/v3/materials/' + item + '/retry').status_code == 200
        material = wait(client, lambda m: len(downloads) > before and not m['content']['loading'])
        assert material['source']['captions'] == 'available' and material['content']['text'] == text
        assert captions.calls == [VIDEO]


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
    wall, tick = [time.time()], [100.]
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=captions, ai_transport=model_transport(ai), wall_clock=lambda: wall[0], clock=lambda: tick[0], lease=600), headers=HEADERS) as client:
        # Connecting starts collection; disable unrelated processing beforehand.
        assert client.patch('/api/v3/settings/oko/global', json={'theses': False}).status_code == 200
        feed = subscribe(client)
        client.patch('/api/v3/settings/feed/' + feed, json={'theses': False})
        presence(client, 'reader')
        material = wait(client, lambda m: m['source'].get('captions') == 'not_requested')
        assert not captions.calls and not ai
        client.patch('/api/v3/settings/feed/' + feed, json={'theses': True, 'processor': 'llamacpp'})
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True})
        material = wait(client, lambda m: m['source'].get('captions') == 'unavailable')
        assert material['content']['completeness'].get('message','') == 'Субтитры недоступны' and material['content']['text'] == ''
        assert material['theses']['result'] is None and not ai
        captions.outcome = 'error'
        # Advance both admission clocks; never wait for real rate-limit time.
        wall[0] += 10
        tick[0] += 10
        client.post('/api/v3/materials/' + material['id'] + '/retry')
        material = wait(client, lambda m: m['source'].get('captions') == 'error')
        assert 'Ограничение' in material['content']['completeness'].get('message','') and not ai
        captions.outcome = 'ok'
        # Advance both admission clocks; never wait for real rate-limit time.
        wall[0] += 10
        tick[0] += 10
        client.post('/api/v3/materials/' + material['id'] + '/retry')
        material = wait(client, lambda m: m['theses']['result'])
        assert material['content']['text'] == 'Проверенные субтитры.'


def test_high_imported_youtube_gets_captions_theses_and_deletes_everything(tmp_path):
    captions, downloads, ai = Captions(), [], []
    kwargs = dict(token='test', rss_enabled=True, rss_transport=transport(downloads),
                  youtube_captions=captions, ai_transport=model_transport(ai))
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        feed = subscribe(client)
        client.patch('/api/v3/feeds/' + feed, json={'importance': 'high'})
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp', 'prompt_theses': 'Сохрани границы.'})
        presence(client, 'reader')
        material = wait(client, lambda m: m['theses']['result'])
        assert material['content']['text'] == 'Модель принимает только текст. Источники сохраняются точно.'
        assert material['source_url'] == f'https://www.youtube.com/watch?v={VIDEO}'
        assert captions.calls == [VIDEO]

        chat = next(r for r in ai if r.url.path == '/v1/chat/completions')
        assert 'Сохрани границы.' in json.loads(chat.content)['messages'][0]['content']
        image = material['content']['images'][0]
        assert client.get(image).content == PNG
        assert client.post('/api/v3/materials/' + material['id'] + '/viewed').status_code == 200
        assert client.get(image).status_code == 404
        assert not list((tmp_path / 'content').rglob('*.*'))
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'reader')
        time.sleep(.2)
        assert client.get('/api/v3/cards').json() == []
        assert captions.calls == [VIDEO]


def test_codex_permission_and_ready_captions_survive_restart(tmp_path):
    from tests.support.codex import CodexService, configure_codex
    captions, codex = Captions(), CodexService()
    kwargs = dict(token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=captions, codex_client=codex)
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        configure_codex(client)
        feed = subscribe(client)
        client.patch('/api/v3/feeds/'+feed,json={'public_ai':False,'importance':'high'})
        client.patch('/api/v3/settings/oko/global',json={'theses':True})
        presence(client, 'reader')
        material = wait(client, lambda m: m['theses']['diagnostic_message'])
        assert 'Запрещена публичная' in material['theses']['diagnostic_message']
        assert not codex.inputs
        client.patch('/api/v3/feeds/' + feed, json={'public_ai': True})
        material = wait(client, lambda m: m['theses']['result'])
        item = material['id']
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'reader')
        material = wait(client, lambda m: m['theses']['result'])
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
    from tests.support.reader import Clock
    clock = Clock()
    captions = Late()
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=captions, lease=.1, grace=.5, clock=clock, wall_clock=clock), headers=HEADERS) as client:
        feed = subscribe(client)
        client.patch('/api/v3/feeds/' + feed, json={'importance': 'high'})
        presence(client, 'reader')
        assert captions.started.wait(2)
        item = client.get('/api/v3/cards').json()[0]['id']
        if action == 'viewed':
            client.post('/api/v3/materials/' + item + '/viewed')
        elif action == 'stop':
            client.post('/api/v3/stop')
        else:
            close_presence(client, 'reader')
            clock.now += .15
        captions.release.set()
        time.sleep(.05)
        assert not list((tmp_path / 'content').rglob('image-*'))
        assert not any('Late captions' in p.read_text() for p in (tmp_path / 'content').rglob('*.md'))


def test_owned_caption_worker_preserves_original_exception_in_debug(tmp_path, monkeypatch):
    import os
    package = tmp_path / 'sdk' / 'youtube_transcript_api'
    package.mkdir(parents=True)
    (package / '_errors.py').write_text('class TranscriptsDisabled(Exception): pass\nclass NoTranscriptFound(Exception): pass\nclass VideoUnplayable(Exception): pass\n')
    (package / '__init__.py').write_text('''
class YouTubeTranscriptApi:
    def __init__(self, http_client): pass
    def list(self, video):
        raise RuntimeError('SDK failed password=private-caption-key')
''')
    monkeypatch.setenv('PYTHONPATH', str(package.parent) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    with TestClient(create_app(tmp_path / 'data', token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=LocalCaptions()), headers=HEADERS) as client:
        feed = subscribe(client)
        client.patch('/api/v3/feeds/' + feed, json={'importance': 'high'})
        presence(client, 'reader')
        wait(client, lambda m: m['source'].get('captions') == 'error')
        groups = client.get('/api/v3/diagnostics').json()['groups']
        matching = [g for g in groups if 'SDK failed' in g['message']]
        assert len(matching) == 1 and matching[0]['count'] == 1
        assert matching[0]['error_type'] == 'RuntimeError'
        assert 'Worker traceback' in matching[0]['traceback']
        assert 'private-caption-key' not in str(groups)


def test_youtube_added_through_checked_url_uses_existing_collector(tmp_path):
    from tests.support.youtube import History
    captions = Captions()
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=captions, youtube_history=History([VIDEO])), headers=HEADERS) as client:
        presence(client, 'reader')
        url = 'https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw'
        checked = client.post('/api/v3/feeds/verify', json={'url': url}).json()
        added = client.post('/api/v3/feeds', json={'url': url, 'verification': checked['verification'], 'title': '3Blue1Brown', 'folder': 'Учёба'})
        assert added.status_code == 200, added.text
        feed = added.json()
        assert feed['kind'] == feed['transport'] == 'youtube'
        client.patch('/api/v3/feeds/' + feed['id'], json={'importance': 'high'})
        material = wait(client, lambda m: m['source'].get('captions') == 'available')
        assert material['source_url'] == f'https://www.youtube.com/watch?v={VIDEO}'
        assert captions.calls == [VIDEO]

@pytest.mark.parametrize('reason, skipped', [
    ('This live event will begin in 7 days.', True),
    ('This video is not available in your country.', False),
])
def test_future_live_is_discarded_but_other_unplayable_errors_remain(tmp_path, monkeypatch, reason, skipped):
    import os
    package = tmp_path / 'sdk' / 'youtube_transcript_api'
    package.mkdir(parents=True)
    calls = tmp_path / 'caption-calls'
    (package / '_errors.py').write_text('''
class TranscriptsDisabled(Exception): pass
class NoTranscriptFound(Exception): pass
class VideoUnplayable(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)
''')
    (package / '__init__.py').write_text(f'''
from pathlib import Path
from ._errors import VideoUnplayable
class YouTubeTranscriptApi:
    def __init__(self, http_client): pass
    def list(self, video):
        with Path({str(calls)!r}).open('a') as log:
            log.write(video + '\\n')
        raise VideoUnplayable({reason!r})
''')
    monkeypatch.setenv('PYTHONPATH', str(package.parent) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    data = tmp_path / 'data'
    kwargs = dict(token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=LocalCaptions())
    with TestClient(create_app(data, **kwargs), headers=HEADERS) as client:
        # Keep the real worker admitted throughout subscription setup. Default
        # theses requests captions; changing importance can cancel that attempt.
        presence(client, 'reader')
        subscribe(client)
        if not skipped:
            wait(client, lambda m: m['source'].get('captions') == 'error',keep_alive=True)
            assert any(g['error_type'] == 'VideoUnplayable' for g in client.get('/api/v3/diagnostics').json()['groups'])
            return
        for _ in range(200):
            # A live browser renews its lease while an SDK worker is pending.
            assert presence(client, 'reader').status_code==200
            if calls.exists() and client.get('/api/v3/cards').json() == []:
                break
            time.sleep(.02)
        else:
            raise AssertionError('Future live card was not discarded')
        assert client.get('/api/v3/diagnostics').json()['groups'] == []
        for _ in range(2):
            client.post('/api/v3/collect')
            time.sleep(.2)
            assert client.get('/api/v3/cards').json() == []
        assert calls.read_text().splitlines() == [VIDEO]
    with TestClient(create_app(data, **kwargs), headers=HEADERS) as client:
        presence(client, 'reader')
        client.post('/api/v3/collect')
        time.sleep(.2)
        assert client.get('/api/v3/cards').json() == []
        assert client.get('/api/v3/diagnostics').json()['groups'] == []
        assert calls.read_text().splitlines() == [VIDEO]


def test_default_theses_request_youtube_captions_without_high_importance(tmp_path):
    captions = Captions()
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=captions), headers=HEADERS) as client:
        feed = subscribe(client)
        settings = client.get('/api/v3/settings/feed/' + feed).json()
        assert settings['effective']['theses']['value'] is True
        assert settings['feed']['importance'] == 'normal'
        assert settings['feed']['public_ai'] is True
        presence(client, 'reader')
        material = wait(client, lambda m: m['source'].get('captions') == 'available' and not m['content']['loading'])
        assert material['content']['text'] == 'Модель принимает только текст. Источники сохраняются точно.'
        assert captions.calls == [VIDEO]
