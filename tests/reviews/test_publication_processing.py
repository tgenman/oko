"""Latest-publication processing through real HTTP and synthetic source/model."""
import json
from tests.support.storage import database
import httpx
import pytest

from tests.support.publication_pipeline import publication_root
from tests.support.models import model_transport
from tests.support.filters import FilterSource
import time
from tests.support.app import create_app
from tests.support.presence import presence
from tests.support.reader import HEADERS, wait_for
from tests.support.telegram import configure_fast_web, settled
from tests.support.telegram_web import page
from tests.support.v3 import TestClient


def coordinated_transport(calls):
    native = model_transport([])

    def respond(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        sources = task['sources']
        if task['operation'] == 'filter':
            text = sources[0]['text']
            result = dict(hidden=text.startswith('Реклама:'), reason='Реклама' if 'Реклама:' in text else '',
                          removed=['Реклама: купите курс.'] if text.startswith('Открылась') else [])
        else:
            result = dict(points=[dict(text=sources[0]['text'], sources=[dict(id=sources[0]['id'], url=sources[0]['url'])])])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result)}}]})

    return httpx.MockTransport(respond)


def stored(root,item):
    with database(root) as db:
        row=dict(db.execute('SELECT * FROM items WHERE id = %s',(item,)).fetchone())
    for key in ('content','filtered_content','summarized_content','processing_metadata'):
        row[key]=json.loads(row[key])
    return row


def setup(client):
    configure_fast_web(client)
    client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
    client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':'Убери рекламу.'})
    presence(client,'reader')
    connected=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
    assert connected.status_code==200,connected.text


def test_results_have_atomic_metadata_and_media_change_preserves_processing(tmp_path):
    root=publication_root(tmp_path)
    calls=[]
    photo=['first']
    def source(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=request.url.path.encode(),headers={'content-type':'image/jpeg'})
        media='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/'+photo[0]+'.jpg\')"></a>'
        posts=[(11,'Открылась выставка. Реклама: купите курс.'+media),(12,'Изменён закон о выборах.'),(13,'Реклама: купите курс.')]
        before=int(request.url.params.get('before','0'))
        return httpx.Response(200,text=page([(identity,text) for identity,text in posts if not before or identity<before]))
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(source),ai_transport=coordinated_transport(calls))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global',json={'originals':False})
        setup(client)
        cards=wait_for(client,lambda:rows if len(rows:=client.get('/api/v3/cards').json())==2 and all(c['theses']['result'] for c in rows) else None)
        assert all('Реклама:' not in c['snippet'] for c in cards)
        assert {'filter','theses'}<={c['operation'] for c in calls}
        card=next(c for c in cards if c['source_url'].endswith('/11'))
        item=card['id']
        row=stored(root,item)
        assert row['status']=='ready'
        for operation in ('filter','summary'):
            metadata=row['processing_metadata'][operation]
            assert {'input_hash','settings_hash','processor','model','completed_at'}<=metadata.keys()
            assert metadata['processor']=='llamacpp' and metadata['model']=='local:latest'
        assert row['processing_metadata']['summary']['parameters']=={'temperature':0,'max_tokens':4096,'reasoning_effort':'none'}
        assert row['filtered_content']['removed']==[{'publication_id':item,'block_index':0,'text':'Реклама: купите курс.'}]
        assert all(b['source_publication_id']==item for b in row['filtered_content']['blocks'])
        accepted=row['processing_metadata']
        count=len(calls)
        original=client.get('/api/v3/materials/'+item).json()
        assert original['originals'] is False and original['content']['text'] and original['theses']['result']
        client.patch('/api/v3/settings/feed/'+card['feed_id'],json={'originals':True})
        assert client.get('/api/v3/materials/'+item).json()['originals'] is True
        assert client.get('/api/v3/settings/feed/'+card['feed_id']).json()['effective']['theses']['value'] is True
        assert client.get('/api/v3/materials/'+item).json()['theses']['result']==original['theses']['result']
        assert len(calls)==count
        photo[0]='second'
        client.post('/api/v3/materials/'+item+'/retry')
        settled(client,count=2)
        updated=stored(root,item)
        assert updated['processing_metadata']==accepted
        ids=json.loads(updated['media_ids'])
        assert all(b['media_id'] in ids for b in updated['filtered_content']['blocks'] if b['type']=='media')
        assert len(calls)==count

        wait_for(client,lambda:client.get('/api/v3/status').json()['work']==[{'phase':'idle'}])
        saved=client.get('/api/v3/cards').json()
        time.sleep(1.2)  # More than two periodic reconciliation passes.
        assert len(calls)==count
        assert [c['theses'] for c in client.get('/api/v3/cards').json()]==[c['theses'] for c in saved]
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        wait_for(client,lambda:client.get('/api/v3/status').json()['work']==[{'phase':'idle'}])
        assert [c['theses'] for c in client.get('/api/v3/cards').json()]==[c['theses'] for c in saved]
        assert stored(root,item)['processing_metadata']==accepted
        assert len(calls)==count


