"""Revision lifecycle through the agreed reader API and external HTTP seam."""
from oko.collection.contracts import DiscoveryResult, FetchResult, FetchRequirements, FetchResources
from tests.support.presence import presence, close_presence
import time
import httpx
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.rss import rss, import_feed, wait_cards
from tests.support.reader import wait_for

HEADERS = {'Authorization': 'Bearer test-token'}
PNG = bytes.fromhex('89504e470d0a1a0a')


def test_rss_edit_keeps_previous_media_and_viewed_identity_never_returns(tmp_path):
    revision = [1]
    def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        if request.url.path == '/article':
            return httpx.Response(200, text=f'<article><p>Версия {revision[0]}</p><img src="/image.png"></article>')
        return httpx.Response(200, content=PNG + str(revision[0]).encode(), headers={'Content-Type': 'image/png'})
    options = dict(token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(reply))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        import_feed(client)
        presence(client, 'test')
        item = wait_cards(client)[0]['id']
        revision[0] = 2
        client.post('/api/v3/collect')
        current = wait_for(client, lambda: m if (m := client.get('/api/v3/materials/' + item).json())['content'].get('text') == 'Версия 2' else None)
        assert current['content']['changed'] is True
        assert current['content']['previous_versions']==[]
        assert client.get(current['content']['images'][0]).content == PNG + b'2'
        client.post('/api/v3/collect')
        wait_for(client, lambda: client.get('/api/v3/status').json()['work']==[{'phase':'idle'}])
        assert len(client.get('/api/v3/materials/' + item).json()['content']['previous_versions']) == 0
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/materials/' + item).json()['content']['text'] == 'Версия 2'
        presence(client, 'test')
        client.post('/api/v3/materials/' + item + '/viewed')
        assert client.get(current['content']['images'][0]).status_code == 404
        assert not (tmp_path / 'content' / item).exists()
        revision[0] = 3
        client.post('/api/v3/collect')
        time.sleep(.15)
        assert client.get('/api/v3/cards').json() == []




def test_confirmed_remote_deletion_keeps_local_copy_but_absence_is_not_deletion(tmp_path):
    removed = [False]
    def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        if removed[0]:
            return httpx.Response(404)
        return httpx.Response(200, text='<article><p>Локальная копия</p></article>')
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        import_feed(client)
        presence(client, 'test')
        item = wait_cards(client)[0]['id']
        removed[0] = True
        client.post('/api/v3/collect')
        retained = wait_for(client, lambda: m if (m := client.get('/api/v3/materials/' + item).json())['content'].get('source_deleted') else None)
        assert retained['content']['text'] == 'Локальная копия'
        assert retained['content']['previous_versions'] == []
        assert (retained['content']['completeness']['state']=='complete') is True
        client.post('/api/v3/materials/' + item + '/viewed')
        assert client.get('/api/v3/materials/' + item).status_code == 404


def test_interrupted_version_publish_keeps_old_until_fresh_retry(tmp_path):
    from oko.collection.contracts import Material, Publication
    class Editable:
        fetch_requirements = FetchRequirements()
        changes = None
        text = 'Первая версия'
        async def discover(self, cursor):
            return DiscoveryResult(tuple([Publication('one', 'https://source.test/one', time.time())]), 'one')
        async def fetch(self, publication, resources=FetchResources()):
            return FetchResult(Material('Новость', self.text))
    source = Editable()
    interrupted = [False]
    def fault(point):
        if interrupted[0] and point == 'after_file_move':
            raise OSError('interrupted')
    options = dict(source=source, token='test-token')
    with TestClient(create_app(tmp_path, fault=fault, **options), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'test')
        item = wait_cards(client)[0]['id']
        source.text = 'Вторая версия'
        interrupted[0] = True
        client.post('/api/v3/collect')
        wait_for(client, lambda: any(p['code']=='material_save_failed' for p in client.get('/api/v3/status').json()['problems']['current']))
        assert client.get('/api/v3/materials/' + item).json()['content']['text'] == 'Первая версия'
        assert client.get('/api/v3/materials/' + item).json()['content']['previous_versions'] == []
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/materials/' + item).json()['content']['text'] == 'Первая версия'
        source.text = 'Третья версия после восстановления'
        presence(client, 'test')
        current = wait_for(client, lambda: m if (m := client.get('/api/v3/materials/' + item).json())['content'].get('text') == source.text else None)
        assert len(current['content']['previous_versions']) == 0
        assert current['content']['previous_versions']==[]
        client.post('/api/v3/materials/' + item + '/viewed')
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()


