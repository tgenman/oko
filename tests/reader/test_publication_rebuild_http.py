"""A rebuilt publication becomes visible only after its complete replacement."""
import asyncio
import threading
import httpx
from tests.support.app import create_app
from tests.support.reader import wait_for
from tests.support.telegram import configure_fast_web,settled
from tests.support.telegram_web import page
from tests.support.v3 import TestClient

HEADERS={'Authorization':'Bearer test'}


def body_text(publication):
    return '\n\n'.join(b['text'] for b in publication['content'].get('blocks',[]) if b['type']=='text' and b['role']=='body')


def test_rebuild_keeps_old_text_and_media_until_new_file_is_ready(tmp_path):
    phase=[0]
    started,release=threading.Event(),threading.Event()
    async def source(request):
        if request.url.host=='cdn.example':
            if phase[0]:
                started.set()
                while not release.is_set():await asyncio.sleep(.01)
            return httpx.Response(200,content=b'new-image' if phase[0] else b'old-image',headers={'content-type':'image/jpeg'})
        text='New text' if phase[0] else 'Old text'
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/image.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,text+photo)]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(source)),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
            client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
            item=settled(client,timeout=15)[0]['id']
            old=client.get('/api/v3/publication/'+item).json()
            old_url=next(m['url'] for m in old['media'] if m['url'])
            assert client.get(old_url).content==b'old-image'
            phase[0]=1
            assert client.post('/api/v3/materials/'+item+'/rebuild').status_code==200
            assert started.wait(10)
            held=client.get('/api/v3/publication/'+item).json()
            assert body_text(held)=='Old text' and client.get(old_url).content==b'old-image'
            release.set()
            current=wait_for(client,lambda:(v if body_text(v:=client.get('/api/v3/publication/'+item).json())=='New text' else None),timeout=15)
            new_url=next(m['url'] for m in current['media'] if m['url'])
            assert client.get(new_url).content==b'new-image'
        finally:
            release.set()


def test_rebuild_failure_keeps_old_copy_and_retries_after_restart(tmp_path):
    import time
    phase=[0]
    offset=[0.0]
    calls=[]
    release=threading.Event()
    async def source(request):
        if request.url.host=='cdn.example':
            calls.append(phase[0])
            if phase[0]==1:return httpx.Response(503)
            while phase[0]==2 and not release.is_set():await asyncio.sleep(.01)
            return httpx.Response(200,content=b'new-image' if phase[0] else b'old-image',headers={'content-type':'image/jpeg'})
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/image.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,('New text' if phase[0] else 'Old text')+photo)]))
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(source),
                 clock=lambda:time.monotonic()+offset[0],wall_clock=lambda:time.time()+offset[0])
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        configure_fast_web(client)
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':'','retry_initial_seconds':1,'retry_max_seconds':2})
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        item=settled(client,timeout=15)[0]['id']
        old=client.get('/api/v3/publication/'+item).json()
        old_url=next(m['url'] for m in old['media'] if m['url'])
        phase[0]=1
        assert client.post('/api/v3/materials/'+item+'/rebuild').status_code==200
        failed=wait_for(client,lambda:(v if (v:=client.get('/api/v3/publication/'+item).json()) and any(j['operation']=='rebuild' and j['status']=='pending' and j['reason_message'] for j in v['jobs']) else None))
        assert body_text(failed)=='Old text' and client.get(old_url).content==b'old-image'
    phase[0]=2
    offset[0]+=5
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        old=client.get('/api/v3/publication/'+item).json()
        assert body_text(old)=='Old text' and client.get(old_url).content==b'old-image'
        release.set()
        current=wait_for(client,lambda:(v if body_text(v:=client.get('/api/v3/publication/'+item).json())=='New text' else None),timeout=15)
        new_url=next(m['url'] for m in current['media'] if m['url'])
        assert client.get(new_url).content==b'new-image'
        assert calls.count(1)>=1 and calls.count(2)>=1


