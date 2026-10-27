"""One Telegraph enrichment path shared by web and verified client collection."""
from tests.support.telegram import configure_fast_web
import httpx
import pytest

from tests.support.app import create_app
from tests.support.telegram import Telegram, settled
from tests.support.telegram_web import page
from tests.support.v3 import TestClient

POST = 'Original post https://telegra.ph/One https://www.telegra.ph/One https://telegra.ph/Two'


class TelegraphClient(Telegram):
    post_text = POST
    async def page(self, account, peer, after, since):
        rows = (await super().page(account,peer,after,since))['messages']
        rows[0]['text'] = self.post_text
        return dict(messages=rows,more=False)

    async def messages(self, account, peer, ids):
        return (await self.page(account,peer,0,0))['messages']

    async def history_page(self, account, peer, upper, before, since):
        return dict(messages=(await self.page(account,peer,0,0))['messages'],upper=11,next_before=11,more=False)


@pytest.mark.parametrize('access',['web','client'])
def test_direct_articles_are_complete_local_and_unique_without_recursive_crawling(tmp_path,access):
    calls = []
    async def response(request):
        calls.append(str(request.url))
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        if request.url.host == 'api.telegra.ph':
            assert request.url.params['return_content'] == 'true'
            name = request.url.path.split('/')[-1]
            content = [{'tag':'p','children':['Article '+name+' ', {'tag':'a','attrs':{'href':'https://example.org/no-follow'},'children':['ordinary link']}]}]
            if name == 'One':
                content.append({'tag':'figure','children':[{'tag':'img','attrs':{'src':'/file/photo.jpg'}},{'tag':'figcaption','children':['Photo caption']}]})
            return httpx.Response(200,json={'ok':True,'result':{'title':'Title '+name,'content':content}})
        if request.url.host == 'telegra.ph':
            return httpx.Response(200,content=b'article-photo',headers={'content-type':'image/jpeg'})
        assert request.url.host == 't.me'
        return httpx.Response(200,text=page([(11,POST)]))
    options = dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response))
    if access == 'client':
        options['telegram_client'] = TelegraphClient()
    with TestClient(create_app(tmp_path,**options),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        body = dict(source_locator='@fixture',folder='Inbox')
        if access == 'client':
            client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'fixture'})
            body['access'] = {'kind':'client','profile':'personal'}
        assert client.post('/api/v3/telegram/connect',json=body).status_code == 200
        from tests.support.reader import wait_for
        identity=settled(client)[0]['id']
        material=wait_for(client,lambda: value if (value:=client.get('/api/v3/materials/'+identity).json())['content']['completeness']['state']=='complete' else None)
        assert material['content']['text'].startswith(POST)
        linked = material['content']['linked_content']
        assert linked['post_text'] == POST
        assert [a['title'] for a in linked['articles']] == ['Title One', 'Title Two']
        assert linked['articles'][0]['media_urls'] == [material['content']['attachments'][0]['url']]
        assert material['content']['text'].count('Article One') == 1
        assert material['content']['text'].count('Article Two') == 1
        assert 'Photo caption' in material['content']['text']
        assert (material['content']['completeness']['state']=='complete') is True
        assert len(material['content']['attachments']) == 1
        assert client.get(material['content']['attachments'][0]['url']).content == b'article-photo'
        assert not any('example.org' in url for url in calls)
        assert sum('/getPage/One?' in url for url in calls) == 2  # Metadata, then media locator validation.
        assert sum('/getPage/Two?' in url for url in calls) == 1