def test_hidden_has_empty_blocks_original_and_no_restore_or_summary(tmp_path):
    root=publication_root(tmp_path)
    calls=[]
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Реклама: купите курс.')])) )
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        setup(client)
        card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards?show_hidden=true').json() if (c['filter']['result'] or {}).get('hidden')),None))
        row=stored(root,card['id'])
        assert row['status']=='filter_out' and row['filtered_content']['blocks']==[]
        assert 'can_restore' not in card['filter']['result']
        assert row['filtered_content']['reason']
        assert row['content']['blocks'][0]['text']=='Реклама: купите курс.'
        assert not any(c['operation']=='theses' for c in calls)
        assert client.post('/api/v3/materials/'+card['id']+'/filter/restore').status_code==404


def test_confirmed_empty_input_completes_without_model(tmp_path):
    root=publication_root(tmp_path)
    calls=[]
    text=['']
    photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/image.jpg\')"></a>'
    def source(request):
        return httpx.Response(200,content=b'photo',headers={'content-type':'image/jpeg'}) if request.url.host=='cdn.example' else httpx.Response(200,text=page([(11,text[0]+photo)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(source),ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        setup(client)
        item=settled(client)[0]['id']
        wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['theses']['result'])
        row=stored(root,item)
        assert row['status']=='ready'
        assert row['summarized_content']=={'points':[],'extra':{}}
        assert row['filtered_content']['blocks']==[] and row['filtered_content']['removed']==[]
        assert row['processing_metadata']['filter']['reason']=='no_input'
        assert row['processing_metadata']['summary']['reason']=='no_input'
        assert calls==[]

        accepted=row['processing_metadata']
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(source),ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        client.post('/api/v3/collect')
        settled(client)
        assert stored(root,item)['processing_metadata']==accepted
        assert calls==[]
        text[0]='Text appeared'
        client.post('/api/v3/materials/'+item+'/retry')
        wait_for(client,lambda:r and r['points'] if (r:=client.get('/api/v3/materials/'+item).json()['theses']['result']) is not None else None)
        assert {call['operation'] for call in calls}=={'filter','theses'}
        assert all(call['sources'][0]['text']=='Text appeared' for call in calls)


def test_unavailable_input_waits_without_model_and_processes_when_retrieved(tmp_path):
    from tests.support.telegram_web import mark_post_unavailable
    root=publication_root(tmp_path)
    calls=[]
    available=[False]
    def source(request):
        html=page([(11,'Retrieved text' if available[0] else '')])
        return httpx.Response(200,text=html if available[0] else mark_post_unavailable(html,'fixture/11'))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(source),ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        setup(client)
        item=wait_for(client,lambda:(c[0]['id'] if (c:=client.get('/api/v3/cards').json()) else None))
        wait_for(client,lambda:r['status']=='pending_collection' and r['content']!={} and r['complete'] if (r:=stored(root,item)) else False)
        row=stored(root,item)
        assert row['filtered_content']=={} and row['summarized_content']=={}
        assert calls==[]
        available[0]=True
        client.post('/api/v3/materials/'+item+'/retry')
        wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['theses']['result'])
        assert all(c['sources'][0]['text']=='Retrieved text' for c in calls)


def test_rule_change_rejects_late_filter_and_uses_one_new_feed_snapshot(tmp_path):
    import asyncio
    import threading
    root=publication_root(tmp_path)
    calls=[]
    started,release=threading.Event(),threading.Event()
    native=coordinated_transport(calls)
    first=[True]
    async def model(request):
        if request.url.path=='/v1/chat/completions':
            task=json.loads(json.loads(request.content)['messages'][1]['content'])
            if task['operation']=='filter' and first[0]:
                first[0]=False
                calls.append(task)
                started.set()
                while not release.is_set():
                    try:
                        await asyncio.sleep(.01)
                    except asyncio.CancelledError:
                        pass
                return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(dict(hidden=True,reason='Old rule',removed=[]))}}]})
        return native.handler(request)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Useful text')])) )
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            setup(client)
            assert started.wait(3)
            client.patch('/api/v3/settings/oko/global',json={'prompt_filters':'Keep the text.','prompt_theses':'New summary rule.'})
            release.set()
            card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['theses']['result']),None))
            assert not card['filter']['result']['hidden']
            assert len([c for c in calls if c['operation']=='filter'])==2
            assert calls[0]['prompt']=='Убери рекламу.'
            assert all(c['prompt']==('Keep the text.' if c['operation']=='filter' else 'New summary rule.') for c in calls[1:])
            chosen=stored(root,card['id'])['processing_metadata']['_pass']
            assert chosen['feed_id']==card['feed_id']
            assert chosen['settings']['values']['prompt_filters']=='Keep the text.'
            assert chosen['settings']['values']['prompt_theses']=='New summary rule.'
        finally:
            release.set()