def test_explicit_telegram_reload_checks_known_ids_including_media_only_edits_and_deletions(tmp_path):
    from tests.support.telegram import Telegram, connect, settled
    class Editable(Telegram):
        text = 'Текст до правки'
        data = b'first-image'
        missing = False
        async def messages(self, account, peer, ids):
            if self.missing:
                return []
            return [dict(id=11, date=time.time()-60, text=self.text, group=None,
                         media=dict(kind='image', mime='image/jpeg', extension='jpg'))]
        async def download(self, account, peer, id):
            return self.data
    source = Editable()
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=source), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'test')
        connect(client)
        item = settled(client)[0]['id']
        source.text = ''  # A real edit may remove all text.
        source.data = b'edited-image'
        client.post('/api/v3/materials/' + item + '/retry')
        edited = wait_for(client, lambda: m if (m := client.get('/api/v3/materials/' + item).json())['content'].get('text') == '' else None)
        assert client.get(edited['content']['attachments'][0]['url']).content == b'edited-image'
        assert edited['content']['previous_versions']==[]
        previous_revision=edited['content']['revision_id']
        source.data = b'media-only-edit'
        client.post('/api/v3/materials/' + item + '/retry')
        edited = wait_for(client, lambda: m if (m := client.get('/api/v3/materials/' + item).json())['content']['revision_id']!=previous_revision else None)
        assert client.get(edited['content']['attachments'][0]['url']).content == b'media-only-edit'
        source.missing = True
        client.post('/api/v3/materials/' + item + '/retry')
        wait_for(client, lambda: client.get('/api/v3/materials/' + item).json()['content']['source_deleted'])
        assert client.get(edited['content']['attachments'][0]['url']).content == b'media-only-edit'
        client.post('/api/v3/materials/' + item + '/viewed')
        assert client.get(edited['content']['attachments'][0]['url']).status_code == 404


