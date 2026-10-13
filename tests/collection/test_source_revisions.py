"""Revision lifecycle through the agreed reader API and external HTTP seam."""
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
        import_feed(client)
        client.post('/api/tabs/test/heartbeat')
        item = wait_cards(client)[0]['id']
        revision[0] = 2
        client.post('/api/collect')
        current = wait_for(client, lambda: m if (m := client.get('/api/materials/' + item).json()).get('text') == 'Версия 2' else None)
        assert current['changed'] is True
        previous = client.get(current['previous'][0]['url']).json()
        assert previous['text'] == 'Версия 1'
        assert client.get(previous['images'][0]).content == PNG + b'1'
        assert client.get(current['images'][0]).content == PNG + b'2'
        client.post('/api/collect')
        wait_for(client, lambda: client.get('/api/status').json()['stage'] == 'Готово')
        assert len(client.get('/api/materials/' + item).json()['previous']) == 1
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/materials/' + item).json()['text'] == 'Версия 2'
        client.post('/api/tabs/test/heartbeat')
        client.post('/api/materials/' + item + '/viewed')
        assert client.get(previous['images'][0]).status_code == 404
        assert client.get(current['previous'][0]['url']).status_code == 404
        assert not (tmp_path / 'content' / item).exists()
        revision[0] = 3
        client.post('/api/collect')
        time.sleep(.15)
        assert client.get('/api/cards').json() == []


def test_edit_regenerates_same_digest_members_and_current_theses(tmp_path):
    from oko.collection.contracts import Material, Publication
    from tests.support.digests import digest_transport
    class Editable:
        text = 'Старое утверждение'
        async def discover(self, cursor):
            return [Publication('one', 'https://source.test/one', time.time())], 'one'
        async def fetch(self, publication):
            return Material('Новость', self.text)
    source = Editable()
    calls = []
    with TestClient(create_app(tmp_path, source=source, token='test-token', ai_transport=digest_transport(calls)), headers=HEADERS) as client:
        client.patch('/api/settings/oko/global', json={'events': False, 'processor': 'llamacpp', 'theses': True})
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.post('/api/tabs/test/heartbeat')
        item = wait_cards(client)[0]['id']
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        wait_for(client, lambda: client.get('/api/materials/' + identity).json().get('digest'))
        source.text = 'Новое утверждение'
        client.post('/api/collect')
        updated = wait_for(client, lambda: m if (m := client.get('/api/materials/' + identity).json()).get('digest') and m['sources'][0]['text'] == source.text else None)
        assert [s['id'] for s in updated['sources']] == [item]
        assert client.get('/api/materials/' + item).json()['theses']
        assert any(c['operation'] == 'digest' and c['sources'][0]['text'] == 'Новое утверждение' for c in calls)
        assert any(c['operation'] == 'theses' and c['sources'][0]['text'] == 'Новое утверждение' for c in calls)


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
        client.post('/api/tabs/test/heartbeat')
        item = wait_cards(client)[0]['id']
        removed[0] = True
        client.post('/api/collect')
        retained = wait_for(client, lambda: m if (m := client.get('/api/materials/' + item).json()).get('source_deleted') else None)
        assert retained['text'] == 'Локальная копия'
        assert retained['previous'] == []
        assert retained['full'] is True
        client.post('/api/materials/' + item + '/viewed')
        assert client.get('/api/materials/' + item).status_code == 404


def test_interrupted_version_publish_keeps_old_until_fresh_retry(tmp_path):
    from oko.collection.contracts import Material, Publication
    class Editable:
        text = 'Первая версия'
        async def discover(self, cursor):
            return [Publication('one', 'https://source.test/one', time.time())], 'one'
        async def fetch(self, publication):
            return Material('Новость', self.text)
    source = Editable()
    interrupted = [False]
    def fault(point):
        if interrupted[0] and point == 'after_file_move':
            raise OSError('interrupted')
    options = dict(source=source, token='test-token')
    with TestClient(create_app(tmp_path, fault=fault, **options), headers=HEADERS) as client:
        client.patch('/api/settings/oko/global', json={'events': False})
        client.post('/api/tabs/test/heartbeat')
        item = wait_cards(client)[0]['id']
        source.text = 'Вторая версия'
        interrupted[0] = True
        client.post('/api/collect')
        wait_for(client, lambda: client.get('/api/status').json()['errors'])
        assert client.get('/api/materials/' + item).json()['text'] == 'Первая версия'
        assert client.get('/api/materials/' + item).json()['previous'] == []
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/materials/' + item).json()['text'] == 'Первая версия'
        source.text = 'Третья версия после восстановления'
        client.post('/api/tabs/test/heartbeat')
        current = wait_for(client, lambda: m if (m := client.get('/api/materials/' + item).json()).get('text') == source.text else None)
        assert len(current['previous']) == 1
        assert client.get(current['previous'][0]['url']).json()['text'] == 'Первая версия'
        client.post('/api/materials/' + item + '/viewed')
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()


