"""YouTube media and processing through the target reader HTTP pipeline."""
from tests.support.cards import read_all_cards
import asyncio
import json
import threading

import httpx
import pytest

from tests.support.publication_pipeline import publication_root
from tests.support.app import create_app
from tests.support.reader import HEADERS,wait_for
from tests.support.presence import presence
from tests.support.models import model_transport
from tests.support.youtube import CHANNEL,VIDEO,PNG,feed_xml,subscribe,History
from tests.support.v3 import TestClient


@pytest.mark.parametrize('high_importance',[False,True])
def test_youtube_captions_and_theses_are_ready_while_cover_is_held(tmp_path,high_importance):
    root=publication_root(tmp_path)
    started,release=threading.Event(),threading.Event()
    captions_calls=[]
    class Captions:
        async def fetch(self,video):
            captions_calls.append(video)
            return 'Original raw captions.'
    async def response(request):
        if request.url.path=='/feeds/videos.xml':
            return httpx.Response(200,text=feed_xml())
        assert request.url.host=='i.ytimg.com'
        started.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    models=[]
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,
            rss_transport=httpx.MockTransport(response),youtube_captions=Captions(),
            youtube_history=History([VIDEO]),ai_transport=model_transport(models)),headers=HEADERS) as client:
        try:
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
            feed=subscribe(client)
            if high_importance:
                assert client.patch('/api/v3/feeds/'+feed,json={'importance':'high'}).status_code==200
            presence(client,'reader')
            assert started.wait(3)
            card=wait_for(client,lambda:next((card for card in read_all_cards(client, '/api/v3/cards') if client.get('/api/v3/publication/'+card['id']).json()['theses']['result']),None))
            material=client.get('/api/v3/publication/'+card['id']).json()
            assert material['text']=='Original raw captions.'
            assert captions_calls==[VIDEO] and [media for media in material['media'] if media['role'] == 'attachment']
            task=json.loads(json.loads(next(request for request in models if request.url.path=='/v1/chat/completions').content)['messages'][1]['content'])
            assert task['sources'][0]['text']=='Original raw captions.'
            release.set()
            wait_for(client,lambda:any(a['kind']=='image' for a in [media for media in client.get('/api/v3/publication/'+card['id']).json()['media'] if media['role'] == 'attachment']))
        finally:
            release.set()


def test_confirmed_missing_captions_complete_without_model_but_error_does_not(tmp_path):
    from tests.support.storage import database
    from oko.collection.contracts import SourceError
    for name,missing in [('missing',True),('error',False)]:
        root=publication_root(tmp_path/name)
        class Captions:
            async def fetch(self,video):
                if missing:
                    return None
                raise SourceError('Temporary caption error')
        def response(request):
            return httpx.Response(200,text=feed_xml()) if request.url.path=='/feeds/videos.xml' else httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
        models=[]
        with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,
                rss_transport=httpx.MockTransport(response),youtube_captions=Captions(),
                youtube_history=History([VIDEO]),ai_transport=model_transport(models)),headers=HEADERS) as client:
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
            subscribe(client)
            presence(client,'reader')
            card=wait_for(client,lambda:(cards[0] if (cards:=read_all_cards(client, '/api/v3/cards')) else None))
            if missing:
                value=wait_for(client,lambda:(value if (value:=client.get('/api/v3/publication/'+card['id']).json())['theses']['result'] is not None else None))
                assert value['theses']['result']['points']==[]
            else:
                wait_for(client,lambda:'Temporary caption error' in client.get('/api/v3/publication/'+card['id']).json()['completeness'].get('message',''))
                assert client.get('/api/v3/publication/'+card['id']).json()['theses']['result'] is None
            assert not any(request.url.path=='/v1/chat/completions' for request in models)
            with database(root) as db:
                row=db.execute('SELECT status , processing_metadata FROM items WHERE id = %s',(card['id'],)).fetchone()
                if missing:
                    assert row[0]=='ready' and json.loads(row[1])['summary']['reason']=='no_input'
                else:
                    assert row[0]=='pending_collection' and 'summary' not in json.loads(row[1])