def test_rebuild_shared_article_preserves_other_publication_results(tmp_path):
    from tests.support.cards import read_publications_for_cards
    from tests.support.models import model_transport
    phase=[0]
    started,release=threading.Event(),threading.Event()
    model_started,model_release=threading.Event(),threading.Event()
    hold_model=[False]
    calls=[]
    native=model_transport(calls)
    async def model(request):
        if hold_model[0] and request.url.path=='/v1/chat/completions':
            model_started.set()
            while not model_release.is_set():await asyncio.sleep(.01)
        return native.handler(request)
    async def source(request):
        if request.url.host=='api.telegra.ph':
            article='New shared article' if phase[0] else 'Old shared article'
            return httpx.Response(200,json={'ok':True,'result':{'title':'Shared','content':[{'tag':'p','children':[article]},{'tag':'img','attrs':{'src':'https://cdn.example/shared.jpg'}}]}})
        if request.url.host=='cdn.example':
            if phase[0]:
                started.set()
                while not release.is_set():await asyncio.sleep(.01)
            return httpx.Response(200,content=b'new-shared' if phase[0] else b'old-shared',headers={'content-type':'image/jpeg'})
        posts=[(11,('New Post A' if phase[0] else 'Post A')+' https://telegra.ph/Shared'),(12,'Post B https://telegra.ph/Shared')]
        before=request.url.params.get('before')
        return httpx.Response(200,text=page([(number,text) for number,text in posts if not before or number<int(before)]))
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(source),ai_transport=httpx.MockTransport(model))
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
            client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
            def ready():
                values=read_publications_for_cards(client,'/api/v3/cards')
                return values if len(values)==2 and all(v['summarized_content'].get('points') for v in values) else None
            cards=wait_for(client,ready,timeout=20)
            a=next(c for c in cards if c['source_url'].endswith('/11'))['id']
            b=next(c for c in cards if c['source_url'].endswith('/12'))['id']
            before=client.get('/api/v3/publication/'+b).json()
            assert '_revision' not in before['processing_metadata'] and '_revision_input' not in before['processing_metadata']
            assert 'Old shared article' in str(before['linked_publications'])
            model_count=len([r for r in calls if r.url.path=='/v1/chat/completions'])
            hold_model[0]=True
            assert client.post('/api/v3/materials/'+b+'/regenerate').status_code==200
            assert model_started.wait(10)
            # Migration 115 clears old Job snapshots without removing results.
            from tests.support.storage import database
            with database(tmp_path) as db:
                db.execute("UPDATE jobs SET input_snapshot=NULL WHERE item=%s AND kind IN ('filter','theses')",(b,))
            phase[0]=1
            assert client.post('/api/v3/materials/'+a+'/rebuild').status_code==200
            assert started.wait(10), [g['message'] for g in client.get('/api/v3/diagnostics').json()['groups']]
            assert 'New Post A' not in body_text(client.get('/api/v3/publication/'+a).json())
            assert 'Old shared article' in str(client.get('/api/v3/publication/'+b).json()['linked_publications'])
            release.set()
            after=wait_for(client,lambda:(v if 'New shared article' in str((v:=client.get('/api/v3/publication/'+b).json())['linked_publications']) else None),timeout=20)
            assert 'New Post A' in body_text(client.get('/api/v3/publication/'+a).json())
            hold_model[0]=False
            model_release.set()
            assert after['summarized_content']==before['summarized_content']
            assert after['processing_metadata']==before['processing_metadata']
            assert after['status']==before['status']
            assert len([r for r in calls if r.url.path=='/v1/chat/completions'])<=model_count+2
        finally:
            release.set()
            model_release.set()
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        after=client.get('/api/v3/publication/'+b).json()
        assert 'New shared article' in str(after['linked_publications'])
        assert after['summarized_content']==before['summarized_content']
        assert after['processing_metadata']==before['processing_metadata']


def test_viewed_while_rebuilding_rejects_late_file(tmp_path):
    phase=[0]
    started,release=threading.Event(),threading.Event()
    async def source(request):
        if request.url.host=='cdn.example':
            if phase[0]:
                started.set()
                while not release.is_set():await asyncio.sleep(.01)
            return httpx.Response(200,content=b'new' if phase[0] else b'old',headers={'content-type':'image/jpeg'})
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/image.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,('New text' if phase[0] else 'Old text')+photo)]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(source)),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
            client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
            item=settled(client,timeout=15)[0]['id']
            phase[0]=1
            assert client.post('/api/v3/materials/'+item+'/rebuild').status_code==200
            assert started.wait(10)
            assert client.post('/api/v3/materials/'+item+'/viewed').status_code==200
            release.set()
            wait_for(client,lambda:client.get('/api/v3/publication/'+item).status_code==404,timeout=15)
        finally:
            release.set()


