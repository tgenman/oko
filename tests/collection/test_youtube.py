from tests.support.cards import read_all_cards
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
        assert material['text'] == 'Текст через настоящий worker.'
        client.post('/api/v3/materials/' + material['id'] + '/viewed')
        assert read_all_cards(client, '/api/v3/cards') == []


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
        cards = read_all_cards(client, '/api/v3/cards')
        if len(cards) == 1:
            material = client.get('/api/v3/publication/' + cards[0]['id']).json()
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


def test_saved_captions_are_reused_during_collection_and_after_restart(tmp_path):
    captions, downloads = Captions(), []
    kwargs = dict(token='test', rss_enabled=True, rss_transport=transport(downloads), youtube_captions=captions)
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        feed = subscribe(client)
        client.patch('/api/v3/feeds/' + feed, json={'importance': 'high'})
        presence(client, 'reader')
        material = wait(client, lambda m: m['source'].get('captions') == 'available' and not m['loading'])
        item, text = material['id'], material['text']
        assert captions.calls == [VIDEO]
        # Ordinary source refresh keeps saved captions; explicit Reload Publication
        # separately revalidates media, including subtitles (youtube-publications.md).
        before = len(downloads)
        assert client.post('/api/v3/collect').status_code == 200
        material = wait(client, lambda m: len(downloads) > before and not m['loading'])
        assert material['source']['captions'] == 'available' and material['text'] == text
        assert captions.calls == [VIDEO]
        client.patch('/api/v3/settings/feed/' + feed, json={'theses': False})
        client.patch('/api/v3/settings/feed/' + feed, json={'theses': True})
    # Persistence, rather than an in-memory cache, must prevent another request.
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'reader')
        before = len(downloads)
        assert client.post('/api/v3/collect').status_code == 200
        material = wait(client, lambda m: len(downloads) > before and not m['loading'])
        assert material['source']['captions'] == 'available' and material['text'] == text
        assert captions.calls == [VIDEO]