def test_permission_revoked_during_check_does_not_transfer_canonical_source(tmp_path):
    import asyncio
    import threading
    root=publication_root(tmp_path)
    started,release=threading.Event(),threading.Event()
    calls=[]
    native=coordinated_transport(calls)
    async def model(request):
        if request.url.path=='/props':
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(.01)
                except asyncio.CancelledError:
                    pass
        return native.handler(request)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Private canonical source')])) )
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
            feed=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox')).json()['feed_id']
            card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['availability']=='available'),None))
            item=card['id']
            client.patch('/api/v3/feeds/'+feed,json={'public_ai':True})
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':False})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':'Filter'})
            assert started.wait(3)
            client.patch('/api/v3/feeds/'+feed,json={'public_ai':False})
            release.set()
            wait_for(client,lambda:any(w.get('reason')=='public_ai_not_allowed' for w in client.get('/api/v3/status').json()['work']))
            assert calls==[]
            assert client.get('/api/v3/status').json()['problems']['current']==[]
            assert all(c['theses']['result'] is None for c in client.get('/api/v3/cards').json())
            item=client.get('/api/v3/cards').json()[0]['id']
            assert stored(root,item)['filtered_content']=={}
            assert stored(root,item)['summarized_content']=={}
        finally:
            release.set()


def test_processing_does_not_wait_for_the_media_lane(tmp_path):
    import asyncio
    import threading
    root=publication_root(tmp_path)
    started,release=threading.Event(),threading.Event()
    calls=[]
    async def source(request):
        if request.url.host=='cdn.example':
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return httpx.Response(200,content=b'image',headers={'content-type':'image/jpeg'})
        media='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/image.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,'Useful text'+media)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(source),ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        try:
            setup(client)
            assert started.wait(3)
            card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['theses']['result']),None))
            assert stored(root,card['id'])['status']=='ready'
            assert card['attachments']==[]
            assert {c['operation'] for c in calls}=={'filter','theses'}
            metadata=stored(root,card['id'])['processing_metadata']
            release.set()
            wait_for(client,lambda:client.get('/api/v3/materials/'+card['id']).json()['content']['attachments'])
            assert stored(root,card['id'])['processing_metadata']==metadata
        finally:
            release.set()