def test_rebuild_reuses_results_only_when_its_input_is_unchanged(tmp_path):
    from tests.support.cards import read_publications_for_cards
    from tests.support.models import model_transport
    phase=[0]
    calls=[]
    async def source(request):
        return httpx.Response(200,text=page([(11,'Changed input' if phase[0] else 'Original input')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(source),
                               ai_transport=model_transport(calls)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        def ready():
            cards=read_publications_for_cards(client,'/api/v3/cards')
            return cards[0] if cards and cards[0]['summarized_content'].get('points') else None
        item=wait_for(client,ready,timeout=20)['id']
        before=client.get('/api/v3/publication/'+item).json()
        count=len([r for r in calls if r.url.path=='/v1/chat/completions'])
        assert client.post('/api/v3/materials/'+item+'/rebuild').status_code==200
        def rebuilt():
            value=client.get('/api/v3/publication/'+item).json()
            return value if any(j['operation']=='rebuild' and j['status']=='done' for j in value['jobs']) else None
        same=wait_for(client,rebuilt,timeout=20)
        assert same['processing_metadata']==before['processing_metadata']
        assert same['summarized_content']==before['summarized_content']
        assert len([r for r in calls if r.url.path=='/v1/chat/completions'])==count
        phase[0]=1
        assert client.post('/api/v3/materials/'+item+'/rebuild').status_code==200
        def changed():
            value=client.get('/api/v3/publication/'+item).json()
            return value if body_text(value)=='Changed input' and value['summarized_content'].get('points') and len([r for r in calls if r.url.path=='/v1/chat/completions'])>count else None
        updated=wait_for(client,changed,timeout=20)
        assert updated['processing_metadata']!=before['processing_metadata']


def test_rebuild_preserves_source_wait_when_requested_again(tmp_path):
    from tests.support.storage import database
    phase=[0]
    calls=[]
    async def source(request):
        calls.append(phase[0])
        if phase[0]:return httpx.Response(429,headers={'retry-after':'60'})
        return httpx.Response(200,text=page([(11,'Initial text')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(source)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':'','retry_initial_seconds':1,'retry_max_seconds':1})
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        item=settled(client,timeout=15)[0]['id']
        phase[0]=1
        assert client.post('/api/v3/materials/'+item+'/rebuild').status_code==200
        wait_for(client,lambda:any(j['operation']=='rebuild' and j['status']=='pending' and j['reason_message'] for j in client.get('/api/v3/publication/'+item).json()['jobs']))
        with database(tmp_path) as db:
            first=db.execute("SELECT retry_at,wait_scope FROM jobs WHERE kind='rebuild' AND item=%s",(item,)).fetchone()
            assert first['retry_at'] is not None and first['wait_scope']
        assert client.post('/api/v3/materials/'+item+'/rebuild').status_code==200
        with database(tmp_path) as db:
            second=db.execute("SELECT retry_at,wait_scope FROM jobs WHERE kind='rebuild' AND item=%s",(item,)).fetchone()
            assert tuple(second)==tuple(first)
        assert body_text(client.get('/api/v3/publication/'+item).json())=='Initial text'
        assert calls.count(1)==1


def test_replaced_file_without_prior_job_is_cleaned_after_restart(tmp_path):
    from tests.support.storage import database
    phase=[0]
    async def source(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=b'new' if phase[0] else b'old',headers={'content-type':'image/jpeg'})
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/image.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,('New text' if phase[0] else 'Old text')+photo)]))
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(source))
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        configure_fast_web(client)
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        item=settled(client,timeout=15)[0]['id']
        with database(tmp_path) as db:
            old=db.execute('SELECT id,storage_key FROM media_files WHERE publication_id=%s',(item,)).fetchone()
            db.execute("DELETE FROM jobs WHERE kind='download_media' AND item=%s",(old['id'],))
        old_path=tmp_path/old['storage_key']
        assert old_path.read_bytes()==b'old'
        # Model an interruption between the DB commit and eager file pruning.
        def disable_prune():
            client.app.state.host.require_store().publications.prune=lambda _item:None
        client.portal.call(disable_prune)
        phase[0]=1
        assert client.post('/api/v3/materials/'+item+'/rebuild').status_code==200
        wait_for(client,lambda:body_text(client.get('/api/v3/publication/'+item).json())=='New text',timeout=15)
        assert old_path.exists()
        with database(tmp_path) as db:
            assert db.execute("SELECT 1 FROM jobs WHERE kind='download_media' AND item=%s AND payload->>'retired_media_key'=%s",(old['id'],old['storage_key'])).fetchone()
        abandoned=tmp_path/'staging'/item/'rebuild-interrupted'
        abandoned.mkdir(parents=True)
        (abandoned/'partial').write_bytes(b'partial')
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        from tests.support.presence import presence
        presence(client,'cleanup-restart')
        def cleaned():
            client.get('/api/v3/status')
            return not old_path.exists() and not abandoned.exists()
        wait_for(client,cleaned,timeout=15)
        with database(tmp_path) as db:
            assert db.execute("SELECT 1 FROM jobs WHERE kind='download_media' AND item=%s",(old['id'],)).fetchone() is None