def test_telegram_rechecks_known_ids_including_media_only_edits_and_deletions(tmp_path):
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
        client.patch('/api/settings/oko/global', json={'events': False})
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        item = settled(client)[0]['id']
        source.text = ''  # A real edit may remove all text.
        source.data = b'edited-image'
        client.post('/api/collect')
        edited = wait_for(client, lambda: m if (m := client.get('/api/materials/' + item).json()).get('text') == '' else None)
        assert client.get(edited['attachments'][0]['url']).content == b'edited-image'
        previous = client.get(edited['previous'][0]['url']).json()
        assert previous['text'] == 'Текст до правки'
        assert client.get(previous['attachments'][0]['url']).content == b'first-image'
        source.data = b'media-only-edit'
        client.post('/api/collect')
        edited = wait_for(client, lambda: m if len((m := client.get('/api/materials/' + item).json()).get('previous', [])) == 2 else None)
        assert client.get(edited['attachments'][0]['url']).content == b'media-only-edit'
        source.missing = True
        client.post('/api/collect')
        wait_for(client, lambda: client.get('/api/materials/' + item).json()['source_deleted'])
        assert client.get(edited['attachments'][0]['url']).content == b'media-only-edit'
        client.post('/api/materials/' + item + '/viewed')
        assert client.get(previous['attachments'][0]['url']).status_code == 404


def test_late_ai_response_cannot_publish_theses_for_superseded_version(tmp_path):
    import asyncio
    import json
    import threading
    from oko.collection.contracts import Material, Publication
    from tests.support.models import model_transport
    class Editable:
        text = 'Старое утверждение'
        async def discover(self, cursor):
            return [Publication('one', 'https://source.test/one', time.time())], 'one'
        async def fetch(self, publication):
            return Material('Новость', self.text)
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
        client.patch('/api/settings/oko/global', json={'events': False, 'processor': 'llamacpp', 'theses': True})
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.post('/api/tabs/test/heartbeat')
        item = wait_cards(client)[0]['id']
        try:
            assert held.started.wait(2)
            source.text = 'Новое утверждение'
            client.post('/api/collect')
            current = wait_for(client, lambda: m if (m := client.get('/api/materials/' + item).json()).get('text') == source.text else None)
            assert current['theses'] is None
        finally:
            held.release.set()
        theses = wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))
        assert [p['text'] for p in theses['points']] == ['Новое утверждение']


def test_youtube_title_edit_is_visible_but_leaving_atom_window_is_not_deletion(tmp_path):
    from tests.support.youtube import feed_xml, subscribe, NoCaptions, PNG as PREVIEW
    title, present = ['О границах модели'], [True]
    def reply(request):
        if request.url.path == '/feeds/videos.xml':
            xml = feed_xml().replace('О границах модели', title[0]) if present[0] else '<feed xmlns="http://www.w3.org/2005/Atom"/>'
            return httpx.Response(200, text=xml)
        return httpx.Response(200, content=PREVIEW, headers={'Content-Type': 'image/png'})
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(reply), youtube_captions=NoCaptions()), headers=HEADERS) as client:
        client.patch('/api/settings/oko/global', json={'events': False})
        subscribe(client)
        client.post('/api/tabs/test/heartbeat')
        item = wait_cards(client)[0]['id']
        title[0] = 'Новое название видео'
        client.post('/api/collect')
        material = wait_for(client, lambda: m if (m := client.get('/api/materials/' + item).json()).get('title') == title[0] else None)
        assert client.get(material['previous'][0]['url']).json()['title'] == 'О границах модели'
        present[0] = False
        client.post('/api/collect')
        time.sleep(.1)
        retained = client.get('/api/materials/' + item).json()
        assert retained['title'] == 'Новое название видео' and retained['source_deleted'] is False
        assert 'не определяются' in client.get('/api/status').json()['sources'][0]['changes']