def test_article_retrieval_error_is_not_sent_as_publication_text(tmp_path):
    root=publication_root(tmp_path)
    calls=[]
    broken=[True]
    native=coordinated_transport(calls)
    def model(request):
        response=native.handler(request)
        if request.url.path=='/v1/chat/completions':
            task=json.loads(json.loads(request.content)['messages'][1]['content'])
            if task['operation']=='theses':
                value=response.json()
                result=json.loads(value['choices'][0]['message']['content'])
                result['points'][0]['text']='Useful summary'
                value['choices'][0]['message']['content']=json.dumps(result)
                return httpx.Response(200,json=value)
        return response
    def source(request):
        if request.url.host=='api.telegra.ph':
            if broken[0]:
                return httpx.Response(503)
            return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[{'tag':'p','children':['Retrieved article']}]}})
        return httpx.Response(200,text=page([(11,'Useful post <a href="https://telegra.ph/Fixture">article</a>')]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(source),ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        setup(client)
        card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['theses']['result']),None))
        assert all('Статья ещё не сохранена' not in call['sources'][0]['text'] for call in calls)
        assert all('Telegraph:' not in call['sources'][0]['text'] for call in calls)
        broken[0]=False
        client.post('/api/v3/materials/'+card['id']+'/retry')
        wait_for(client,lambda:any('Retrieved article' in source['text'] for call in calls for source in call['sources']))


@pytest.mark.parametrize('invalid',['quote_removal','unknown_reference'])
def test_invalid_model_output_does_not_become_a_result(tmp_path,invalid):
    root=publication_root(tmp_path)
    calls=[]
    native=coordinated_transport(calls)
    broken=[True]
    def model(request):
        response=native.handler(request)
        if broken[0] and request.url.path=='/v1/chat/completions':
            task=json.loads(json.loads(request.content)['messages'][1]['content'])
            result=None
            if invalid=='quote_removal' and task['operation']=='filter':
                result=dict(hidden=False,reason='Wrong author',removed=['Quoted context'])
            if invalid=='unknown_reference' and task['operation']=='theses':
                result=dict(points=[dict(text='Invalid attribution',sources=[dict(id='unknown',url='https://example.org/unknown')])])
            if result is not None:
                return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
        return response
    reply='<a class="tgme_widget_message_reply" href="https://t.me/fixture/10"><span class="tgme_widget_message_author_name">Other author</span><div class="js-message_reply_text">Quoted context</div></a>'
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,reply+'Own text')])) )
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        setup(client)
        stage='filter' if invalid=='quote_removal' else 'theses'
        card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c[stage]['diagnostic_message']),None))
        row=stored(root,card['id'])
        assert row['filtered_content' if stage=='filter' else 'summarized_content']=={}
        assert client.get('/api/v3/materials/'+card['id']).json()['content']['reply_preview']['text']=='Quoted context'
        broken[0]=False
        assert client.post('/api/v3/materials/'+card['id']+'/'+stage+'/retry').status_code==200
        wait_for(client,lambda:client.get('/api/v3/materials/'+card['id']).json()['theses']['result'])
        assert [b['text'] for b in stored(root,card['id'])['filtered_content']['blocks'] if b['type']=='quote']==['Quoted context']


