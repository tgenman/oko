from tests.support.subscriptions import add_sources
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.codex import CodexService, configure_codex
from tests.support.reader import HEADERS
from tests.support.reader import wait_for
from oko.collection.contracts import Publication, SourceError, Material


class FailedDownloads:
    fixed = False

    async def discover(self, cursor):
        return ([Publication('one', 'https://source.test/one', 1800000000),
                 Publication('two', 'https://source.test/two', 1800000001)] if cursor is None else []), 'done'

    async def fetch(self, publication):
        if not self.fixed:
            raise SourceError('Синтетический сбой загрузки', code='source-connection')
        return Material('Заголовок ' + publication.id, 'Текст')


def test_source_failures_group_objects_and_retry_is_not_completion(tmp_path):
    source = FailedDownloads()
    with TestClient(create_app(tmp_path, token='test', source=source), headers=HEADERS) as client:
        client.patch('/api/settings/oko/global', json={'theses': False, 'prompt_filters': ''})
        client.post('/api/tabs/reader/heartbeat')
        state = wait_for(client, lambda: (state if len((state := client.get('/api/status').json())['errors']) and
                                         len(state['problems']['current']) == 1 and len(state['problems']['current'][0]['objects']) == 2 else None))
        problem = state['problems']['current'][0]
        assert problem['code'] == 'source-connection'
        assert problem['section'] == 'object'
        assert state['problems']['count'] == 1
        assert {o['kind'] for o in problem['objects']} == {'material'}
        item = problem['objects'][0]['id']
        assert client.post('/api/materials/' + item + '/retry').status_code == 200
        retrying = client.get('/api/status').json()['problems']
        assert retrying['count'] == 1
        assert retrying['current'][0]['id'] == problem['id']
        source.fixed = True
        for obj in problem['objects']:
            client.post('/api/materials/' + obj['id'] + '/retry')
        wait_for(client, lambda: client.get('/api/status').json()['problems']['count'] == 0)


def test_model_problem_history_survives_restart_and_expires_from_completion(tmp_path):
    wall = [1800000000.0]
    options = dict(token='test', codex_client=CodexService(), wall_clock=lambda: wall[0])
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        first = client.get('/api/status').json()['problems']['current'][0]
        wall[0] += 60
        assert client.get('/api/status').json()['problems']['current'][0]['created_at'] == first['created_at']
        configure_codex(client)
        finished = client.get('/api/status').json()['problems']['recent'][0]
        assert finished['outcome'] == 'resolved'
        assert finished['finished_at'] == wall[0]
        assert client.delete('/api/debug').status_code == 200
        assert client.get('/api/status').json()['problems']['recent'][0]['id'] == finished['id']
    wall[0] += 86399
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/status').json()['problems']['recent'][0]['finished_at'] == finished['finished_at']
        wall[0] += 1
        assert client.get('/api/status').json()['problems']['recent'] == []


def test_missing_model_is_one_current_problem_and_settings_fix_it(tmp_path):
    with TestClient(create_app(tmp_path, token='test', codex_client=CodexService()), headers=HEADERS) as client:
        response = client.get('/api/v3/status')
        assert response.status_code == 200
        problems = response.json()['problems']
        assert len(problems['current']) == 1
        problem = problems['current'][0]
        assert problem['code'] == 'processor_not_configured'
        assert problem['recovery'] == [{'kind':'configure_processor','processor_id':'codex'}]
        assert problems['count'] == 1
        configure_codex(client)
        fixed = client.get('/api/v3/status').json()['problems']
        assert fixed['current'] == []
        assert fixed['count'] == 0


def test_settings_error_is_durable_and_successful_retry_completes_it(tmp_path):
    options = dict(token='test', codex_client=CodexService())
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure_codex(client)
        response = client.patch('/api/v3/settings/oko/global', json={'theses':'invalid'})
        assert response.status_code == 422
        client.post('/api/v3/command-observations',json={'id':'invalid-setting','context':{'operation':'settings_patch','target':{'scope':'oko','owner':'global'},'keys':['theses']},'result':'failed'})
        problems = client.get('/api/v3/status').json()['problems']
        assert problems['count'] == 1
        problem = problems['current'][0]
        assert problem['code'] == 'command_failed'
        assert problem['recovery'] == [{'kind':'change_setting','target':{'scope':'oko','owner':'global'},'keys':['theses']}]
        assert client.delete('/api/debug').status_code == 200
        assert client.get('/api/v3/status').json()['problems']['count'] == 1
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/status').json()['problems']['current'][0]['id'] == problem['id']
        assert client.patch('/api/v3/settings/oko/global', json={'theses':False}).status_code == 200
        result = client.get('/api/v3/status').json()['problems']
        assert result['count'] == 0
        assert result['recent'][0]['outcome'] == 'resolved'


