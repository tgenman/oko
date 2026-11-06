"""Latest-publication processing through real HTTP and synthetic source/model."""
from tests.support.publications import body_text,summary,filter_result,filter_state,reasons
from tests.support.cards import read_publications_for_cards
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
        row=dict(db.execute('SELECT * FROM publications WHERE id = %s',(item,)).fetchone())
    for key in ('filtered_content','summarized_content','processing_metadata'):
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
        cards=wait_for(client,lambda:rows if len(rows:=read_publications_for_cards(client, '/api/v3/cards'))==2 and all(summary(c) for c in rows) else None)
        assert all(all('Реклама:' not in block['text'] for block in filter_result(c)['blocks'] if block['type']=='text') for c in cards)
        assert {'filter','theses'}<={c['operation'] for c in calls}
        card=next(c for c in cards if c['source_url'].endswith('/11'))
        item=card['id']
        row=stored(root,item)
        assert row['status']=='ready'
        for operation in ('filter','summary'):
            metadata=row['processing_metadata'][operation]
            assert {'input_hash','prompt','processor','model','parameters','completed_at'}<=metadata.keys()
            assert 'signature' not in metadata and 'settings_hash' not in metadata
            assert metadata['processor']=='llamacpp' and metadata['model']=='local:latest'
        assert row['processing_metadata']['summary']['parameters']=={'temperature':0,'max_tokens':4096,'reasoning_effort':'none'}
        assert row['filtered_content']['removed']==[{'publication_id':item,'block_index':0,'text':'Реклама: купите курс.'}]
        assert all(b['source_publication_id']==item for b in row['filtered_content']['blocks'])
        accepted=row['processing_metadata']
        count=len(calls)
        original=client.get('/api/v3/publication/'+item).json()
        assert client.get('/api/v3/settings/feed/'+card['feed_id']).json()['effective']['originals']['value'] is False
        assert body_text(original) and summary(original)
        client.patch('/api/v3/settings/feed/'+card['feed_id'],json={'originals':True})
        assert client.get('/api/v3/settings/feed/'+card['feed_id']).json()['effective']['originals']['value'] is True
        assert client.get('/api/v3/settings/feed/'+card['feed_id']).json()['effective']['theses']['value'] is True
        assert summary(client.get('/api/v3/publication/'+item).json())==summary(original)
        assert len(calls)==count
        photo[0]='second'
        client.post('/api/v3/materials/'+item+'/retry')
        settled(client,count=2)
        updated=stored(root,item)
        assert updated['processing_metadata']==accepted
        ids=json.loads(updated['media_ids'])
        assert all(b['media_id'] in ids for b in updated['filtered_content']['blocks'] if b['type']=='media')
        assert len(calls)==count

        wait_for(client,lambda:client.get('/api/v3/status/details').json()['work']==[{'phase':'idle'}])
        saved=read_publications_for_cards(client, '/api/v3/cards')
        time.sleep(1.2)  # More than two periodic reconciliation passes.
        assert len(calls)==count
        assert [summary(c) for c in read_publications_for_cards(client, '/api/v3/cards')]==[summary(c) for c in saved]
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        wait_for(client,lambda:client.get('/api/v3/status/details').json()['work']==[{'phase':'idle'}])
        assert [summary(c) for c in read_publications_for_cards(client, '/api/v3/cards')]==[summary(c) for c in saved]
        assert stored(root,item)['processing_metadata']==accepted
        assert len(calls)==count