def test_youtube_summary_uses_raw_captions_after_filtering(tmp_path):
    root=publication_root(tmp_path)
    calls=[]
    class Captions:
        async def fetch(self,video):
            return 'Useful words. Ad text.'
    def response(request):
        return httpx.Response(200,text=feed_xml()) if request.url.path=='/feeds/videos.xml' else httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    native=model_transport([])
    def model(request):
        if request.url.path!='/v1/chat/completions':
            return native.handler(request)
        task=json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        if task['operation']=='filter':
            result={'hidden':False,'removed':['Ad text.'],'reason':'Advertising'}
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
        return native.handler(request)
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,
            rss_transport=httpx.MockTransport(response),youtube_captions=Captions(),
            youtube_history=History([VIDEO]),ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':'Remove advertising'})
        subscribe(client)
        presence(client,'reader')
        card=wait_for(client,lambda:next((card for card in read_all_cards(client, '/api/v3/cards') if client.get('/api/v3/publication/'+card['id']).json()['theses']['result']),None))
        assert any(call['operation']=='filter' for call in calls)
        summary=next(call for call in calls if call['operation']=='theses')
        assert summary['sources'][0]['text']=='Useful words. Ad text.'
        from tests.support.storage import database
        from oko.storage.processing_input import digest
        with database(root) as db:
            saved=db.execute('SELECT processing_metadata , content FROM items WHERE id = %s',(card['id'],)).fetchone()
            metadata=json.loads(saved[0])
            blocks=json.loads(saved[1])['blocks']
            index=next(i for i,b in enumerate(blocks) if b.get('role')=='subtitles')
            media=blocks[index]['media_id']
        assert metadata['summary']['input_hash']==digest([dict(type='text',role='body',text='Useful words. Ad text.',source_media_id=media,source_block_index=index)])
        material=client.get('/api/v3/publication/'+card['id']).json()
        assert material['text']=='Useful words. Ad text.'
        assert material['filter']['result']['text']=='Useful words. '


def test_youtube_metadata_refresh_keeps_caption_file_and_accepted_processing(tmp_path):
    root=publication_root(tmp_path)
    phase=[False]
    captions_calls=[]
    class Captions:
        async def fetch(self,video):
            captions_calls.append(video)
            return 'Stable original captions.'
    def response(request):
        if request.url.path=='/feeds/videos.xml':
            return httpx.Response(200,text=feed_xml().replace('О границах модели','New title' if phase[0] else 'Old title'))
        return httpx.Response(200,content=PNG+(b'changed' if phase[0] else b''),headers={'content-type':'image/png'})
    models=[]
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,
            rss_transport=httpx.MockTransport(response),youtube_captions=Captions(),
            youtube_history=History([VIDEO]),ai_transport=model_transport(models)),headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
        subscribe(client)
        presence(client,'reader')
        card=wait_for(client,lambda:next((card for card in read_all_cards(client, '/api/v3/cards') if client.get('/api/v3/publication/'+card['id']).json()['theses']['result'] and any(a['kind']=='image' for a in [a for a in client.get('/api/v3/publication/'+card['id']).json()['media'] if a['role']=='attachment'])),None))
        original=client.get('/api/v3/publication/'+card['id']).json()
        caption_url=next(a['url'] for a in [media for media in original['media'] if media['role'] == 'attachment'] if a['kind']=='file')
        count=sum(request.url.path=='/v1/chat/completions' for request in models)
        phase[0]=True
        client.post('/api/v3/collect')
        updated=wait_for(client,lambda:(m if (m:=client.get('/api/v3/publication/'+card['id']).json())['title']=='New title' and not m['loading'] and any(a['kind']=='image' for a in [media for media in m['media'] if media['role'] == 'attachment']) else None))
        assert updated['text']=='Stable original captions.'
        assert updated['theses']['result']==original['theses']['result']
        assert next(a['url'] for a in [media for media in updated['media'] if media['role'] == 'attachment'] if a['kind']=='file')==caption_url
        assert captions_calls==[VIDEO] and sum(request.url.path=='/v1/chat/completions' for request in models)==count
        cover=next(a['url'] for a in [media for media in updated['media'] if media['role'] == 'attachment'] if a['kind']=='image')
        assert client.get(cover).content==PNG+b'changed'