def test_revision_invalidates_events_without_deleting_other_originals(tmp_path):
    from tests.support.sources import EventSource
    from tests.support.events import event_transport
    from oko.collection.contracts import Material, Publication
    class Editable(EventSource):
        edited = False
        async def discover(self, cursor):
            return [Publication(str(i), f'https://example.org/event/{i}', 1800000000+i*60) for i in range(3)], '3'
        async def fetch(self, publication):
            if self.edited and publication.id == '0':
                return Material('Открытие', 'Библиотека увеличила число мест до 40.')
            return await super().fetch(publication)
    source, calls = Editable(), []
    with TestClient(create_app(tmp_path, source=source, token='test-token', ai_transport=event_transport(calls)), headers=HEADERS) as client:
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'events': True, 'processor': 'llamacpp'})
        client.post('/api/tabs/test/heartbeat')
        event = wait_for(client, lambda: next((c for c in client.get('/api/cards').json() if c.get('event')), None))
        members = [s['id'] for s in event['sources']]
        source.edited = True
        client.post('/api/collect')
        updated = wait_for(client, lambda: next((c for c in client.get('/api/cards').json() if c.get('event') and any(s['text'] == 'Библиотека увеличила число мест до 40.' for s in client.get('/api/materials/' + c['id']).json().get('sources', []))), None))
        assert {s['id'] for s in updated['sources']} == set(members)
        changed = next(s for s in client.get('/api/materials/' + updated['id']).json()['sources'] if s['changed'])
        assert client.get(changed['previous'][0]['url']).json()['text'] == 'Открылась библиотека на 24 места.'
        client.post('/api/materials/' + changed['id'] + '/viewed')
        assert client.get(changed['previous'][0]['url']).status_code == 404
        for member in set(members) - {changed['id']}:
            assert client.get('/api/materials/' + member).status_code == 200


def test_revision_waits_for_public_ai_permission_instead_of_reusing_old_theses(tmp_path):
    from oko.collection.contracts import Material, Publication
    from tests.support.digests import digest_transport
    class Editable:
        text = 'Первая версия'
        async def discover(self, cursor):
            return [Publication('one', 'https://source.test/one', time.time())], 'one'
        async def fetch(self, publication):
            return Material('Новость', self.text)
    source, calls = Editable(), []
    with TestClient(create_app(tmp_path, source=source, token='test-token', ai_transport=digest_transport(calls)), headers=HEADERS) as client:
        client.put('/api/processors/llamacpp', json={'endpoint': 'https://remote.test', 'model': 'local:latest', 'local_execution': False})
        client.patch('/api/settings/oko/global', json={'events': False, 'processor': 'llamacpp', 'theses': True})
        feed = 'demo'
        client.patch('/api/feeds/' + feed, json={'public_ai': True})
        client.post('/api/tabs/test/heartbeat')
        item = wait_cards(client)[0]['id']
        wait_for(client, lambda: client.get('/api/materials/' + item).json().get('theses'))
        client.patch('/api/feeds/' + feed, json={'public_ai': False})
        count = len(calls)
        source.text = 'Правка с закрытым доступом AI'
        client.post('/api/collect')
        material = wait_for(client, lambda: m if (m := client.get('/api/materials/' + item).json()).get('text') == source.text else None)
        assert material['theses'] is None
        time.sleep(.1)
        assert len(calls) == count
        assert client.get('/api/materials/' + item).json()['ai']['error']


def test_failed_unchanged_telegram_recheck_does_not_create_revision_or_erase_media(tmp_path):
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
        client.patch('/api/settings/oko/global', json={'events': False})
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        original = settled(client)[0]
        item = original['id']
        source.fail = True
        client.post('/api/collect')
        wait_for(client, lambda: client.get('/api/status').json()['errors'] or client.get('/api/materials/' + item).json()['changed'])
        retained = client.get('/api/materials/' + item).json()
        assert retained['version'] == original['version']
        assert retained['changed'] is False and retained['previous'] == []
        assert retained['full'] is True
        assert client.get(retained['attachments'][0]['url']).content == b'saved-image'
        assert client.get('/api/status').json()['errors'] == ['Ограничение частоты']
        source.fail = False
        client.post('/api/materials/' + item + '/retry')
        wait_for(client, lambda: not client.get('/api/materials/' + item).json()['loading'])
        assert client.get('/api/materials/' + item).json()['version'] == original['version']
        source.text = 'Настоящая правка текста при недоступном вложении'
        source.fail = True
        client.post('/api/collect')
        edited = wait_for(client, lambda: m if (m := client.get('/api/materials/' + item).json()).get('text') == source.text else None)
        assert edited['changed'] is True and edited['full'] is False
        assert len(edited['previous']) == 1
        assert client.get(edited['previous'][0]['url']).json()['text'] == 'Неизменный пост'


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
        client.patch('/api/settings/oko/global', json={'events': False})
        feed = subscribe(client)
        client.patch('/api/settings/feed/' + feed, json={'theses': True})
        client.post('/api/tabs/test/heartbeat')
        original = wait_cards(client)[0]
        item = original['id']
        assert client.get('/api/materials/' + item).json()['captions'] == 'available'
        count = captions.calls
        client.patch('/api/settings/feed/' + feed, json={'theses': False})
        client.post('/api/collect')
        wait_for(client, lambda: not client.get('/api/materials/' + item).json().get('loading') and client.get('/api/status').json()['ready_sources'] == 1)
        retained = client.get('/api/materials/' + item).json()
        assert retained['text'] == 'Уже сохранённые субтитры'
        assert retained['captions'] == 'available'
        assert retained['version'] == original['version']
        assert retained['changed'] is False and retained['previous'] == []
        assert captions.calls == count