def test_hidden_has_empty_blocks_original_and_no_restore_or_summary(tmp_path):
    root=publication_root(tmp_path)
    calls=[]
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Реклама: купите курс.')])) )
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        setup(client)
        card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client, '/api/v3/cards?show_hidden=true') if (filter_result(c) or {}).get('hidden')),None))
        row=stored(root,card['id'])
        assert row['status']=='filter_out' and row['filtered_content']['blocks']==[]
        assert 'can_restore' not in filter_result(card)
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
        item=settled(client,timeout=15)[0]['id']
        wait_for(client,lambda:summary(client.get('/api/v3/publication/'+item).json()))
        row=stored(root,item)
        assert row['status']=='ready'
        assert row['summarized_content']=={'points':[]}
        assert row['filtered_content']['blocks']==[] and row['filtered_content']['removed']==[]
        assert row['processing_metadata']['filter']['reason']=='no_input'
        assert row['processing_metadata']['summary']['reason']=='no_input'
        for stage in ('filter','summary'):
            metadata=row['processing_metadata'][stage]
            assert metadata['prompt'] is None and metadata['processor'] is None and metadata['model'] is None
            assert metadata['parameters']=={} and metadata['completed_at']
        assert calls==[]

        accepted=row['processing_metadata']
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(source),ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        client.post('/api/v3/collect')
        settled(client,timeout=15)
        assert stored(root,item)['processing_metadata']==accepted
        assert calls==[]
        text[0]='Text appeared'
        client.post('/api/v3/materials/'+item+'/retry')
        assert summary(client.get('/api/v3/publication/'+item).json())['points']==[]
        assert stored(root,item)['processing_metadata']==accepted
        assert calls==[]


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
        item=wait_for(client,lambda:(c[0]['id'] if (c:=read_publications_for_cards(client, '/api/v3/cards')) else None))
        wait_for(client,lambda:r['status']=='pending_collection' and r['content']!={} if (r:=stored(root,item)) else False)
        row=stored(root,item)
        assert row['filtered_content']=={} and row['summarized_content']=={}
        assert calls==[]
        available[0]=True
        client.post('/api/v3/materials/'+item+'/retry')
        wait_for(client,lambda:summary(client.get('/api/v3/publication/'+item).json()))
        assert all(c['sources'][0]['text']=='Retrieved text' for c in calls)


def test_running_filter_finishes_with_its_settings_and_summary_uses_new_settings(tmp_path):
    import asyncio
    import threading
    root=publication_root(tmp_path)
    calls=[]
    started,release=threading.Event(),threading.Event()
    native=coordinated_transport(calls)
    async def model(request):
        if request.url.path=='/v1/chat/completions':
            task=json.loads(json.loads(request.content)['messages'][1]['content'])
            if task['operation']=='filter':
                calls.append(task)
                started.set()
                while not release.is_set():
                    await asyncio.sleep(.01)
                return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(dict(hidden=False,reason='Old rule',removed=[]))}}]})
        return native.handler(request)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Useful text')])) )
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            setup(client)
            assert started.wait(10)
            client.patch('/api/v3/settings/oko/global',json={'prompt_filters':'Keep the text.','prompt_theses':'New summary rule.'})
            release.set()
            card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client,'/api/v3/cards') if summary(c)),None))
            assert filter_result(card)['reason']=='Old rule'
            assert [c['prompt'] for c in calls if c['operation']=='filter']==['Убери рекламу.']
            assert [c['prompt'] for c in calls if c['operation']=='theses']==['New summary rule.']
            accepted=stored(root,card['id'])['processing_metadata']
            assert accepted['filter']['prompt']=='Убери рекламу.'
            assert accepted['summary']['prompt']=='New summary rule.'
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
            card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client, '/api/v3/cards') if c['content'].get('blocks') is not None),None))
            item=card['id']
            client.patch('/api/v3/feeds/'+feed,json={'public_ai':True})
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':False})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':'Filter'})
            assert started.wait(3)
            client.patch('/api/v3/feeds/'+feed,json={'public_ai':False})
            release.set()
            wait_for(client,lambda:any(w.get('reason')=='public_ai_not_allowed' for w in client.get('/api/v3/status/details').json()['work']))
            assert calls==[]
            assert client.get('/api/v3/status/details').json()['problems']['current']==[]
            assert all(summary(c) is None for c in read_publications_for_cards(client, '/api/v3/cards'))
            item=read_publications_for_cards(client, '/api/v3/cards')[0]['id']
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
            assert started.wait(20),client.get('/api/v3/status/details').json()
            card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client, '/api/v3/cards') if summary(c)),None))
            assert stored(root,card['id'])['status']=='ready'
            assert any(media['status']=='pending_download' and media['url'] is None for media in card['media'])
            assert [media for media in card['media'] if media['url'] is not None]==[]
            assert {c['operation'] for c in calls}=={'filter','theses'}
            metadata=stored(root,card['id'])['processing_metadata']
            release.set()
            wait_for(client,lambda:[media for media in client.get('/api/v3/publication/'+card['id']).json()['media'] if media['url'] is not None])
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
        card=wait_for(client,lambda:next(iter(read_publications_for_cards(client, '/api/v3/cards')),None))
        wait_for(client,lambda:(value if reasons(value:=client.get('/api/v3/publication/'+card['id']).json()) else None))
        assert calls==[]
        assert summary(client.get('/api/v3/publication/'+card['id']).json()) is None
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
        card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client, '/api/v3/cards') if reasons(c,stage)),None))
        row=stored(root,card['id'])
        assert row['filtered_content' if stage=='filter' else 'summarized_content']=={}
        assert next(b for b in client.get('/api/v3/publication/'+card['id']).json()['content']['blocks'] if b['type']=='quote')['text']=='Quoted context'
        broken[0]=False
        assert client.post('/api/v3/materials/'+card['id']+'/'+stage+'/retry').status_code==200
        wait_for(client,lambda:summary(client.get('/api/v3/publication/'+card['id']).json()))
        assert [b['text'] for b in stored(root,card['id'])['filtered_content']['blocks'] if b['type']=='quote']==['Quoted context']