def test_explicit_reload_revalidates_saved_captions_after_source_interval(tmp_path):
    captions, downloads, shift = Captions(), [], [0.0]
    kwargs = dict(token='test', rss_enabled=True, rss_transport=transport(downloads), youtube_captions=captions,
                  lease=600, clock=lambda: time.monotonic()+shift[0], wall_clock=lambda: time.time()+shift[0])
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'reader')
        subscribe(client)
        material = wait(client, lambda m: m['source'].get('captions') == 'available' and not m['loading'])
        assert captions.calls == [VIDEO]
        assert client.post('/api/v3/materials/' + material['id'] + '/retry').status_code == 200
        shift[0] += 60
        updated = wait(client, lambda m: len(captions.calls) == 2 and not m['loading'])
        assert updated['text'] == material['text']
        assert captions.calls == [VIDEO, VIDEO]

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
        assert material['completeness']['message']=='Субтитры не запрошены'
        from tests.support.storage import database_url
        import psycopg
        with psycopg.connect(database_url(tmp_path)) as db:
            state=db.execute("SELECT m.status,j.id FROM media_files m LEFT JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=%s AND m.resource_key='youtube:subtitles'",(material['id'],)).fetchone()
        assert state == ('pending_download',None)
        client.patch('/api/v3/settings/feed/' + feed, json={'theses': True, 'processor': 'llamacpp'})
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True})
        material = wait(client, lambda m: m['source'].get('captions') == 'unavailable' and m['theses']['state']=='ready')
        assert material['completeness'].get('message','') == 'Субтитры недоступны' and material['text'] == ''
        assert material['theses']['result']['points']==[] and not ai
        with psycopg.connect(database_url(tmp_path)) as db:
            reason = db.execute("SELECT j.reason_code, j.reason_message FROM jobs j JOIN media_files m ON m.id=j.item WHERE j.kind='download_media' AND m.publication_id=%s AND m.resource_key='youtube:subtitles'", (material['id'],)).fetchone()
        assert reason == ('subtitles_absent', 'Субтитры недоступны')
        source = next(s for s in client.get('/api/v3/collection-status').json()['sources'] if s['feed_id'] == feed)
        assert source['error_count'] == 0
        assert not any(op['state'] == 'error' for op in client.get('/api/v3/collection-status/' + feed).json()['operations'])
        with psycopg.connect(database_url(tmp_path),autocommit=True) as db:
            db.execute("UPDATE jobs SET reason_message='Confirmed no captions' WHERE kind='download_media' AND item=(SELECT id FROM media_files WHERE publication_id=%s AND resource_key='youtube:subtitles')",(material['id'],))
        assert client.get('/api/v3/publication/'+material['id']).json()['completeness']['message']=='Confirmed no captions'
        with psycopg.connect(database_url(tmp_path),autocommit=True) as db:
            db.execute("UPDATE jobs SET wait_scope='youtube:captions',retry_at=%s,next=%s WHERE kind='download_media' AND item=(SELECT id FROM media_files WHERE publication_id=%s AND resource_key='youtube:subtitles')",(wall[0]+100,tick[0]+100,material['id']))
        captions.outcome = 'error'
        # Advance both admission clocks; never wait for real rate-limit time.
        client.post('/api/v3/materials/' + material['id'] + '/retry')
        with psycopg.connect(database_url(tmp_path)) as db:
            pending=db.execute("SELECT m.status,j.status,p.content #>> '{extra,youtube,captions}' FROM media_files m JOIN jobs j ON j.item=m.id AND j.kind='download_media' JOIN publications p ON p.id=m.publication_id WHERE m.publication_id=%s AND m.resource_key='youtube:subtitles'",(material['id'],)).fetchone()
        assert pending==('pending_download','pending','pending')
        assert client.get('/api/v3/publication/'+material['id']).json()['source'].get('captions') == 'pending'
        with psycopg.connect(database_url(tmp_path),autocommit=True) as db:
            db.execute("UPDATE jobs SET status='running' WHERE kind='download_media' AND item=(SELECT id FROM media_files WHERE publication_id=%s AND resource_key='youtube:subtitles')",(material['id'],))
        assert client.get('/api/v3/publication/'+material['id']).json()['source'].get('captions') == 'running'
        with psycopg.connect(database_url(tmp_path),autocommit=True) as db:
            db.execute("UPDATE jobs SET status='failed',reason_code='network',reason_message='Network error' WHERE kind='download_media' AND item=(SELECT id FROM media_files WHERE publication_id=%s AND resource_key='youtube:subtitles')",(material['id'],))
        assert client.get('/api/v3/publication/'+material['id']).json()['source'].get('captions') == 'error'
        with psycopg.connect(database_url(tmp_path),autocommit=True) as db:
            db.execute("UPDATE jobs SET status='pending',reason_code=NULL,reason_message=NULL WHERE kind='download_media' AND item=(SELECT id FROM media_files WHERE publication_id=%s AND resource_key='youtube:subtitles')",(material['id'],))
        wall[0] += 110
        tick[0] += 110
        material = wait(client, lambda m: m['source'].get('captions') == 'pending' and 'Ограничение' in m['completeness'].get('message',''))
        assert 'Ограничение' in material['completeness'].get('message','') and not ai
        captions.outcome = 'ok'
        # Advance both admission clocks; never wait for real rate-limit time.
        wall[0] += 10
        tick[0] += 10
        client.post('/api/v3/materials/' + material['id'] + '/retry')
        wait(client,lambda m:m['source'].get('captions')=='available')
        tick[0]+=1
        material = wait(client, lambda m: m['theses']['result'])
        assert material['text'] == 'Проверенные субтитры.'


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
        assert material['text'] == 'Модель принимает только текст. Источники сохраняются точно.'
        assert material['source_url'] == f'https://www.youtube.com/watch?v={VIDEO}'
        assert captions.calls == [VIDEO]

        chat = next(r for r in ai if r.url.path == '/v1/chat/completions')
        assert 'Сохрани границы.' in json.loads(chat.content)['messages'][0]['content']
        image = next(media['url'] for media in [media for media in material['media'] if media['role'] == 'attachment'] if media['kind']=='image')
        assert client.get(image).content == PNG
        assert client.post('/api/v3/materials/' + material['id'] + '/viewed').status_code == 200
        assert client.get(image).status_code == 404
        assert not list((tmp_path / 'content').rglob('*.*'))
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'reader')
        time.sleep(.2)
        assert client.get('/api/v3/publication/'+material['id']).status_code==404
        assert client.get(image).status_code==404
        # A repeated Atom window can create a new Publication; the old one stays removed.


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
        material = wait(client, lambda m: m['theses']['error'])
        assert 'Запрещена публичная' in material['theses']['error']
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
    import psycopg
    from tests.support.reader import Clock, wait_for
    from tests.support.storage import database_url

    late_text = 'Late captions must not return.'
    class Late(Captions):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.cancelled = threading.Event()
            self.release = threading.Event()
            self.completed = threading.Event()

        async def fetch(self, video):
            task = asyncio.current_task()
            assert task is not None
            task.add_done_callback(lambda _: self.completed.set())
            self.started.set()
            while not self.release.is_set():
                try:
                    await asyncio.sleep(.01)
                except asyncio.CancelledError:
                    self.cancelled.set()
            return late_text

    def media_files():
        return {path.relative_to(tmp_path): path.read_bytes()
                for path in (tmp_path / 'media').rglob('*') if path.is_file()}

    clock = Clock()
    captions = Late()
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=transport([]), youtube_captions=captions, **clock.options()), headers=HEADERS) as client:
        try:
            # Keep setup's temporary session from cancelling the tested attempt.
            assert presence(client, 'reader').status_code == 200
            subscribe(client)
            assert captions.started.wait(3)
            item = read_all_cards(client, '/api/v3/cards')[0]['id']
            material = wait_for(client, lambda: value if (value := client.get('/api/v3/publication/' + item).json()) and any(media['role'] == 'attachment' for media in value['media']) else None)
            assert client.get([media for media in material['media'] if media['role'] == 'attachment'][0]['url']).content == PNG
            before_files = media_files()
            assert PNG in before_files.values()
            with psycopg.connect(database_url(tmp_path), autocommit=True) as db:
                assert db.execute("SELECT status,storage_key FROM media_files WHERE publication_id=%s AND resource_key='youtube:subtitles'", (item,)).fetchone() == ('pending_download', None)
                media_job_ids = [row[0] for row in db.execute("SELECT j.id FROM jobs j JOIN media_files m ON m.id=j.item WHERE j.kind='download_media' AND m.publication_id=%s",(item,))]
                assert media_job_ids
            assert not captions.cancelled.is_set()
            if action == 'viewed':
                assert client.post('/api/v3/materials/' + item + '/viewed').status_code == 200
            elif action == 'stop':
                assert client.post('/api/v3/stop').status_code == 200
            else:
                assert client.put('/api/v3/power', json={'enabled':False}).status_code == 200
                assert client.get('/api/v3/status').json()['work_allowed'] is False
            assert captions.cancelled.wait(3)
        finally:
            captions.release.set()
        # Wait for the whole collection attempt, including acceptance/cleanup,
        # rather than just the synthetic provider returning or a fixed sleep.
        assert captions.completed.wait(3)
        with psycopg.connect(database_url(tmp_path), autocommit=True) as db:
            if action == 'viewed':
                # Deletion defers while the caption attempt owns a media path.
                # Let its scheduled retry run after that attempt has released it.
                clock.now += .2
                wait_for(client, lambda: db.execute('SELECT id FROM publications WHERE id=%s', (item,)).fetchone() is None)
            rows = db.execute('SELECT content,filtered_content,summarized_content FROM publications').fetchall()
            assert all(late_text not in value for row in rows for value in row)
            publication = db.execute('SELECT viewed FROM publications WHERE id=%s', (item,)).fetchone()
            subtitles = db.execute("SELECT status,storage_key FROM media_files WHERE publication_id=%s AND resource_key='youtube:subtitles'", (item,)).fetchall()
            if action == 'viewed':
                assert publication is None
                assert db.execute('SELECT id FROM media_files WHERE publication_id=%s', (item,)).fetchall() == []
                assert db.execute('SELECT id FROM jobs WHERE id=ANY(%s)',(media_job_ids,)).fetchall() == []
            else:
                assert publication == (0,)
                assert len(subtitles) == 1
                assert subtitles[0][0] != 'ready' and subtitles[0][1] is None
        files = media_files()
        assert all(late_text.encode() not in data for data in files.values())
        assert files == ({} if action == 'viewed' else before_files)
        if action == 'viewed':
            assert read_all_cards(client, '/api/v3/cards') == []
            assert client.get('/api/v3/publication/' + item).status_code == 404
        elif action == 'stop':
            assert client.get('/api/v3/status').json()['runtime_state'] == 'stopped'
        else:
            assert late_text not in client.get('/api/v3/publication/' + item).text


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
        # Keep the worker admitted when subscribe closes its short setup session.
        presence(client, 'reader')
        feed = subscribe(client)
        presence(client, 'reader')
        wait(client, lambda m: m['source'].get('captions') == 'pending' and bool(m['completeness'].get('message')), keep_alive=True)
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
            wait(client, lambda m: m['source'].get('captions') == 'pending' and bool(m['completeness'].get('message')),keep_alive=True)
            assert any(g['error_type'] == 'VideoUnplayable' for g in client.get('/api/v3/diagnostics').json()['groups'])
            return
        for _ in range(200):
            # A live browser renews its lease while an SDK worker is pending.
            assert presence(client, 'reader').status_code==200
            if calls.exists() and read_all_cards(client, '/api/v3/cards') == []:
                break
            time.sleep(.02)
        else:
            raise AssertionError('Future live card was not discarded')
        assert client.get('/api/v3/diagnostics').json()['groups'] == []
        assert calls.read_text().splitlines() == [VIDEO]