def test_lost_successful_reply_is_reconciled_and_only_failed_draft_can_be_abandoned(tmp_path):
    with TestClient(create_app(tmp_path, token='test', codex_client=CodexService()), headers=HEADERS) as client:
        configure_codex(client)
        assert client.patch('/api/settings/oko/global', json={'theses':False}, headers={'X-Oko-Action':'lost-reply'}).status_code == 200
        response = client.post('/api/problems/actions', json={'id':'lost-reply','method':'PATCH',
            'path':'/api/settings/oko/global','fields':['theses'],'result':'unknown'})
        assert response.status_code == 200
        assert response.json()['result'] == 'succeeded'
        assert client.get('/api/status').json()['problems']['count'] == 0
        client.patch('/api/settings/oko/global', json={'theses':'invalid'})
        failed = client.get('/api/status').json()['problems']['current'][0]
        assert client.post('/api/problems/' + failed['id'] + '/abandon').status_code == 200
        result = client.get('/api/status').json()['problems']
        assert result['count'] == 0
        assert result['recent'][0]['outcome'] == 'irrelevant'


def test_shared_youtube_pause_across_feeds_is_one_problem(tmp_path):
    import httpx
    from oko.collection.youtube.access import CaptionsBlocked
    from tests.support.youtube import CHANNEL, PNG, VIDEO, feed_xml
    wall = [1900000000.0]
    class Captions:
        async def fetch(self, video):
            raise CaptionsBlocked()
    def external(request):
        if request.url.host == 'i.ytimg.com':
            return httpx.Response(200, content=PNG, headers={'content-type':'image/png'})
        channel = request.url.params.get('channel_id')
        return httpx.Response(200, text=feed_xml().replace(VIDEO, VIDEO if channel == CHANNEL else 'lmnopqrstuv'))
    options = dict(token='test', rss_enabled=True, rss_transport=httpx.MockTransport(external),
                   wall_clock=lambda:wall[0], youtube_captions=Captions())
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        for channel in (CHANNEL,'UC'+'b'*22):
            result=add_sources(client, [(f'https://www.youtube.com/feeds/videos.xml?channel_id={channel}', 'Science', 'Без папки')])
            assert result.status_code==200
            client.patch('/api/feeds/'+result.json()['feeds'][-1]['id'],json={'importance':'high'})
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client,lambda:client.get('/api/youtube').json()['paused'])
        problems=wait_for(client,lambda:(p if len((p:=client.get('/api/status').json()['problems'])['current'])==2 and
            len(next(x for x in p['current'] if x['code']=='youtube-admission')['objects'])==2 else None))
        pause=next(p for p in problems['current'] if p['code']=='youtube-admission')
        assert pause['section']=='wait' and pause['next_at']==wall[0]+900
        assert problems['count']==2


def test_viewed_material_scrubs_problem_objects_but_failed_delete_remains_current(tmp_path):
    from tests.support.reader import Clock, setup_reader
    from oko.collection.demo import DemoSource
    clock = Clock()
    failing = [False]
    def fault(point):
        if point == 'before_delete' and failing[0]:
            raise OSError('synthetic deletion failure')
    with TestClient(create_app(tmp_path, token='test', source=DemoSource(), fault=fault, clock=clock), headers=HEADERS) as client:
        item = setup_reader(client, theses=False)
        client.post('/api/materials/' + item + '/unsupported')
        before = client.get('/api/status').json()['problems']
        assert any(p['objects'][0]['id'] == item for p in before['current'])
        assert client.post('/api/problems/'+before['current'][0]['id']+'/abandon').status_code == 400
        failing[0] = True
        assert client.post('/api/materials/' + item + '/viewed').status_code == 200
        result = client.get('/api/status').json()['problems']
        assert any(p['code'] == 'delete' for p in result['current'])
        assert all(obj['id'] != item for p in result['current'] + result['recent'] for obj in p['objects'])
        old = next(p for p in result['recent'] if p['code'] == 'action-failed')
        assert old['outcome'] == 'irrelevant' and old['action'] is None
        failing[0] = False
        clock.now += 1.1
        wait_for(client,lambda:not any(p['code']=='delete' for p in client.get('/api/status').json()['problems']['current']))


def test_partial_material_is_visible_with_available_text(tmp_path):
    class Partial(FailedDownloads):
        async def fetch(self, publication):
            return Material('Частичный материал', 'Доступный текст', full=False, reason='Вложение недоступно')
    with TestClient(create_app(tmp_path, token='test', source=Partial()), headers=HEADERS) as client:
        client.patch('/api/settings/oko/global',json={'theses':False,'prompt_filters':''})
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client,lambda:len(cards:=client.get('/api/cards').json())==2 and all(c['complete'] for c in cards))
        problems=client.get('/api/status').json()['problems']
        assert problems['count']==2
        assert problems['current'][0]['code']=='material-incomplete'
        assert len(problems['current'][0]['objects'])==1
        assert client.get('/api/materials/'+problems['current'][0]['objects'][0]['id']).json()['text']=='Доступный текст'