def test_local_retry_records_the_model_actually_used_and_ready_result_survives_choice_change(tmp_path):
    from tests.support.models import model_transport
    root=publication_root(tmp_path)
    offset=[0.0]
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
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model),
                               clock=lambda:time.monotonic()+offset[0],wall_clock=lambda:time.time()+offset[0]),headers=HEADERS) as client:
        configure_fast_web(client)
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':selected[0],'local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':'','retry_initial_seconds':60,'retry_max_seconds':60})
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client, '/api/v3/cards') if reasons(c,'theses')),None))
        selected[0]='local:second'
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':selected[0],'local_execution':True})
        broken[0]=False
        offset[0]+=61
        wait_for(client,lambda:summary(client.get('/api/v3/publication/'+card['id']).json()))
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
        card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client, '/api/v3/cards') if summary(c)),None))
        item=card['id']
        accepted=stored(root,item)
        assert client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:new','local_execution':True}).status_code==200
        broken[0]=True
        assert client.post('/api/v3/materials/'+item+'/regenerate').status_code==200
        wait_for(client,lambda:reasons(client.get('/api/v3/publication/'+item).json(),'theses'))
        assert stored(root,item)['processing_metadata']==accepted['processing_metadata']
        assert stored(root,item)['summarized_content']==accepted['summarized_content']
        assert not list((root/'content'/item).glob('*/accepted-theses-*.md'))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        assert summary(client.get('/api/v3/publication/'+item).json())==summary(card)
        assert stored(root,item)['summarized_content']==accepted['summarized_content']
        text[0]='Changed text'
        client.post('/api/v3/materials/'+item+'/retry')
        assert body_text(client.get('/api/v3/publication/'+item).json())=='Original text'
        assert stored(root,item)['summarized_content']==accepted['summarized_content']
        assert summary(client.get('/api/v3/publication/'+item).json())==summary(card)


def test_disabling_stages_preserves_results_and_reenable_reuses_them(tmp_path):
    root=publication_root(tmp_path)
    calls=[]
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Useful text')])) )
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        setup(client)
        card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client, '/api/v3/cards') if summary(c)),None))
        item,feed=card['id'],card['feed_id']
        accepted=stored(root,item)
        count=len(calls)
        response=client.patch('/api/v3/settings/feed/'+feed,json={'filter_enabled':False,'theses':False})
        assert response.status_code==200, response.text
        disabled=client.get('/api/v3/publication/'+item).json()
        assert filter_result(disabled) is not None
        assert summary(disabled)==summary(card)
        row=stored(root,item)
        assert row['filtered_content']==accepted['filtered_content']
        assert row['summarized_content']==accepted['summarized_content']
        response=client.patch('/api/v3/settings/feed/'+feed,json={'filter_enabled':True,'theses':True})
        assert response.status_code==200, response.text
        assert summary(client.get('/api/v3/publication/'+item).json())==summary(card)
        time.sleep(.2)
        assert len(calls)==count


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
        card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client, '/api/v3/cards') if summary(c)),None))
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
        assert metadata['parameters']=={'fast':True,'reasoning_effort':'high'}
        assert 'fast' not in metadata and 'reasoning_effort' not in metadata
        assert len(model.inputs)==count+1