def test_description_uses_common_content_block():
    from datetime import datetime, timezone
    from oko.collection.adapters import ExternalKey, ExternalReference, Platform, PublicationContent, PublicationMetadata
    from oko.collection.youtube.publication_source import YouTubeAdapterSource
    from oko.storage.publication_document import header

    ref = ExternalReference(ExternalKey(Platform.youtube, 'video'), 'https://youtube.test/video')
    value = PublicationContent(PublicationMetadata(ref, 'Title', datetime.now(timezone.utc)),
                               {'blocks': [{'type': 'text', 'role': 'description', 'text': 'Description'}],
                                'extra': {'youtube': {'captions': 'pending'}}})
    source = object.__new__(YouTubeAdapterSource)
    source.want_captions = True
    material = source._material(value)
    assert material.description == 'Description'
    assert 'description' not in header(material)
    assert material.adapter_content.content['blocks'][0]['text'] == 'Description'
    cleared = PublicationContent(value.metadata,
                                 {'blocks': [{'type': 'text', 'role': 'description', 'text': ''}],
                                  'extra': {'youtube': {'captions': 'pending'}}})
    assert source._material(cleared, material).description == ''


def test_confirmed_absent_captions_survive_restart_without_retry(tmp_path):
    import psycopg
    from tests.support.storage import database_url

    class Missing(Captions):
        async def fetch(self, video):
            self.calls.append(video)
            return None

    captions=Missing()
    kwargs=dict(token='test',rss_enabled=True,rss_transport=transport([]),youtube_captions=captions,lease=600)
    with TestClient(create_app(tmp_path, **kwargs),headers=HEADERS) as client:
        presence(client,'reader')
        subscribe(client)
        material=wait(client,lambda value:value['source'].get('captions')=='unavailable')
        item=material['id']
        with psycopg.connect(database_url(tmp_path)) as db:
            assert db.execute("SELECT j.status,j.reason_code FROM jobs j JOIN media_files m ON m.id=j.item WHERE j.kind='download_media' AND m.publication_id=%s AND m.resource_key='youtube:subtitles'",(item,)).fetchone()==('done','subtitles_absent')
        with psycopg.connect(database_url(tmp_path),autocommit=True) as db:
            db.execute("UPDATE jobs SET reason_message=NULL WHERE kind='download_media' AND item=(SELECT id FROM media_files WHERE publication_id=%s AND resource_key='youtube:subtitles')",(item,))
        assert client.get('/api/v3/publication/'+item).json()['completeness']['message']=='Субтитры недоступны'
    with TestClient(create_app(tmp_path, **kwargs),headers=HEADERS) as client:
        presence(client,'reader')
        material=wait(client,lambda value:value['id']==item and value['source'].get('captions')=='unavailable')
        assert material['completeness']['message']=='Субтитры недоступны'
        assert captions.calls==[VIDEO]