def test_late_ai_response_cannot_publish_theses_for_superseded_version(tmp_path):
    import asyncio
    import json
    import threading
    from oko.collection.contracts import Material, Publication
    from tests.support.models import model_transport
    class Editable:
        fetch_requirements = FetchRequirements()
        changes = None
        text = 'Старое утверждение'
        async def discover(self, cursor):
            return DiscoveryResult(tuple([Publication('one', 'https://source.test/one', time.time())]), 'one')
        async def fetch(self, publication, resources=FetchResources()):
            return FetchResult(Material('Новость', self.text))
    class Held(httpx.AsyncBaseTransport):
        started = threading.Event()
        release = threading.Event()
        native = model_transport([])
        async def handle_async_request(self, request):
            if request.url.path != '/v1/chat/completions':
                return self.native.handler(request)
            task = json.loads(json.loads(request.content)['messages'][1]['content'])
            text = task['sources'][0]['text']
            if text == 'Старое утверждение':
                self.started.set()
                while not self.release.is_set():
                    try:
                        await asyncio.sleep(.01)
                    except asyncio.CancelledError:
                        pass
            ref = task['sources'][0]
            result = dict(points=[dict(text=text, sources=[dict(id=ref['id'], url=ref['url'])])])
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result)}}]})
    source, held = Editable(), Held()
    with TestClient(create_app(tmp_path, source=source, token='test-token', ai_transport=held), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        presence(client, 'test')
        item = wait_cards(client)[0]['id']
        try:
            assert held.started.wait(2)
            source.text = 'Новое утверждение'
            client.post('/api/v3/collect')
            current = wait_for(client, lambda: m if (m := client.get('/api/v3/materials/' + item).json())['content'].get('text') == source.text else None)
            assert current['theses']['result'] is None
        finally:
            held.release.set()
        theses = wait_for(client, lambda: client.get('/api/v3/materials/' + item).json()['theses']['result'])
        assert [p['text'] for p in theses['points']] == ['Новое утверждение']


def test_youtube_title_edit_is_visible_but_leaving_atom_window_is_not_deletion(tmp_path):
    from tests.support.youtube import feed_xml, subscribe, PNG as PREVIEW
    from tests.support.app import NoCaptions
    title, present = ['О границах модели'], [True]
    clock = [time.time()]
    def reply(request):
        if request.url.path == '/feeds/videos.xml':
            xml = feed_xml().replace('О границах модели', title[0]) if present[0] else '<feed xmlns="http://www.w3.org/2005/Atom"/>'
            return httpx.Response(200, text=xml)
        return httpx.Response(200, content=PREVIEW, headers={'Content-Type': 'image/png'})
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(reply), youtube_captions=NoCaptions(),clock=lambda:clock[0],wall_clock=lambda:clock[0],lease=60), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        subscribe(client)
        presence(client, 'test')
        item = wait_cards(client)[0]['id']
        title[0] = 'Новое название видео'
        clock[0] += 11  # Manual refresh now retries captions, respecting the source interval.
        client.post('/api/v3/collect')
        material = wait_for(client, lambda: m if (m := client.get('/api/v3/materials/' + item).json()).get('title') == title[0] else None)
        present[0] = False
        client.post('/api/v3/collect')
        time.sleep(.1)
        retained = client.get('/api/v3/materials/' + item).json()
        assert retained['title'] == 'Новое название видео' and retained['content']['source_deleted'] is False
        assert 'не определяются' in client.get('/api/v3/status').json()['sources'][0]['changes']




def test_revision_waits_for_public_ai_permission_instead_of_reusing_old_theses(tmp_path):
    from oko.collection.contracts import Material, Publication
    from tests.support.models import model_transport
    class Editable:
        fetch_requirements = FetchRequirements()
        changes = None
        text = 'Первая версия'
        async def discover(self, cursor):
            return DiscoveryResult(tuple([Publication('one', 'https://source.test/one', time.time())]), 'one')
        async def fetch(self, publication, resources=FetchResources()):
            return FetchResult(Material('Новость', self.text))
    source, calls = Editable(), []
    with TestClient(create_app(tmp_path, source=source, token='test-token', ai_transport=model_transport(calls)), headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'https://remote.test', 'model': 'local:latest', 'local_execution': False})
        client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        feed = 'demo'
        client.patch('/api/v3/feeds/' + feed, json={'public_ai': True})
        presence(client, 'test')
        item = wait_cards(client)[0]['id']
        wait_for(client, lambda: client.get('/api/v3/materials/' + item).json()['theses']['result'])
        client.patch('/api/v3/feeds/' + feed, json={'public_ai': False})
        count = sum(r.url.path == '/v1/chat/completions' for r in calls)
        source.text = 'Правка с закрытым доступом AI'
        client.post('/api/v3/collect')
        material = wait_for(client, lambda: m if (m := client.get('/api/v3/materials/' + item).json())['content'].get('text') == source.text else None)
        assert material['theses']['result'] is None
        time.sleep(.1)
        assert sum(r.url.path == '/v1/chat/completions' for r in calls) == count
        assert client.get('/api/v3/materials/' + item).json()['theses']['diagnostic_message']