def test_manual_caption_revalidation_rotates_only_changed_file_and_results(tmp_path):
    import time
    root=publication_root(tmp_path)
    wall=[time.time()]
    text=['First captions.']
    calls=[]
    class Captions:
        async def fetch(self,video):
            calls.append(video)
            return text[0]
    def response(request):
        return httpx.Response(200,text=feed_xml()) if request.url.path=='/feeds/videos.xml' else httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    models=[]
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,wall_clock=lambda:wall[0],
            rss_transport=httpx.MockTransport(response),youtube_captions=Captions(),
            youtube_history=History([VIDEO]),ai_transport=model_transport(models)),headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
        subscribe(client)
        presence(client,'reader')
        card=wait_for(client,lambda:next((card for card in read_all_cards(client, '/api/v3/cards') if client.get('/api/v3/publication/'+card['id']).json()['theses']['result']),None))
        old=client.get('/api/v3/publication/'+card['id']).json()
        old_file=next(a['url'] for a in [media for media in old['media'] if media['role'] == 'attachment'] if a['kind']=='file')
        text[0]='Updated captions.'
        wall[0]+=11
        client.post('/api/v3/materials/'+card['id']+'/retry')
        fresh=wait_for(client,lambda:(m if (m:=client.get('/api/v3/publication/'+card['id']).json())['text']=='Updated captions.' and m['theses']['result'] else None))
        current_file=next(a['url'] for a in [media for media in fresh['media'] if media['role'] == 'attachment'] if a['kind']=='file')
        assert current_file!=old_file and client.get(old_file).status_code==404
        assert client.get(current_file).content==b'Updated captions.'
        count=sum(request.url.path=='/v1/chat/completions' for request in models)
        assert count==2
        wall[0]+=11
        client.post('/api/v3/materials/'+card['id']+'/retry')
        wait_for(client,lambda:len(calls)==3 and not client.get('/api/v3/publication/'+card['id']).json()['loading'])
        same=client.get('/api/v3/publication/'+card['id']).json()
        assert next(a['url'] for a in [media for media in same['media'] if media['role'] == 'attachment'] if a['kind']=='file')==current_file
        assert sum(request.url.path=='/v1/chat/completions' for request in models)==count
        # Losing the local input must not turn an unknown transcript into a
        # confirmed empty input or keep presenting its old result as current.
        from tests.support.storage import database
        with database(root) as db:
            key=db.execute('SELECT storage_key FROM media_files WHERE id = %s',(current_file.rsplit('/',1)[-1],)).fetchone()[0]
        (root/key).unlink()
        missing=client.get('/api/v3/publication/'+card['id']).json()
        assert missing['theses']['result'] is None
        assert missing['source']['captions']=='error'
        assert sum(request.url.path=='/v1/chat/completions' for request in models)==count


def test_failed_caption_revalidation_keeps_verified_file_and_results(tmp_path):
    import time
    from oko.collection.contracts import SourceError
    root=publication_root(tmp_path)
    wall=[time.time()]
    broken=[False]
    class Captions:
        async def fetch(self,video):
            if broken[0]:
                raise SourceError('Refresh failed')
            return 'Verified captions.'
    def response(request):
        return httpx.Response(200,text=feed_xml()) if request.url.path=='/feeds/videos.xml' else httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    models=[]
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,wall_clock=lambda:wall[0],
            rss_transport=httpx.MockTransport(response),youtube_captions=Captions(),
            youtube_history=History([VIDEO]),ai_transport=model_transport(models)),headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
        subscribe(client)
        presence(client,'reader')
        card=wait_for(client,lambda:next((card for card in read_all_cards(client, '/api/v3/cards') if client.get('/api/v3/publication/'+card['id']).json()['theses']['result']),None))
        old=client.get('/api/v3/publication/'+card['id']).json()
        url=next(a['url'] for a in [media for media in old['media'] if media['role'] == 'attachment'] if a['kind']=='file')
        broken[0]=True
        wall[0]+=11
        client.post('/api/v3/materials/'+card['id']+'/retry')
        wait_for(client,lambda:any(problem['message']=='Refresh failed' for problem in client.get('/api/v3/status/details').json()['problems']['current']))
        kept=client.get('/api/v3/publication/'+card['id']).json()
        assert kept['text']=='Verified captions.' and kept['theses']['result']==old['theses']['result']
        assert client.get(url).content==b'Verified captions.'
        assert sum(request.url.path=='/v1/chat/completions' for request in models)==1