def test_persisted_empty_job_completes_without_a_model_after_restart(tmp_path):
    from tests.support.storage import database
    root=publication_root(tmp_path)
    calls=[]
    photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/image.jpg\')"></a>'
    source=httpx.MockTransport(lambda request:httpx.Response(200,content=b'photo',headers={'content-type':'image/jpeg'}) if request.url.host=='cdn.example' else httpx.Response(200,text=page([(11,photo)])))
    options=dict(token='test',api_only=True,rss_transport=source,ai_transport=coordinated_transport(calls))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        setup(client)
        item=settled(client)[0]['id']
        wait_for(client,lambda:stored(root,item)['status']=='ready')
    with database(root) as db:
        db.execute("UPDATE publications SET filtered_content = '{}' , summarized_content = '{}' , processing_metadata = '{}' , status = 'pending_filtering' WHERE id = %s",(item,))
        db.execute("UPDATE jobs SET status = 'pending' , generation = generation + 1 WHERE kind = 'filter' AND item = %s",(item,))
        db.execute("DELETE FROM jobs WHERE kind = 'theses' AND item = %s",(item,))
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
        wait_for(client, lambda: len(c := read_publications_for_cards(client, '/api/v3/cards?show_hidden=true')) == 3 and all(filter_state(x) == 'ready' for x in c))
        assert len(read_publications_for_cards(client, '/api/v3/cards')) == 2
        preview = client.post('/api/v3/folders/fixture/completion/preview').json()
        assert preview['cards_count'] == preview['publications_count'] == 3
        assert client.post('/api/v3/folders/fixture/completion', json={'token': preview['token']}).status_code == 200
        assert read_publications_for_cards(client, '/api/v3/cards?show_hidden=true') == []


def test_clearing_filter_prompt_keeps_accepted_filter_and_theses(tmp_path):
    root=publication_root(tmp_path)
    calls=[]
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Открылась выставка. Реклама: купите курс.')])))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        setup(client)
        card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client,'/api/v3/cards') if summary(c)),None))
        item=card['id']
        before=client.get('/api/v3/publication/'+item).json()
        count=len(calls)
        assert client.patch('/api/v3/settings/oko/global',json={'prompt_filters':''}).status_code==200
        after=client.get('/api/v3/publication/'+item).json()
        assert filter_result(after)==filter_result(before)
        assert summary(after)==summary(before)
        assert len(calls)==count