def test_local_retry_records_the_model_actually_used_and_ready_result_survives_choice_change(tmp_path):
    from tests.support.models import model_transport
    root=publication_root(tmp_path)
    selected=['local:latest']
    broken=[True]
    sent=[]
    native=model_transport([])
    def model(request):
        if request.url.path=='/v1/models':
            return httpx.Response(200,json={'data':[{'id':selected[0],'owned_by':'llamacpp','meta':{'size':100}}]})
        if request.url.path=='/v1/chat/completions':
            sent.append(json.loads(request.content)['model'])
            if broken[0]:
                return httpx.Response(503)
        return native.handler(request)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Useful text')])) )
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':selected[0],'local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':'','retry_initial_seconds':1,'retry_max_seconds':1})
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['theses']['diagnostic_message']),None))
        selected[0]='local:second'
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':selected[0],'local_execution':True})
        broken[0]=False
        wait_for(client,lambda:client.get('/api/v3/materials/'+card['id']).json()['theses']['result'])
        metadata=stored(root,card['id'])['processing_metadata']
        assert metadata['summary']['model']==sent[-1]=='local:second'
        count=len(sent)
        selected[0]='local:third'
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':selected[0],'local_execution':True})
        settled(client)
        assert stored(root,card['id'])['processing_metadata']==metadata
        assert len(sent)==count


def test_failed_regeneration_retains_only_current_results_across_restart(tmp_path):
    root=publication_root(tmp_path)
    calls=[]
    broken=[False]
    text=['Original text']
    native=coordinated_transport(calls)
    def model(request):
        if broken[0] and request.url.path=='/v1/chat/completions':
            return httpx.Response(503)
        return native.handler(request)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,text[0])])) )
    options=dict(token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        setup(client)
        card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['theses']['result']),None))
        item=card['id']
        accepted=stored(root,item)
        assert client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:new','local_execution':True}).status_code==200
        broken[0]=True
        assert client.post('/api/v3/materials/'+item+'/regenerate').status_code==200
        wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['theses']['diagnostic_message'])
        assert stored(root,item)['processing_metadata']==accepted['processing_metadata']
        assert stored(root,item)['summarized_content']==accepted['summarized_content']
        assert not list((root/'content'/item).glob('*/accepted-theses-*.md'))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        assert client.get('/api/v3/materials/'+item).json()['theses']['result']==card['theses']['result']
        assert stored(root,item)['summarized_content']==accepted['summarized_content']
        text[0]='Changed text'
        client.post('/api/v3/materials/'+item+'/retry')
        wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['content']['text']=='Changed text')
        assert stored(root,item)['summarized_content']=={}
        assert client.get('/api/v3/materials/'+item).json()['theses']['result'] is None


def test_feed_rules_are_saved_and_disabled_stages_are_skipped(tmp_path):
    root=publication_root(tmp_path)
    calls=[]
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Useful text')])) )
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        setup(client)
        card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['theses']['result']),None))
        item,feed=card['id'],card['feed_id']
        client.patch('/api/v3/settings/feed/'+feed,json={'theses':False,'prompt_filters':''})
        assert client.get('/api/v3/materials/'+item).json()['theses']['result'] is None
        row=wait_for(client,lambda:r if (r:=stored(root,item))['status']=='ready' and r['filtered_content']=={} and r['summarized_content']=={} else None)
        assert row['status']=='ready' and row['filtered_content']=={} and row['summarized_content']=={}
        count=len(calls)
        client.patch('/api/v3/settings/feed/'+feed,json={'prompt_filters':'Убери рекламу.','theses':True})
        wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['theses']['result'])
        assert [c['operation'] for c in calls[count:]]==['filter','theses']
        client.patch('/api/v3/settings/feed/'+feed,json={'theses':False,'prompt_filters':''})
        count=len(calls)
        client.patch('/api/v3/settings/feed/'+feed,json={'prompt_filters':'Only filtering'})
        wait_for(client,lambda:any(card['id']==item and card['filter']['attempt']['state']=='ready'
                                   for card in client.get('/api/v3/cards').json()))
        assert [c['operation'] for c in calls[count:]]==['filter']
        assert stored(root,item)['summarized_content']=={}
        count=len(calls)
        client.patch('/api/v3/settings/feed/'+feed,json={'prompt_filters':'','theses':True})
        wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['theses']['result'])
        assert [c['operation'] for c in calls[count:]]==['theses']
        assert stored(root,item)['filtered_content']=={}


