"""Telegram reply context through the reader HTTP interface."""
from tests.support.telegram import configure_fast_web
import httpx
import pytest

from tests.support.app import create_app
from tests.support.telegram import settled
from tests.support.telegram_web import page
from tests.support.v3 import TestClient


@pytest.mark.parametrize('href', ['/fixture/10', '10', 'https://t.me/fixture/10'])
def test_reply_is_separate_from_own_text_and_survives_restart(tmp_path, href):
    reply = '''<a class="tgme_widget_message_reply" href="/fixture/10">
<div class="tgme_widget_message_author"><span class="tgme_widget_message_author_name">Reply author</span></div>
<div class="tgme_widget_message_text js-message_reply_text">Quoted<br> context <img alt="🙂"> …</div></a>'''
    async def response(request):
        return httpx.Response(200, text=page([(11, reply.replace('href="/fixture/10"', 'href="'+href+'"')+'Own first line<br><br>Own paragraph')]))
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response))
    headers = {'Authorization': 'Bearer test'}
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        configure_fast_web(client)
        assert client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture',folder='Inbox')).status_code == 200
        card = settled(client)[0]
        assert card['title'] == 'Own first line'
        material = client.get('/api/v3/materials/'+card['id']).json()
        assert material['content']['text'] == 'Own first line\n\nOwn paragraph'
        assert material['content']['reply_preview'] == dict(author='Reply author', text='Quoted context 🙂 …', source_url='https://t.me/fixture/10')
        frame = client.get('/api/v3/reading/materials/'+card['id']).json()
        entity = next(r['data'] for r in frame['resources'] if r['kind']=='material')
        assert entity['content']['reply_preview'] == material['content']['reply_preview']
        assert entity['title'] == card['title']
        assert material['content']['completeness']['state'] == 'complete'
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        configure_fast_web(client)
        saved = client.get('/api/v3/materials/'+card['id']).json()
        assert saved['content']['reply_preview'] == material['content']['reply_preview']


