"""Latest-publication processing through real HTTP and synthetic source/model."""
import json
import sqlite3

import httpx

from tests.reader.test_independent_media import migrated_root
from tests.reviews.test_processing_coordination import coordinated_transport
from tests.support.app import create_app
from tests.support.presence import presence
from tests.support.reader import HEADERS, wait_for
from tests.support.telegram import configure_fast_web, settled
from tests.support.telegram_web import page
from tests.support.v3 import TestClient


def stored(root,item):
    with sqlite3.connect(root/'state.sqlite') as db:
        db.row_factory=sqlite3.Row
        row=dict(db.execute('SELECT * FROM items WHERE id=?',(item,)).fetchone())
    for key in ('content','filtered_content','summarized_content','processing_metadata'):
        row[key]=json.loads(row[key])
    return row


def setup(client):
    configure_fast_web(client)
    client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
    client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':'Убери рекламу.'})
    presence(client,'reader')
    client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))


def test_results_have_atomic_metadata_and_media_change_preserves_processing(tmp_path):
    root=migrated_root(tmp_path)
    calls=[]
    photo=['first']
    def source(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=request.url.path.encode(),headers={'content-type':'image/jpeg'})
        media='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/'+photo[0]+'.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,'Открылась выставка. Реклама: купите курс.'+media)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(source),ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        setup(client)
        card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['theses']['result']),None))
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
        photo[0]='second'
        client.post('/api/v3/materials/'+item+'/retry')
        settled(client)
        updated=stored(root,item)
        assert updated['processing_metadata']==accepted
        ids=json.loads(updated['media_ids'])
        assert all(b['media_id'] in ids for b in updated['filtered_content']['blocks'] if b['type']=='media')
        assert len(calls)==count


def test_hidden_has_empty_blocks_original_and_no_restore_or_summary(tmp_path):
    root=migrated_root(tmp_path)
    calls=[]
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Реклама: купите курс.')])) )
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        setup(client)
        card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards?show_hidden=true').json() if (c['filter']['result'] or {}).get('hidden')),None))
        row=stored(root,card['id'])
        assert row['status']=='filter_out' and row['filtered_content']['blocks']==[]
        assert card['filter']['result']['can_restore'] is False
        assert row['filtered_content']['reason']
        assert row['content']['blocks'][0]['text']=='Реклама: купите курс.'
        assert not any(c['operation']=='theses' for c in calls)
        assert client.post('/api/v3/materials/'+card['id']+'/filter/restore').status_code==400


def test_confirmed_empty_input_completes_without_model(tmp_path):
    root=migrated_root(tmp_path)
    calls=[]
    photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/image.jpg\')"></a>'
    def source(request):
        return httpx.Response(200,content=b'photo',headers={'content-type':'image/jpeg'}) if request.url.host=='cdn.example' else httpx.Response(200,text=page([(11,photo)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(source),ai_transport=coordinated_transport(calls)),headers=HEADERS) as client:
        setup(client)
        item=settled(client)[0]['id']
        row=wait_for(client,lambda:r if (r:=stored(root,item))['status']=='ready' else None)
        assert row['summarized_content']=={'points':[],'extra':{}}
        assert row['filtered_content']['blocks']==[] and row['filtered_content']['removed']==[]
        assert row['processing_metadata']['filter']['reason']=='no_input'
        assert row['processing_metadata']['summary']['reason']=='no_input'
        assert calls==[]


def test_unavailable_input_waits_without_model_and_processes_when_retrieved(tmp_path):
    from tests.support.telegram_web import mark_post_unavailable
    root=migrated_root(tmp_path)
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
    root=migrated_root(tmp_path)
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