def test_codex_future_choices_preserve_results_and_explicit_regeneration_uses_new_choice(tmp_path):
    from tests.support.codex import CodexService,configure_codex
    class Choices(CodexService):
        async def catalog(self):
            return [dict(model=model,display_name=model,default=model=='synthetic',reasoning_efforts=['low','high'],default_reasoning_effort='low',fast_supported=True) for model in ('synthetic','synthetic-next')]
    root=publication_root(tmp_path)
    model=Choices()
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Useful text')])) )
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,codex_client=model),headers=HEADERS) as client:
        configure_fast_web(client)
        configure_codex(client)
        client.patch('/api/v3/settings/oko/global',json={'processor':'codex','theses':True,'prompt_filters':''})
        feed=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox')).json()['feed_id']
        client.patch('/api/v3/feeds/'+feed,json={'public_ai':True})
        card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['theses']['result']),None))
        item=card['id']
        accepted=stored(root,item)['processing_metadata']
        count=len(model.inputs)
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        assert stored(root,item)['processing_metadata']==accepted
        assert len(model.inputs)==count
        choice=dict(model='synthetic-next',reasoning_effort='high',fast=True)
        assert client.put('/api/v3/processors/codex',json={kind:choice for kind in ('filter','theses')}).status_code==200
        settled(client)
        assert stored(root,item)['processing_metadata']==accepted and len(model.inputs)==count
        assert client.post('/api/v3/materials/'+item+'/regenerate').status_code==200
        wait_for(client,lambda:stored(root,item)['processing_metadata'].get('summary',{}).get('model')=='synthetic-next')
        metadata=stored(root,item)['processing_metadata']['summary']
        assert metadata['fast'] is True and metadata['reasoning_effort']=='high'
        assert len(model.inputs)==count+1


def test_persisted_empty_job_completes_without_a_model_after_restart(tmp_path):
    from tests.support.storage import database
    root=publication_root(tmp_path)
    calls=[]
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'')])))
    options=dict(token='test',api_only=True,rss_transport=source,ai_transport=coordinated_transport(calls))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        setup(client)
        item=settled(client)[0]['id']
        wait_for(client,lambda:stored(root,item)['status']=='ready')
    with database(root) as db:
        db.execute("UPDATE items SET filtered_content = '{}' , summarized_content = '{}' , processing_metadata = '{}' , status = 'pending_filtering' WHERE id = %s",(item,))
        db.execute("UPDATE jobs SET status = 'pending' , generation = generation + 1 WHERE kind = 'filter' AND item = %s",(item,))
        db.execute("DELETE FROM jobs WHERE kind = 'theses' AND item = %s",(item,))
        db.execute('DELETE FROM thesis_inputs WHERE item = %s',(item,))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        row=wait_for(client,lambda:row if (row:=stored(root,item))['status']=='ready' else None)
        assert row['processing_metadata']['filter']['reason']=='no_input'
        assert row['processing_metadata']['summary']['reason']=='no_input'
        assert calls==[]


def test_folder_completion_includes_hidden_publications(tmp_path):
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=coordinated_transport([])), headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp', 'theses': False, 'prompt_filters': 'Убери рекламу.'})
        presence(client, 'reader')
        wait_for(client, lambda: len(c := client.get('/api/v3/cards?show_hidden=true').json()) == 3 and all(x['filter']['attempt']['state'] == 'ready' for x in c))
        assert len(client.get('/api/v3/cards').json()) == 2
        preview = client.post('/api/v3/folders/demo/completion/preview').json()
        assert preview['cards_count'] == preview['publications_count'] == 3
        assert client.post('/api/v3/folders/demo/completion', json={'token': preview['token']}).status_code == 200
        assert client.get('/api/v3/cards?show_hidden=true').json() == []
