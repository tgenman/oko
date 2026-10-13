from tests.support.digests import configure, digest_transport
import json
import httpx
from fastapi.testclient import TestClient
from oko.reader.app import create_app
from tests.support.sources import EventSource
from tests.support.reader import HEADERS, setup_reader, wait_for
from tests.support.models import model_transport


def test_compress_preserves_exact_members_and_deletes_unmentioned_originals(tmp_path):
    calls = []
    options = dict(source=EventSource(), token='test', ai_transport=digest_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure(client)
        originals = client.get('/api/cards').json()
        result = client.post('/api/digests/folder/demo', json={})
        assert result.status_code == 200
        identity = result.json()['id']
        digest = wait_for(client, lambda: m if (m := client.get('/api/materials/' + identity).json()).get('digest') else None)
        assert {s['id'] for s in digest['sources']} == {c['id'] for c in originals}
        assert len(digest['digest']['points'][0]['sources']) == 1
        assert len(client.get('/api/cards').json()) == 1
        assert client.get('/api/folders').json()[0]['count'] == 3
        again = client.post('/api/digests/folder/demo', json={}).json()['id']
        wait_for(client, lambda: client.get('/api/materials/' + again).json().get('digest'))
        assert {s['id'] for s in client.get('/api/materials/' + again).json()['sources']} == {c['id'] for c in originals}
        assert len(client.get('/api/cards').json()) == 1
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/materials/' + again).json()['digest']
        client.post('/api/tabs/reader/heartbeat')
        assert client.post('/api/materials/' + again + '/viewed').status_code == 200
        assert client.get('/api/cards').json() == []
        for source in originals:
            assert client.get('/api/materials/' + source['id']).status_code == 404
            assert not (tmp_path / 'content' / source['id']).exists()
        assert not list((tmp_path / 'digests').glob('*.md'))


def test_hundred_sources_are_chunked_without_losing_frozen_members(tmp_path):
    from oko.collection.contracts import Publication, Material
    class Many:
        async def discover(self, cursor):
            return ([Publication(str(i), f'https://example.org/many/{i}', 1800000000+i) for i in range(100)] if cursor is None else []), '100'
        async def fetch(self, publication):
            return Material('Тема '+publication.id, 'Текст '+publication.id)
    calls = []
    with TestClient(create_app(tmp_path, source=Many(), token='test', ai_transport=digest_transport(calls)), headers=HEADERS) as client:
        client.patch('/api/settings/oko/global', json={'events': False, 'processor': 'llamacpp'})
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.post('/api/tabs/reader/heartbeat')
        import time
        for _ in range(600):
            client.post('/api/tabs/reader/heartbeat')
            cards = client.get('/api/cards').json()
            if len(cards) == 100 and all(x['complete'] for x in cards):
                break
            time.sleep(.02)
        assert len(cards) == 100 and all(x['complete'] for x in cards)
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        # Eleven processing requests need a live reader throughout the wait.
        # The shared three-second helper is too short under full-suite load.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            client.post('/api/tabs/reader/heartbeat')
            material = client.get('/api/materials/' + identity).json()
            if material.get('digest'):
                break
            time.sleep(.02)
        assert material.get('digest'), client.get('/api/status').json()
        assert len(material['sources']) == 100
        assert len(calls) == 11
        assert len({s['id'] for call in calls[:-1] for s in call['sources']}) == 100
        client.post('/api/materials/' + identity + '/viewed')
        assert client.get('/api/cards').json() == []
        assert not list((tmp_path / 'digests').glob('*'))


def test_schedule_coalesces_missed_ticks_and_opening_does_not_generate(tmp_path, monkeypatch):
    import time
    now = [1800000300.0]
    monkeypatch.setattr('oko.reader.runtime.time.time', lambda: now[0])
    calls = []
    options = dict(source=EventSource(), token='test', ai_transport=digest_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure(client)
        client.patch('/api/settings/oko/global', json={'digests': True, 'schedule': '00:00', 'digest_days': 7})
        wait_for(client, lambda: any(c.get('digest') for c in client.get('/api/cards').json()))
        assert len(calls) == 1
        client.get('/api/cards?folder=demo')
        time.sleep(.55)
        assert len(calls) == 1
    now[0] += 3*86400
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client, lambda: len(calls) == 2)
        wait_for(client, lambda: len(client.get('/api/cards').json()) == 1 and client.get('/api/cards').json()[0].get('digest'))
        time.sleep(.55)
        assert len(calls) == 2


def test_period_keeps_hidden_members_but_never_summarizes_ads(tmp_path):
    from tests.support.filters import FilterSource, transport
    filters = transport([])
    summary = digest_transport([])
    def reply(request):
        if request.url.path == '/v1/chat/completions':
            task = json.loads(json.loads(request.content)['messages'][1]['content'])
            if task['operation'] == 'digest':
                assert all('Реклама:' not in s['text'] for s in task['sources'])
                return summary.handler(request)
        return filters.handler(request)
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        configure(client)
        client.patch('/api/settings/oko/global', json={'prompt_filters': 'Убери рекламу'})
        wait_for(client, lambda: len(c := client.get('/api/cards').json()) == 2 and all(x['filter']['status'] == 'done' for x in c))
        assert client.post('/api/digests/folder/demo', json={'start': 20.0, 'end': 10.0}).status_code == 422
        identity = client.post('/api/digests/folder/demo', json={'start': 1799999999.0}).json()['id']
        digest = wait_for(client, lambda: m if (m := client.get('/api/materials/'+identity).json()).get('digest') else None)
        assert len(digest['sources']) == 2
        assert any(s['filter']['hidden'] for s in digest['sources'])
        client.post('/api/materials/'+identity+'/viewed')
        remaining = client.get('/api/cards?show_hidden=true').json()
        assert len(remaining) == 1 and remaining[0]['url'].endswith('/2')


def test_new_arrivals_stay_outside_existing_digest_and_repeat_expands_them(tmp_path):
    from oko.collection.contracts import Publication, Material
    class Growing(EventSource):
        added = False
        async def discover(self, cursor):
            items, _ = await super().discover(cursor)
            if self.added:
                items.append(Publication('3', 'https://example.org/event/3', 1800000300))
            return items, '4' if self.added else '3'
        async def fetch(self, publication):
            return Material('Новый факт', 'Новая тема') if publication.id == '3' else await super().fetch(publication)
    source = Growing()
    with TestClient(create_app(tmp_path, source=source, token='test', ai_transport=digest_transport([])), headers=HEADERS) as client:
        configure(client)
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        wait_for(client, lambda: client.get('/api/materials/'+identity).json().get('digest'))
        source.added = True
        client.post('/api/collect')
        wait_for(client, lambda: len(c := client.get('/api/cards').json()) == 2 and all(x['complete'] for x in c))
        assert len(client.get('/api/materials/'+identity).json()['sources']) == 3
        next_id = client.post('/api/digests/folder/demo', json={}).json()['id']
        next_digest = wait_for(client, lambda: m if (m := client.get('/api/materials/'+next_id).json()).get('digest') else None)
        assert len(next_digest['sources']) == 4
        assert client.get('/api/materials/'+identity).status_code == 404


def test_view_during_cancel_resistant_generation_cannot_restore_digest(tmp_path):
    import asyncio
    import threading
    import time
    class Held(httpx.AsyncBaseTransport):
        started = threading.Event()
        release = threading.Event()
        native = digest_transport([])
        async def handle_async_request(self, request):
            if request.url.path == '/v1/chat/completions':
                self.started.set()
                while not self.release.is_set():
                    try:
                        await asyncio.sleep(.01)
                    except asyncio.CancelledError:
                        pass
            return self.native.handler(request)
    held = Held()
    with TestClient(create_app(tmp_path, source=EventSource(), token='test', ai_transport=held), headers=HEADERS) as client:
        configure(client)
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        assert held.started.wait(2)
        member = client.get('/api/cards').json()[0]['id']
        client.post('/api/materials/'+member+'/viewed')
        held.release.set()
        time.sleep(.1)
        assert client.get('/api/materials/'+identity).status_code == 404
        assert not list((tmp_path / 'digests').glob('*.md'))


def test_permission_change_during_generation_discards_late_public_result(tmp_path):
    import asyncio
    import threading
    from tests.support.codex import CodexService, configure_codex
    class Held(CodexService):
        started = threading.Event()
        release = threading.Event()
        async def complete(self, prompt, schema, directory, choice):
            self.inputs.append(prompt)
            self.started.set()
            while not self.release.is_set():
                await asyncio.sleep(.01)
            source = json.loads(prompt.split('\n', 1)[1])['sources'][0]
            return json.dumps(dict(points=[dict(text='Обзор', sources=[dict(id=source['id'],url=source['url'])])]))
    service = Held()
    with TestClient(create_app(tmp_path, source=EventSource(), token='test', codex_client=service), headers=HEADERS) as client:
        configure_codex(client)
        setup_reader(client)
        identity = client.post('/api/digests/folder/demo', json={}).json()['id']
        wait_for(client, lambda: any('всех фидов' in e for e in client.get('/api/status').json()['errors']))
        assert not service.inputs
        client.patch('/api/feeds/demo', json={'public_ai': True})
        assert service.started.wait(2)
        client.patch('/api/feeds/demo', json={'public_ai': False})
        service.release.set()
        wait_for(client, lambda: any('всех фидов' in e for e in client.get('/api/status').json()['errors']))
        assert client.get('/api/materials/'+identity).status_code == 404
        assert len(client.get('/api/cards').json()) == 3
        assert not list((tmp_path / 'digests').glob('*.md'))


def test_whole_large_digest_obeys_global_prompt_after_partial_processing(tmp_path):
    from oko.collection.contracts import Material
    class Big(EventSource):
        async def fetch(self, publication):
            return Material('Тема', 'Текст '*10000)
    calls = []
    native = model_transport([])
    def reply(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        s = task['sources'][0]
        result = dict(points=[dict(text=text, sources=[dict(id=s['id'], url=s['url'])]) for text in ['Тема первая', 'Тема вторая']])
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
    with TestClient(create_app(tmp_path, source=Big(), token='test', ai_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        configure(client)
        client.patch('/api/settings/oko/global', json={'prompt_digest':'Ровно два тезиса.', 'originals':False})
        identity = client.post('/api/digests/folder/demo',json={}).json()['id']
        material = wait_for(client, lambda: m if (m:=client.get('/api/materials/'+identity).json()).get('digest') else None)
        assert len(material['digest']['points']) == 2
        assert calls[-1]['operation'] == 'digest_final'
        assert len(material['sources']) == 3


def test_folder_digest_stays_in_selected_folder_when_member_feed_moves(tmp_path):
    from tests.support.rss import rss
    def reply(request):
        if request.url.path.endswith('/feed'):
            return httpx.Response(200,text=rss('https://'+request.url.host+'/article'))
        return httpx.Response(200,text='<article>Открылась библиотека</article>')
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply), ai_transport=digest_transport([])), headers=HEADERS) as client:
        plan = client.post('/api/import',json={'opml':'<opml><body><outline text="Новости"><outline text="a" xmlUrl="https://a.test/feed"/><outline text="b" xmlUrl="https://b.test/feed"/></outline><outline text="Другая"/></body></opml>'}).json()
        folder = next(f['id'] for f in plan['folders'] if f['title']=='Новости')
        other = next(f['id'] for f in plan['folders'] if f['title']=='Другая')
        client.patch('/api/settings/oko/global',json={'events':False,'processor':'llamacpp'})
        client.put('/api/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client,lambda: len(c:=client.get('/api/cards').json())==2 and all(x['complete'] and not x.get('loading') for x in c))
        identity = client.post('/api/digests/folder/'+folder,json={}).json()['id']
        wait_for(client,lambda: client.get('/api/materials/'+identity).json().get('digest'))
        client.patch('/api/feeds/'+plan['feeds'][0]['id'],json={'folder_id':other})
        material = wait_for(client,lambda: m if (m:=client.get('/api/materials/'+identity).json()).get('digest') else None)
        assert material['folder_id'] == folder
        assert len(client.get('/api/cards?folder='+folder).json()) == 1
        assert client.get('/api/cards?folder='+other).json() == []
        assert len(material['sources']) == 2


def test_original_presentation_mode_inherits_independently_of_theses(tmp_path):
    with TestClient(create_app(tmp_path,source=EventSource(),token='test',ai_transport=digest_transport([])),headers=HEADERS) as client:
        configure(client)
        assert all(c['originals'] for c in client.get('/api/cards').json())
        client.patch('/api/settings/oko/global',json={'originals':False, 'theses':True})
        material = wait_for(client,lambda: m if (m:=client.get('/api/materials/'+client.get('/api/cards').json()[0]['id']).json()).get('theses') else None)
        assert material['originals'] is False and material['text']
        client.patch('/api/settings/feed/demo',json={'originals':True})
        assert client.get('/api/materials/'+material['id']).json()['originals'] is True
        assert client.get('/api/settings/feed/demo').json()['effective']['theses']['value'] is True


def test_large_intermediate_review_is_reduced_with_bounded_attributed_requests(tmp_path):
    from oko.collection.contracts import Material
    class Big(EventSource):
        async def fetch(self, publication):
            return Material('Тема', 'Текст '*10000)
    calls = []
    native = model_transport([])
    def reply(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        source = task['sources'][0]
        texts = ['Подробность '+('а'*3800)]*30 if task['operation']=='digest' else ['Общая тема']
        result = dict(points=[dict(text=text,sources=[dict(id=source['id'],url=source['url'])]) for text in texts])
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
    with TestClient(create_app(tmp_path,source=Big(),token='test',ai_transport=httpx.MockTransport(reply)),headers=HEADERS) as client:
        configure(client)
        identity = client.post('/api/digests/folder/demo',json={}).json()['id']
        material = wait_for(client,lambda: m if (m:=client.get('/api/materials/'+identity).json()).get('digest') else None)
        assert any(c['operation']=='digest_reduce' for c in calls)
        assert calls[-1]['operation']=='digest_final'
        assert all(sum(len(s['text'])+len(s['title']) for s in c['sources'])<60000 for c in calls)
        assert len(material['digest']['points'])==1 and len(material['sources'])==3
        client.post('/api/materials/'+identity+'/viewed')
        assert not list((tmp_path / 'digests').glob('*'))