@pytest.mark.parametrize('access',['web','client'])
def test_article_completion_and_retry_keep_previous_copy_without_false_edit(tmp_path,access):
    import time
    post = 'Original https://telegra.ph/One'
    phase = [0]
    async def response(request):
        if request.url.host == 'api.telegra.ph':
            if phase[0]==0:
                return httpx.Response(200,json={'ok':False,'error':'PAGE_NOT_FOUND'})
            if phase[0]==2:
                return httpx.Response(503)
            return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[
                {'tag':'p','children':['Version two' if phase[0]==3 else 'Version one']},
                {'tag':'img','attrs':{'src':'https://cdn.example/image?signature='+str(phase[0])}}]}})
        if request.url.host == 'cdn.example':
            return httpx.Response(200,content=b'known-article-image')
        return httpx.Response(200,text=page([(11,post)]))
    telegram = TelegraphClient()
    telegram.post_text = post
    options = dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response))
    if access=='client':
        options['telegram_client'] = telegram
    with TestClient(create_app(tmp_path,**options),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        body = dict(source_locator='@fixture',folder='Inbox')
        if access=='client':
            client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'fixture'})
            body['access'] = {'kind':'client','profile':'personal'}
        client.post('/api/v3/telegram/connect',json=body)
        deadline = time.monotonic()+2
        pending = None
        while time.monotonic()<deadline:
            cards = client.get('/api/v3/cards').json()
            if cards:
                pending = client.get('/api/v3/materials/'+cards[0]['id']).json()
                if 'Статья Telegraph недоступна' in pending['content'].get('completeness',{}).get('message',''):
                    break
            time.sleep(.02)
        assert pending and 'Статья Telegraph недоступна' in pending['content']['completeness'].get('message','')
        identity = pending['id']
        phase[0] = 1
        client.post('/api/v3/materials/'+identity+'/retry')
        settled(client)
        from tests.support.reader import wait_for
        loaded=wait_for(client,lambda: value if (value:=client.get('/api/v3/materials/'+identity).json())['content']['completeness']['state']=='complete' else None)
        assert (loaded['content']['completeness']['state']=='complete') is True and 'previous_versions' not in loaded['content']
        assert 'Version one' in loaded['content']['text']
        assert loaded['content']['revision_id'] != pending['content']['revision_id']
        image = loaded['content']['attachments'][0]['url']
        phase[0] = 2
        client.post('/api/v3/materials/'+identity+'/retry')
        settled(client)
        retained = client.get('/api/v3/materials/'+identity).json()
        assert retained['content']['text'] == loaded['content']['text'] and (retained['content']['completeness']['state']=='complete') is True
        assert 'previous_versions' not in retained['content'] and retained['content']['revision_id']==loaded['content']['revision_id']
        assert client.get(image).content == b'known-article-image'
        phase[0] = 3
        client.post('/api/v3/materials/'+identity+'/retry')
        settled(client)
        changed = client.get('/api/v3/materials/'+identity).json()
        assert 'Version two' in changed['content']['text'] and 'previous_versions' not in changed['content']