def test_caption_absence_then_reappearance_never_reuses_a_previous_file_url(tmp_path):
    import time
    root=publication_root(tmp_path)
    wall=[time.time()]
    text=['First captions.']
    class Captions:
        async def fetch(self,video):
            return text[0]
    def response(request):
        return httpx.Response(200,text=feed_xml()) if request.url.path=='/feeds/videos.xml' else httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,wall_clock=lambda:wall[0],
            rss_transport=httpx.MockTransport(response),youtube_captions=Captions(),
            youtube_history=History([VIDEO]),ai_transport=model_transport([])),headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
        subscribe(client)
        presence(client,'reader')
        card=wait_for(client,lambda:next((card for card in read_all_cards(client, '/api/v3/cards') if client.get('/api/v3/publication/'+card['id']).json()['theses']['result']),None))
        old=client.get('/api/v3/publication/'+card['id']).json()
        old_file=next(a['url'] for a in [media for media in old['media'] if media['role'] == 'attachment'] if a['kind']=='file')
        text[0]=None
        wall[0]+=11
        client.post('/api/v3/materials/'+card['id']+'/retry')
        absent=wait_for(client,lambda:(m if (m:=client.get('/api/v3/publication/'+card['id']).json())['text']=='' and m['theses']['result'] is not None else None))
        assert absent['theses']['result']['points']==[] and client.get(old_file).status_code==404
        from tests.support.storage import database
        with database(root) as db:
            ids=json.loads(db.execute('SELECT media_ids FROM items WHERE id = %s',(card['id'],)).fetchone()[0])
            assert old_file.rsplit('/',1)[-1] not in ids
        text[0]='Reappeared captions.'
        wall[0]+=11
        client.post('/api/v3/materials/'+card['id']+'/retry')
        fresh=wait_for(client,lambda:(m if (m:=client.get('/api/v3/publication/'+card['id']).json())['text']=='Reappeared captions.' and m['theses']['result'] else None))
        new_file=next(a['url'] for a in [media for media in fresh['media'] if media['role'] == 'attachment'] if a['kind']=='file')
        assert new_file!=old_file and client.get(old_file).status_code==404
        assert client.get(new_file).content==b'Reappeared captions.'


def test_high_importance_youtube_respects_explicitly_disabled_theses(tmp_path):
    root = publication_root(tmp_path)
    class Captions:
        async def fetch(self, video):
            return 'Synthetic captions.'
    def response(request):
        if request.url.path == '/feeds/videos.xml':
            return httpx.Response(200, text=feed_xml())
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    models = []
    with TestClient(create_app(root, token='test', api_only=True, rss_enabled=True,
            rss_transport=httpx.MockTransport(response), youtube_captions=Captions(),
            youtube_history=History([VIDEO]), ai_transport=model_transport(models)), headers=HEADERS) as client:
        assert client.put('/api/v3/processors/llamacpp', json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True}).status_code == 200
        assert client.patch('/api/v3/settings/oko/global', json={'processor':'llamacpp','theses':False,'prompt_filters':''}).status_code == 200
        feed = subscribe(client)
        assert client.patch('/api/v3/feeds/' + feed, json={'importance':'high'}).status_code == 200
        presence(client, 'reader')
        card = wait_for(client, lambda: (cards[0] if (cards := read_all_cards(client, '/api/v3/cards')) else None))
        publication = wait_for(client, lambda: (value if (value := client.get('/api/v3/publication/' + card['id']).json())['text'] == 'Synthetic captions.' else None))
        assert publication['theses']['state'] == 'disabled'
        assert publication['theses']['result'] is None
        assert not any(request.url.path == '/v1/chat/completions' for request in models)