def test_failed_unchanged_telegram_reload_does_not_create_revision_or_erase_media(tmp_path):
    from tests.support.telegram import Telegram, connect, settled
    from oko.collection.contracts import SourceError
    class Editable(Telegram):
        fail = False
        text = 'Неизменный пост'
        async def messages(self, account, peer, ids):
            return [dict(id=11, date=time.time()-60, text=self.text, group=None,
                         media=dict(kind='image', mime='image/jpeg', extension='jpg'))]
        async def download(self, account, peer, id):
            if self.fail:
                raise SourceError('Ограничение частоты', 60)
            return b'saved-image'
    source = Editable()
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=source), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'test')
        connect(client)
        original = settled(client)[0]
        item = original['id']
        source.fail = True
        client.post('/api/v3/materials/' + item + '/retry')
        wait_for(client, lambda: any(p['code']=='source_unavailable' for p in client.get('/api/v3/status').json()['problems']['current']) or client.get('/api/v3/materials/' + item).json()['content']['changed'])
        retained = client.get('/api/v3/materials/' + item).json()
        assert retained['content']['revision_id'] == original['revision_id']
        assert retained['content']['changed'] is False and retained['content']['previous_versions'] == []
        assert (retained['content']['completeness']['state']=='complete') is True
        assert client.get(retained['content']['attachments'][0]['url']).content == b'saved-image'
        assert [p['message'] for p in client.get('/api/v3/status').json()['problems']['current'] if p['code'] in ('source_unavailable','material_save_failed','material_read_failed')] == ['Ограничение частоты']
        source.fail = False
        client.post('/api/v3/materials/' + item + '/retry')
        wait_for(client, lambda: not client.get('/api/v3/materials/' + item).json()['content']['loading'])
        assert client.get('/api/v3/materials/' + item).json()['content']['revision_id'] == original['revision_id']
        source.text = 'Настоящая правка текста при недоступном вложении'
        source.fail = True
        client.post('/api/v3/materials/' + item + '/retry')
        wait_for(client, lambda: any(p['code']=='source_unavailable' for p in client.get('/api/v3/status').json()['problems']['current']))
        assert client.get('/api/v3/materials/' + item).json()['content']['text'] == 'Неизменный пост'
        source.fail = False
        client.post('/api/v3/materials/' + item + '/retry')
        edited = wait_for(client, lambda: m if (m := client.get('/api/v3/materials/' + item).json())['content'].get('text') == source.text else None)
        assert edited['content']['changed'] is True and (edited['content']['completeness']['state']=='complete') is True
        assert len(edited['content']['previous_versions']) == 0


def test_disabling_youtube_theses_keeps_existing_captions_without_refetch(tmp_path):
    from tests.support.youtube import feed_xml, subscribe, PNG as PREVIEW
    class Captions:
        calls = 0
        async def fetch(self, video):
            self.calls += 1
            return 'Уже сохранённые субтитры'
    captions = Captions()
    def reply(request):
        if request.url.path == '/feeds/videos.xml':
            return httpx.Response(200, text=feed_xml())
        return httpx.Response(200, content=PREVIEW, headers={'Content-Type': 'image/png'})
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(reply), youtube_captions=captions), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        feed = subscribe(client)
        client.patch('/api/v3/settings/feed/' + feed, json={'theses': True})
        presence(client, 'test')
        original = wait_cards(client)[0]
        item = original['id']
        assert client.get('/api/v3/materials/' + item).json()['source']['captions'] == 'available'
        count = captions.calls
        client.patch('/api/v3/settings/feed/' + feed, json={'theses': False})
        client.post('/api/v3/collect')
        wait_for(client, lambda: not client.get('/api/v3/materials/' + item).json()['content'].get('loading') and client.get('/api/v3/status').json()['ready_sources'] == 1)
        retained = client.get('/api/v3/materials/' + item).json()
        assert retained['content']['text'] == 'Уже сохранённые субтитры'
        assert retained['source']['captions'] == 'available'
        assert retained['content']['revision_id'] == original['revision_id']
        assert retained['content']['changed'] is False and retained['content']['previous_versions'] == []
        assert captions.calls == count