def test_iframe_is_a_visible_incomplete_link_and_is_never_requested(tmp_path):
    calls = []
    async def response(request):
        calls.append(request.url.host)
        if request.url.host == 'api.telegra.ph':
            return httpx.Response(200,json={'ok':True,'result':{'title':'Embed','content':[
                {'tag':'p','children':['Available text']},
                {'tag':'iframe','attrs':{'src':'http://127.0.0.1/private'}}]}})
        assert request.url.host == 't.me'
        return httpx.Response(200,text=page([(11,'Original https://telegra.ph/Embed')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        from tests.support.reader import wait_for
        identity=settled(client)[0]['id']
        material=wait_for(client,lambda: value if 'Available text' in (value:=client.get('/api/v3/materials/'+identity).json())['content']['text'] else None)
        assert (material['content']['completeness']['state']=='complete') is False
        assert 'Available text' in material['content']['text'] and 'http://127.0.0.1/private' in material['content']['text']
        assert '<iframe' not in material['content']['text']
        assert 'External embed not saved locally' in material['content']['completeness'].get('message','')
        assert '127.0.0.1' not in calls


def test_late_article_after_feed_deletion_cannot_download_media_or_publish(tmp_path):
    import asyncio
    import threading
    import time
    started,cancelled,release = threading.Event(),threading.Event(),threading.Event()
    assets = []
    async def response(request):
        if request.url.host == 'api.telegra.ph':
            started.set()
            try:
                while not release.is_set():
                    await asyncio.sleep(.01)
            except asyncio.CancelledError:
                cancelled.set()
                while not release.is_set():
                    await asyncio.sleep(.01)
            return httpx.Response(200,json={'ok':True,'result':{'title':'Late','content':[
                {'tag':'img','attrs':{'src':'https://cdn.example/late.jpg'}}]}})
        if request.url.host == 'cdn.example':
            assets.append(str(request.url))
            return httpx.Response(200,content=b'late')
        return httpx.Response(200,text=page([(11,'Original https://telegra.ph/Late')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        result = client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        assert started.wait(2)
        try:
            assert client.delete('/api/v3/feeds/'+result.json()['feed_id']).status_code == 200
            assert cancelled.is_set()
            release.set()
            deadline = time.monotonic()+2
            while time.monotonic()<deadline and (client.get('/api/v3/subscriptions').json()['feeds'] or list((tmp_path/'staging').rglob('incoming-*'))):
                time.sleep(.02)
            time.sleep(.05)
            assert not client.get('/api/v3/cards').json()
            assert not assets
        finally:
            release.set()
    assert not list((tmp_path/'staging').rglob('incoming-*'))


def test_installed_sdk_text_url_entities_preserve_utf16_labels_and_collect_article(tmp_path,monkeypatch):
    from oko.collection.telegram.source import LocalTelegram
    from tests.support.telegram_sdk import channel_sdk
    env,trace = channel_sdk(tmp_path,monkeypatch)
    sdk = env.parent/'sitecustomize.py'
    sdk.write_text(sdk.read_text()+'''
original_rows = FakeClient.rows
def linked_rows(self):
    row = next(message for message in original_rows(self) if message.id==131)
    row.message = '😀 Читать статью'
    row.entities = [types.MessageEntityTextUrl(offset=3,length=13,url='https://telegra.ph/Article')]
    return [row]
FakeClient.rows = linked_rows
''')
    calls = []
    async def response(request):
        calls.append(str(request.url))
        assert request.url.host=='api.telegra.ph'
        return httpx.Response(200,json={'ok':True,'result':{'title':'Linked','content':[{'tag':'p','children':['Full linked article']}]}})
    root = tmp_path/'reader'
    with TestClient(create_app(root,token='test',api_only=True,telegram_client=LocalTelegram(env,root),rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'fixture'})
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@forum',folder='Forum',
                    access={'kind':'client','profile':'personal'},initial_history={'mode':'count','count':'1'}))
        material = client.get('/api/v3/materials/'+settled(client)[0]['id']).json()
        assert material['content']['text'].startswith('😀 [Читать статью](https://telegra.ph/Article)')
        from tests.support.reader import wait_for
        material=wait_for(client,lambda: value if 'Full linked article' in (value:=client.get('/api/v3/materials/'+material['id']).json())['content']['text'] else None)
        assert 'Full linked article' in material['content']['text'] and (material['content']['completeness']['state']=='complete') is True
        assert len(calls)==1
        assert trace.is_file()


@pytest.mark.parametrize('filtered',[False,True])
@pytest.mark.parametrize('regenerating',[False,True])
def test_telegraph_transient_failure_preserves_theses_but_real_text_edit_invalidates_them(tmp_path,filtered,regenerating):
    import asyncio
    import json
    import threading
    import time
    from tests.support.filters import transport
    from tests.support.reader import wait_for
    calls=[]
    phase=[1]
    first_article_response=[True]
    block_ai=[False]
    regeneration_started=threading.Event()
    regeneration_finished=threading.Event()
    native=transport(calls,theses=True)
    async def model(request):
        late = False
        if request.url.path=='/v1/chat/completions':
            late=regenerating and block_ai[0] and not regeneration_started.is_set()
            if late:
                regeneration_started.set()
            deadline = time.monotonic()+15
            while block_ai[0] and time.monotonic()<deadline:
                try:
                    await asyncio.sleep(.005)
                except asyncio.CancelledError:
                    if not late:
                        raise
        result=native.handler(request)
        if request.url.path=='/v1/chat/completions':
            task=json.loads(json.loads(request.content)['messages'][1]['content'])
            if task['operation']=='theses':
                payload=result.json()
                generated=json.loads(payload['choices'][0]['message']['content'])
                generated['points'][0]['text']='Actual edit' if any('Actual edit' in source['text'] for source in task['sources']) else 'Original article'
                payload['choices'][0]['message']['content']=json.dumps(generated)
                result=httpx.Response(200,json=payload)
        if late:
            regeneration_finished.set()
        return result
    async def response(request):
        if request.url.host=='api.telegra.ph':
            if first_article_response[0]:
                first_article_response[0]=False
                await asyncio.sleep(.03)  # Root-only theses may finish before its linked article.
            if phase[0]==2:
                return httpx.Response(503)
            return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[
                {'tag':'p','children':['Actual edit' if phase[0]==3 else 'Original article']}]}})
        return httpx.Response(200,text=page([(11,'Post https://telegra.ph/Article')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response),
            ai_transport=httpx.MockTransport(model)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.patch('/api/v3/settings/oko/global',json={'theses':True,'prompt_filters':'Убирай рекламу' if filtered else '','processor':'llamacpp'})
        assert client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True}).status_code==200
        assert client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Web')).status_code==200
        identity=settled(client)[0]['id']
        path='/api/v3/materials/'+identity
        initial=wait_for(client,lambda: v if (v:=client.get(path).json())['theses']['result'] and 'Original article' in v['content']['text'] else None)
        accepted=initial['theses']['result']
        initial_calls=len([r for r in calls if r.url.path=='/v1/chat/completions'])
        if regenerating:
            block_ai[0]=True
            assert client.post(path+'/regenerate').status_code==200
            assert regeneration_started.wait(2)
        phase[0]=2
        assert client.post(path+'/retry').status_code==200
        wait_for(client,lambda: client.get('/api/v3/collection-status').json()['counts']['complete']==1)
        retained=client.get(path).json()
        assert retained['content']['revision_id']==initial['content']['revision_id']
        assert retained['theses']['result']==accepted
        assert retained['filter']['result']==initial['filter']['result']
        assert len([r for r in calls if r.url.path=='/v1/chat/completions'])==initial_calls
        assert retained['content']['text']==initial['content']['text']
        phase[0]=3
        block_ai[0]=True
        try:
            assert client.post(path+'/retry').status_code==200
            changed=wait_for(client,lambda: v if (v:=client.get(path).json())['content']['revision_id']!=initial['content']['revision_id'] else None)
            assert 'Actual edit' in changed['content']['text']
            assert changed['content']['changed'] is True
            assert changed['theses']['result'] is None
        finally:
            block_ai[0]=False
        regenerated=wait_for(client,lambda: client.get(path).json()['theses']['result'])
        assert 'Actual edit' in regenerated['points'][0]['text']
        if regenerating:
            assert regeneration_finished.wait(2)
            assert client.get(path).json()['theses']['result']==regenerated
        completed=[r for r in calls if r.url.path=='/v1/chat/completions']
        expected=initial_calls+(2 if filtered else 1)
        # An explicitly requested regeneration may already have reached the
        # external model when its cancellation races the actual source edit.
        assert expected<=len(completed)<=expected+int(regenerating)





@pytest.mark.parametrize('access', ['web', 'client'])
def test_failed_article_and_image_keep_inline_links_and_http_reasons(tmp_path, access):
    post = 'Original https://telegra.ph/Image https://telegra.ph/Unavailable'
    async def response(request):
        if request.url.host == 'api.telegra.ph':
            if request.url.path.endswith('/Unavailable'):
                return httpx.Response(503)
            return httpx.Response(200, json={'ok': True, 'result': {'title': 'Readable article', 'content': [
                {'tag': 'p', 'children': ['Before image']},
                {'tag': 'img', 'attrs': {'src': 'https://cdn.example/blocked.png?token=secret-value'}},
                {'tag': 'p', 'children': ['After image']}]}})
        if request.url.host == 'cdn.example':
            return httpx.Response(403)
        return httpx.Response(200, text=page([(11, post)]))
    telegram = TelegraphClient()
    telegram.post_text = post
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response))
    if access == 'client':
        options['telegram_client'] = telegram
    with TestClient(create_app(tmp_path, **options), headers={'Authorization': 'Bearer test'}) as client:
        configure_fast_web(client)
        body = dict(source_locator='@fixture', folder='Inbox')
        if access == 'client':
            client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref': 'fixture'})
            body['access'] = {'kind': 'client', 'profile': 'personal'}
        client.post('/api/v3/telegram/connect', json=body)
        import time
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            cards = client.get('/api/v3/cards').json()
            if cards:
                material = client.get('/api/v3/materials/' + cards[0]['id']).json()
                linked = material.get('content', {}).get('linked_content')
                if linked and len(linked['articles']) == 2 and all(article['error'] and 'HTTP' in article['error'] for article in linked['articles']):
                    break
            time.sleep(.02)
        else:
            raise AssertionError('Linked content was not saved')
        image, unavailable = linked['articles']
        assert image['text']=='Before image\n\nAfter image'
        assert image['error'] and 'HTTP 403' in image['error']
        assert 'secret-value' not in str(linked)
        assert 'https://telegra.ph/Unavailable' in linked['post_text']
        assert 'HTTP 503' not in linked['post_text']
        assert unavailable['error'] and 'HTTP 503' in unavailable['error']
        assert image['media_urls'] == []
        assert material['content']['completeness']['state'] == 'incomplete'
        assert '> [!load-error]' not in image['text']
        status = client.get('/api/v3/status').json()
        assert any(a.get('material_id') == material['id'] for p in status['problems']['current'] for a in p['affected'])
        feed = client.get('/api/v3/collection-status/' + material['feed_id']).json()
        assert feed['source']['operation_count'] == 0
        assert feed['source']['state'] == 'complete'
        before = material['content']['revision_id']
        assert client.post('/api/v3/materials/' + material['id'] + '/retry').status_code == 200
        settled(client)
        after = client.get('/api/v3/materials/' + material['id']).json()
        assert after['content']['revision_id'] == before


@pytest.mark.parametrize('url', ['https://telegra.ph/Query?foo=1', 'https://telegra.ph/Query#section', 'http://telegra.ph/Query', 'https://www.telegra.ph/Query'])
def test_article_error_preserves_original_markdown_destination(tmp_path, url):
    async def response(request):
        if request.url.host == 'api.telegra.ph':
            return httpx.Response(503)
        return httpx.Response(200, text=page([(11, f'Post <a href="{url}">Read article</a> tail')]))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization': 'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        import time
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            cards = client.get('/api/v3/cards').json()
            if cards:
                material = client.get('/api/v3/materials/' + cards[0]['id']).json()
                linked = material.get('content', {}).get('linked_content')
                if linked and linked['articles'][0]['error']:
                    break
            time.sleep(.02)
        else:
            raise AssertionError('Failed article was not saved')
        assert f'[Read article]({url})' in linked['post_text']
        assert '> [!load-error]' not in linked['post_text']
        assert linked['articles'][0]['error'] and 'HTTP 503' in linked['articles'][0]['error']
        assert linked['post_text'].endswith(' tail')
        assert linked['post_text'].count('Read article') == 1