def test_processing_metadata_records_actual_prompts_without_competing_signatures(tmp_path):
    root=publication_root(tmp_path)
    calls=[]
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Useful text')])))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        setup(client)
        card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client,'/api/v3/cards') if summary(c)),None))
        metadata=stored(root,card['id'])['processing_metadata']
        for stage,operation in [('filter','filter'),('summary','theses')]:
            accepted=metadata[stage]
            request=next(call for call in calls if call['operation']==operation)
            assert accepted['prompt']==request['prompt']
            assert accepted['processor']=='llamacpp' and accepted['model']=='local:latest'
            assert accepted['parameters'] and accepted['completed_at'] and accepted['input_hash']
            import hashlib
            expected=hashlib.sha256(json.dumps(request['sources'],sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
            assert accepted['input_hash']==expected
            with database(root) as db:
                snapshot=db.execute("SELECT input_snapshot FROM jobs WHERE item=%s AND kind=%s",(card['id'],operation)).fetchone()[0]
            assert snapshot['input_hash']==expected and 'signature' not in snapshot
            assert 'signature' not in accepted and 'settings_hash' not in accepted
        assert '_pass' not in metadata


def test_revoking_public_ai_does_not_cancel_running_local_filter(tmp_path):
    import asyncio
    import threading
    root=publication_root(tmp_path)
    entered,release=threading.Event(),threading.Event()
    calls=[]
    native=coordinated_transport(calls)
    async def model(request):
        if request.url.path=='/v1/chat/completions':
            entered.set()
            while not release.is_set():await asyncio.sleep(.01)
        return native.handler(request)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Local text')])))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
            feed=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).json()['feed_id']
            card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client,'/api/v3/cards') if c['content'].get('blocks') is not None),None))
            client.patch('/api/v3/feeds/'+feed,json={'public_ai':True})
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','prompt_filters':'Filter','theses':False})
            assert entered.wait(10), {'status':client.get('/api/v3/status/details').json(),'diagnostics':client.get('/api/v3/diagnostics').json()}
            client.patch('/api/v3/feeds/'+feed,json={'public_ai':False})
            release.set()
            result=wait_for(client,lambda:(v if filter_result(v:=client.get('/api/v3/publication/'+card['id']).json()) else None))
            assert filter_result(result) is not None
            assert len([call for call in calls if call['operation']=='filter'])==1
        finally:
            release.set()


def test_manual_filter_rebuild_keeps_theses_until_explicit_summary_rebuild(tmp_path):
    root=publication_root(tmp_path)
    calls=[]
    native=model_transport([])
    def model(request):
        if request.url.path!='/v1/chat/completions':return native.handler(request)
        task=json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        if task['operation']=='filter':
            result={'hidden':False,'reason':'Rule','removed':['Ad text.'] if task['prompt']=='Remove ads' else []}
        else:
            first=task['sources'][0]
            result={'points':[{'text':first['text'],'sources':[{'id':first['id'],'url':first['url']}]}]}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Useful text. Ad text.')])))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':'Remove ads'})
        presence(client,'reader')
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client,'/api/v3/cards') if summary(c)),None))
        item=card['id']
        old=summary(client.get('/api/v3/publication/'+item).json())
        assert [point['text'] for point in old['points']]==['Useful text. ']
        client.patch('/api/v3/settings/oko/global',json={'prompt_filters':'Keep all'})
        assert client.post('/api/v3/materials/'+item+'/filter/retry').status_code==200
        wait_for(client,lambda:stored(root,item)['processing_metadata'].get('filter',{}).get('prompt')=='Keep all')
        assert summary(client.get('/api/v3/publication/'+item).json())==old
        assert len([c for c in calls if c['operation']=='theses'])==1
        assert client.post('/api/v3/materials/'+item+'/regenerate').status_code==200
        updated=wait_for(client,lambda:(r if (r:=summary(client.get('/api/v3/publication/'+item).json())) and r!=old else None))
        assert [point['text'] for point in updated['points']]==['Useful text. Ad text.']
        assert len([c for c in calls if c['operation']=='theses'])==2


def test_running_filter_rebuild_exposes_attempt_and_previous_result_together(tmp_path):
    import asyncio
    import threading
    root=publication_root(tmp_path)
    entered,release=threading.Event(),threading.Event()
    hold=[False]
    calls=[]
    native=coordinated_transport(calls)
    async def model(request):
        if request.url.path=='/v1/chat/completions' and hold[0]:
            entered.set()
            while not release.is_set():await asyncio.sleep(.01)
        return native.handler(request)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Useful text')])))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            setup(client)
            card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client,'/api/v3/cards') if summary(c)),None))
            item=card['id']
            previous=filter_result(client.get('/api/v3/publication/'+item).json())
            hold[0]=True
            assert client.post('/api/v3/materials/'+item+'/filter/retry').status_code==200
            assert entered.wait(10)
            current=client.get('/api/v3/publication/'+item).json()
            assert filter_state(current)=='processing'
            assert filter_result(current)==previous
            assert any(j['operation']=='filter' and j['status']=='running' for j in current['jobs'])
        finally:
            release.set()