def test_replacing_captions_keeps_the_same_download_job(tmp_path):
    import psycopg
    from tests.support.storage import database_url

    class Changing(Captions):
        async def fetch(self, video):
            self.calls.append(video)
            return 'First captions' if len(self.calls)==1 else 'Replacement captions'

    captions,downloads,shift=Changing(),[],[0.0]
    kwargs=dict(token='test',rss_enabled=True,rss_transport=transport(downloads),youtube_captions=captions,
                lease=600,clock=lambda:time.monotonic()+shift[0],wall_clock=lambda:time.time()+shift[0])
    with TestClient(create_app(tmp_path,**kwargs),headers=HEADERS) as client:
        presence(client,'reader')
        subscribe(client)
        material=wait(client,lambda m:m['source'].get('captions')=='available' and not m['loading'])
        with psycopg.connect(database_url(tmp_path)) as db:
            first=db.execute("SELECT j.id,m.id FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=%s AND m.resource_key='youtube:subtitles'",(material['id'],)).fetchone()
        assert first is not None
        assert client.post('/api/v3/materials/'+material['id']+'/retry').status_code==200
        shift[0]+=60
        wait(client,lambda m:m['text']=='Replacement captions' and not m['loading'])
        with psycopg.connect(database_url(tmp_path)) as db:
            second=db.execute("SELECT j.id,m.id,j.status FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=%s AND m.resource_key='youtube:subtitles'",(material['id'],)).fetchone()
        assert second is not None and second[0]==first[0] and second[2]=='done'