def test_viewing_removes_scoped_diagnostic_content_from_server_and_disk(tmp_path):
    from oko.collection.demo import DemoSource
    from tests.support.reader import setup_reader
    with TestClient(create_app(tmp_path, token='test', source=DemoSource()), headers=HEADERS) as client:
        item=setup_reader(client,theses=False)
        title=client.get('/api/materials/'+item).json()['title']
        assert client.post('/api/debug',json={'source':'material','message':title,'material_ids':[item]}).status_code==200
        assert title in client.get('/api/debug').text
        assert client.post('/api/materials/'+item+'/viewed').status_code==200
        assert title not in client.get('/api/debug').text
        assert title not in (tmp_path/'debug.json').read_text()


def test_late_unknown_action_cannot_restore_viewed_material_reference(tmp_path):
    from oko.collection.demo import DemoSource
    from tests.support.reader import setup_reader
    with TestClient(create_app(tmp_path,token='test',source=DemoSource()),headers=HEADERS) as client:
        item=setup_reader(client,theses=False)
        assert client.post('/api/materials/'+item+'/viewed').status_code==200
        result=client.post('/api/problems/actions',json={'id':'late-unknown','method':'POST',
            'path':'/api/materials/'+item+'/theses/retry','fields':[],'result':'unknown'})
        assert result.status_code==200
        assert result.json()['result']=='abandoned'
        state=client.get('/api/status').json()['problems']
        assert all(obj['id']!=item for p in state['current']+state['recent'] for obj in p['objects'])


def test_late_server_failure_keeps_viewed_action_irrelevant_and_forgets_diagnostics(tmp_path):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from oko.collection.demo import DemoSource
    from tests.support.reader import setup_reader
    app=create_app(tmp_path,token='test',source=DemoSource())
    started, released=Event(),Event()
    private='Synthetic publication content to remove'
    @app.post('/api/materials/{identity}/slow')
    async def slow(identity: str):
        started.set()
        await asyncio.to_thread(released.wait,5)
        raise RuntimeError(private)
    with TestClient(app,headers=HEADERS) as client, ThreadPoolExecutor() as pool:
        item=setup_reader(client,theses=False)
        request=pool.submit(client.post,'/api/materials/'+item+'/slow')
        assert started.wait(2)
        try:
            assert client.post('/api/materials/'+item+'/viewed').status_code==200
        finally:
            released.set()
        assert request.result().status_code==500
        state=client.get('/api/status').json()['problems']
        assert not any(p['code']=='action-failed' for p in state['current'])
        assert private not in client.get('/api/debug').text
        assert private not in (tmp_path/'debug.json').read_text()




def test_normal_caption_spacing_is_work_without_a_problem(tmp_path):
    import httpx
    from tests.support.youtube import CHANNEL, VIDEO, PNG, feed_xml
    class Captions:
        calls = 0
        async def fetch(self, video):
            self.calls += 1
            return 'Synthetic captions'
    captions=Captions()
    def reply(request):
        if request.url.host=='i.ytimg.com':
            return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
        channel=request.url.params.get('channel_id') or 'UC'+request.url.params.get('playlist_id','')[4:]
        return httpx.Response(200,text=feed_xml().replace(VIDEO,VIDEO if channel==CHANNEL else 'lmnopqrstuv'))
    options=dict(token='test',rss_enabled=True,rss_transport=httpx.MockTransport(reply),youtube_captions=captions,
                 clock=lambda:100,wall_clock=lambda:1900000000,lease=1000000,grace=1000000)
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        client.patch('/api/settings/oko/global',json={'theses':False,'prompt_filters':''})
        for channel in (CHANNEL,'UC'+'b'*22):
            response=add_sources(client, [(f'https://www.youtube.com/feeds/videos.xml?channel_id={channel}', 'Source', 'Topic')])
            assert response.status_code==200
            for feed in response.json()['feeds']:
                client.patch('/api/feeds/'+feed['id'],json={'importance':'high'})
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client,lambda:captions.calls==1 and len(client.get('/api/cards').json())==2)
        waiting=next(card['id'] for card in client.get('/api/cards').json() if client.get('/api/materials/'+card['id']).json().get('captions')!='available')
        problems=client.get('/api/status').json()['problems']
        assert not any(obj['id']==waiting for p in problems['current'] for obj in p['objects'])
        assert not any(p['code']=='youtube-admission' for p in problems['current'])