def test_both_ai_operations_receive_reply_and_reply_changes_invalidate_results(tmp_path):
    import json
    from tests.support.models import model_transport
    from tests.support.presence import presence
    from tests.support.reader import wait_for
    calls = []
    native = model_transport([])
    phase = ['CONTEXT_ONE']
    def model(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        source = task['sources'][0]
        if task['operation'] == 'filter':
            result = dict(hidden=source.get('reply_preview', {}).get('text')=='HIDE_CONTEXT', reason='Context evaluated', removed=[])
        else:
            result = dict(points=[dict(text='Summary '+phase[0], sources=[dict(id=source['id'],url=source['url'])])])
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
    async def response(request):
        reply = '<a class="tgme_widget_message_reply" href="https://t.me/fixture/10"><span class="tgme_widget_message_author_name">Other author</span><div class="js-message_reply_text">'+phase[0]+'</div></a>'
        return httpx.Response(200,text=page([(11,reply+'Own text marker')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response),ai_transport=httpx.MockTransport(model)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.put('/api/v3/processors/llamacpp',json=dict(endpoint='http://localhost:9931',model='local:latest',local_execution=True))
        client.patch('/api/v3/settings/oko/global',json=dict(processor='llamacpp',theses=True,prompt_filters='Evaluate everything'))
        presence(client,'reader')
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        card = settled(client)[0]
        url = '/api/v3/materials/'+card['id']
        first = wait_for(client,lambda: m if (m:=client.get(url).json())['theses']['result'] else None)
        assert {c['operation'] for c in calls} == {'filter','theses'}
        assert all(c['sources'][0]['reply_preview']['text']=='CONTEXT_ONE' and 'Own text marker' in c['sources'][0]['text'] for c in calls)
        assert all(c['sources'][0]['reply_preview']['author']=='Other author' and c['sources'][0]['reply_preview']['source_url']=='https://t.me/fixture/10' for c in calls)
        count = len(calls)
        phase[0] = 'CONTEXT_TWO'
        assert client.post(url+'/retry').status_code == 200
        second = wait_for(client,lambda: m if (m:=client.get(url).json())['theses']['result'] and m['theses']['result']['points'][0]['text']=='Summary CONTEXT_TWO' else None)
        assert second['content']['revision_id'] != first['content']['revision_id']
        assert len(calls) == count+2
        old = second['content']['previous_versions'][0]
        assert client.get(old['url']).json()['reply_preview']['text'] == 'CONTEXT_ONE'
        count = len(calls)
        client.post(url+'/retry')
        wait_for(client,lambda: not client.get(url).json()['content']['loading'])
        assert len(calls) == count
        phase[0] = 'HIDE_CONTEXT'
        client.post(url+'/retry')
        wait_for(client,lambda: (result:=client.get(url).json()['filter']['result']) and result['hidden'])
        assert calls[-1]['operation'] == 'filter'


@pytest.mark.parametrize('author,fragment,address,expected', [
    ('', 'Only quoted context', 'javascript:alert(1)', None),
    ('Named author', '', '', None),
    ('Named author', 'Readable malformed URL context', 'https://t.me/fixture/10 bad', None),
    ('', 'Quote with <b>formatting</b><script>bad()</script>', 'https://t.me/fixture/10', 'https://t.me/fixture/10'),
])
def test_reply_only_and_partial_preview_have_no_invented_media(tmp_path,author,fragment,address,expected):
    async def response(request):
        markup = f'<a class="tgme_widget_message_reply" href="{address}"><span class="tgme_widget_message_author_name">{author}</span><div class="js-message_reply_text">{fragment}</div></a>'
        return httpx.Response(200,text=page([(11,markup)]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        card = settled(client)[0]
        m = client.get('/api/v3/materials/'+card['id']).json()
        assert card['title'] == m['title'] == 'Ответ на сообщение'
        assert m['content']['text'] == ''
        assert m['content']['completeness']['state'] == 'complete'
        assert m['content']['attachments'] == []
        assert m['content']['reply_preview']['source_url'] == expected
        assert m['content']['reply_preview']['author'] == (author or None)
        assert 'bad()' not in m['content']['reply_preview']['text']


@pytest.mark.parametrize('fragment,expected_calls', [('Quote-only marker', 2), ('', 0)])
def test_ai_can_process_reply_without_own_text_but_skips_metadata_only(tmp_path,fragment,expected_calls):
    import json
    from tests.support.models import model_transport
    from tests.support.presence import presence
    from tests.support.reader import wait_for
    tasks=[]
    native=model_transport([])
    def model(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task=json.loads(json.loads(request.content)['messages'][1]['content'])
        tasks.append(task)
        source=task['sources'][0]
        result=dict(hidden=False,reason='',removed=[]) if task['operation']=='filter' else dict(points=[dict(text='Quote-only summary',sources=[dict(id=source['id'],url=source['url'])])])
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
    async def response(request):
        return httpx.Response(200,text=page([(11,'<a class="tgme_widget_message_reply" href="https://t.me/fixture/10"><span class="tgme_widget_message_author_name">Author only</span><div class="js-message_reply_text">'+fragment+'</div></a>')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response),ai_transport=httpx.MockTransport(model)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.put('/api/v3/processors/llamacpp',json=dict(endpoint='http://localhost:9931',model='local:latest',local_execution=True))
        client.patch('/api/v3/settings/oko/global',json=dict(theses=False,prompt_filters=''))
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        card=settled(client)[0]
        client.patch('/api/v3/settings/oko/global',json=dict(processor='llamacpp',theses=True,prompt_filters='Evaluate all text'))
        presence(client,'reader')
        if expected_calls:
            wait_for(client,lambda: client.get('/api/v3/materials/'+card['id']).json()['theses']['result'])
        wait_for(client,lambda: client.get('/api/v3/status').json()['work']==[{'phase':'idle'}])
        assert len(tasks)==expected_calls
        assert all(task['sources'][0]['text']=='' and task['sources'][0]['reply_preview']['text']==fragment for task in tasks)


def test_legacy_copy_can_be_processed_offline_then_reloaded_without_losing_history(tmp_path):
    import json
    from oko.collection.contracts import Material,Publication,DiscoveryResult,FetchResult,FetchRequirements,FetchResources,ReplyPreview,SourceError
    from tests.support.models import model_transport
    from tests.support.presence import presence
    from tests.support.reader import wait_for
    class LegacySource:
        fetch_requirements=FetchRequirements()
        changes=None
        offline=False
        structured=False
        fetches=0
        async def discover(self,cursor):
            return DiscoveryResult((Publication('1','https://t.me/fixture/11',1800000000),) if cursor is None else (), '1')
        async def fetch(self,publication,resources=FetchResources()):
            self.fetches+=1
            if self.offline: raise SourceError('Synthetic source unavailable')
            return FetchResult(Material('Own text' if self.structured else '> [Author', 'Own text' if self.structured else '> [Author\n\nQuoted legacy text](https://t.me/fixture/10)\n\nOwn text', reply_preview=ReplyPreview(author='Author',text='Quoted legacy text',source_url='https://t.me/fixture/10') if self.structured else None))
    source=LegacySource()
    calls=[]
    options=dict(token='test',api_only=True,source=source,ai_transport=model_transport(calls))
    with TestClient(create_app(tmp_path,**options),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.patch('/api/v3/settings/oko/global',json=dict(theses=False,prompt_filters=''))
        card=settled(client)[0]
        url='/api/v3/materials/'+card['id']
        original=client.get(url).json()
        assert original['content']['reply_preview'] is None
        source.offline=True
        fetches=source.fetches
        client.put('/api/v3/processors/llamacpp',json=dict(endpoint='http://localhost:9931',model='local:latest',local_execution=True))
        client.patch('/api/v3/settings/oko/global',json=dict(processor='llamacpp',theses=True))
        presence(client,'reader')
        wait_for(client,lambda: client.get(url).json()['theses']['result'])
        task=json.loads(json.loads(next(c for c in calls if c.url.path=='/v1/chat/completions').content)['messages'][1]['content'])
        assert 'Quoted legacy text' in task['sources'][0]['text'] and 'Own text' in task['sources'][0]['text']
        assert source.fetches==fetches
        source.offline=False
        source.structured=True
        client.post(url+'/retry')
        fixed=wait_for(client,lambda: m if (m:=client.get(url).json())['content']['reply_preview'] else None)
        assert fixed['content']['text']=='Own text'
        assert fixed['content']['previous_versions']
        previous=client.get(fixed['content']['previous_versions'][0]['url']).json()
        assert previous['reply_preview'] is None and previous['text']==original['content']['text']