def test_failed_filter_rebuild_keeps_previous_result_and_shows_error(tmp_path):
    root=publication_root(tmp_path)
    fail=[False]
    native=coordinated_transport([])
    def model(request):
        if fail[0] and request.url.path=='/v1/chat/completions':return httpx.Response(503)
        return native.handler(request)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Useful text')])))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        setup(client)
        card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client,'/api/v3/cards') if summary(c)),None))
        item=card['id']
        previous=filter_result(client.get('/api/v3/publication/'+item).json())
        fail[0]=True
        assert client.post('/api/v3/materials/'+item+'/filter/retry').status_code==200
        result=wait_for(client,lambda:(v if filter_state(v:=client.get('/api/v3/publication/'+item).json())=='error' else None))
        assert filter_result(result)==previous and reasons(result,'filter')


def test_retrying_filter_does_not_cancel_running_summary(tmp_path):
    import asyncio
    import threading
    root=publication_root(tmp_path)
    entered,release=threading.Event(),threading.Event()
    calls=[]
    native=coordinated_transport(calls)
    async def model(request):
        if request.url.path=='/v1/chat/completions':
            task=json.loads(json.loads(request.content)['messages'][1]['content'])
            if task['operation']=='theses':
                entered.set()
                while not release.is_set():await asyncio.sleep(.01)
        return native.handler(request)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Useful text')])))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            setup(client)
            assert entered.wait(10)
            item=read_publications_for_cards(client,'/api/v3/cards')[0]['id']
            assert client.post('/api/v3/materials/'+item+'/filter/retry').status_code==200
            current=client.get('/api/v3/publication/'+item).json()
            assert any(j['operation']=='theses' and j['status']=='running' for j in current['jobs'])
            release.set()
            result=wait_for(client,lambda:(v if summary(v:=client.get('/api/v3/publication/'+item).json()) else None))
            assert [point['text'] for point in summary(result)['points']]==['Useful text']
            assert len([c for c in calls if c['operation']=='theses'])==1
        finally:
            release.set()


def test_public_ai_revoked_during_unverified_loopback_check_blocks_transfer(tmp_path):
    import asyncio
    import threading
    root=publication_root(tmp_path)
    entered,release=threading.Event(),threading.Event()
    transfers=[]
    async def model(request):
        if request.url.path=='/v1/models':
            return httpx.Response(200,json={'data':[{'id':'local:latest','owned_by':'proxy','meta':{'size':100}}]})
        if request.url.path=='/props':
            entered.set()
            while not release.is_set():await asyncio.sleep(.01)
            return httpx.Response(200,json={'model_path':'/remote','build_info':'proxy'})
        transfers.append(request.url.path)
        return httpx.Response(500)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Private source')])))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
            feed=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).json()['feed_id']
            wait_for(client,lambda:read_publications_for_cards(client,'/api/v3/cards'))
            client.patch('/api/v3/feeds/'+feed,json={'public_ai':True})
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://127.0.0.1:9931','model':'local:latest','local_execution':True})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','prompt_filters':'Filter','theses':False})
            assert entered.wait(10)
            client.patch('/api/v3/feeds/'+feed,json={'public_ai':False})
            release.set()
            wait_for(client,lambda:any(w.get('reason')=='public_ai_not_allowed' for w in client.get('/api/v3/status/details').json()['work']))
            assert transfers==[]
        finally:
            release.set()


def test_revoking_public_ai_interrupts_running_remote_filter_and_rejects_late_result(tmp_path):
    import asyncio
    import threading
    root=publication_root(tmp_path)
    entered,cancelled,release=threading.Event(),threading.Event(),threading.Event()
    calls=[]
    native=coordinated_transport(calls)
    async def model(request):
        if request.url.path=='/v1/chat/completions':
            entered.set()
            while not release.is_set():
                try:await asyncio.sleep(.01)
                except asyncio.CancelledError:cancelled.set()
        return native.handler(request)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Private text')])))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
            feed=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).json()['feed_id']
            card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client,'/api/v3/cards') if c['content'].get('blocks') is not None),None))
            client.patch('/api/v3/feeds/'+feed,json={'public_ai':True})
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'https://remote.test','model':'local:latest','local_execution':False})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','prompt_filters':'Filter','theses':False})
            assert entered.wait(10)
            client.patch('/api/v3/feeds/'+feed,json={'public_ai':False})
            assert cancelled.wait(10)
            release.set()
            wait_for(client,lambda:any(j['operation']=='filter' and j['status']!='running' for j in client.get('/api/v3/publication/'+card['id']).json()['jobs']))
            assert filter_result(client.get('/api/v3/publication/'+card['id']).json()) is None
        finally:
            release.set()