def test_subtitles_disappearing_on_retry_keep_completed_job_and_reason(tmp_path):
    import psycopg
    from tests.support.storage import database_url

    class Disappearing(Captions):
        async def fetch(self, video):
            self.calls.append(video)
            return 'Previously saved captions' if len(self.calls)==1 else None

    captions,shift=Disappearing(),[0.0]
    kwargs=dict(token='test',rss_enabled=True,rss_transport=transport([]),youtube_captions=captions,
                lease=600,clock=lambda:time.monotonic()+shift[0],wall_clock=lambda:time.time()+shift[0])
    with TestClient(create_app(tmp_path,**kwargs),headers=HEADERS) as client:
        presence(client,'reader')
        subscribe(client)
        material=wait(client,lambda m:m['source'].get('captions')=='available' and not m['loading'])
        with psycopg.connect(database_url(tmp_path)) as db:
            first=db.execute("SELECT j.id FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=%s AND m.resource_key='youtube:subtitles'",(material['id'],)).fetchone()
        assert first is not None
        assert client.post('/api/v3/materials/'+material['id']+'/retry').status_code==200
        shift[0]+=60
        wait(client,lambda m:m['source'].get('captions')=='unavailable' and not m['loading'])
        with psycopg.connect(database_url(tmp_path)) as db:
            second=db.execute("SELECT j.id,j.status,j.reason_code FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=%s AND m.resource_key='youtube:subtitles'",(material['id'],)).fetchone()
        assert second==(first[0],'done','subtitles_absent')