def test_late_model_reply_cannot_change_recreated_publication(tmp_path):
    import asyncio
    import threading
    entered,release,finished=threading.Event(),threading.Event(),threading.Event()
    text=['Old text']
    calls=[]

    async def model(request):
        if request.url.path!='/v1/chat/completions':
            return model_transport([]).handler(request)
        task=json.loads(json.loads(request.content)['messages'][1]['content'])
        source=task['sources'][0]
        calls.append(source['text'])
        if len(calls)==1:
            entered.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(.01)
                except asyncio.CancelledError:
                    pass
            finished.set()
        result={'points':[{'text':source['text'],'sources':[{'id':source['id'],'url':source['url']}]}]}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})

    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,text[0])])))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=source,
                               ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
            feed=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).json()['feed_id']
            old=wait_for(client,lambda:next((c for c in read_publications_for_cards(client,'/api/v3/cards') if c['content'].get('blocks') is not None),None))['id']
            assert entered.wait(10)
            assert client.post('/api/v3/materials/'+old+'/viewed').status_code==200
            wait_for(client,lambda:client.get('/api/v3/publication/'+old).status_code==404)
            text[0]='New text'
            assert client.post('/api/v3/feeds/'+feed+'/history',json={'mode':'count','count':'1'}).status_code==200
            fresh=wait_for(client,lambda:next((c for c in read_publications_for_cards(client,'/api/v3/cards') if c['id']!=old and 'New text' in body_text(c)),None))['id']
            release.set()
            assert finished.wait(10)
            current=wait_for(client,lambda:(value if (value:=client.get('/api/v3/publication/'+fresh).json()) and summary(value) else None),timeout=20)
            assert summary(current)['points'][0]['text']=='New text'
            assert calls==['Old text','New text']
            assert client.get('/api/v3/publication/'+old).status_code==404
        finally:
            release.set()


def test_public_permission_granted_during_check_then_revoked_during_transfer(tmp_path):
    import asyncio
    import threading
    root=publication_root(tmp_path)
    checking,finish_check,sending,cancelled,release=[threading.Event() for _ in range(5)]
    native=coordinated_transport([])
    async def model(request):
        if request.url.path=='/v1/models':
            return httpx.Response(200,json={'data':[{'id':'local:latest','owned_by':'proxy','meta':{'size':100}}]})
        if request.url.path=='/props':
            checking.set()
            while not finish_check.is_set():await asyncio.sleep(.01)
            return httpx.Response(200,json={'model_path':'/remote','build_info':'proxy'})
        if request.url.path=='/v1/chat/completions':
            sending.set()
            while not release.is_set():
                try:await asyncio.sleep(.01)
                except asyncio.CancelledError:cancelled.set()
        return native.handler(request)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Private input')])))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
            feed=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).json()['feed_id']
            card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client,'/api/v3/cards') if c['content'].get('blocks') is not None),None))
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://127.0.0.1:9931','model':'local:latest','local_execution':True})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','prompt_filters':'Filter','theses':False})
            assert checking.wait(10)
            client.patch('/api/v3/feeds/'+feed,json={'public_ai':True})
            finish_check.set()
            assert sending.wait(10)
            client.patch('/api/v3/feeds/'+feed,json={'public_ai':False})
            assert cancelled.wait(10)
            release.set()
            wait_for(client,lambda:any(j['operation']=='filter' and j['status']!='running' for j in client.get('/api/v3/publication/'+card['id']).json()['jobs']))
            assert filter_result(client.get('/api/v3/publication/'+card['id']).json()) is None
        finally:
            finish_check.set()
            release.set()
