from tests.support.publications import subtitle_text,reasons
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
from tests.support.youtube import VIDEO, PNG, feed_xml, subscribe, History
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
            card=wait_for(client,lambda:next((card for card in read_all_cards(client, '/api/v3/cards') if client.get('/api/v3/publication/'+card['id']).json()['summarized_content'].get('points')),None))
            material=client.get('/api/v3/publication/'+card['id']).json()
            assert subtitle_text(client,material)=='Original raw captions.'
            assert captions_calls==[VIDEO] and [media for media in material['media'] if media['url'] is not None]
            task=json.loads(json.loads(next(request for request in models if request.url.path=='/v1/chat/completions').content)['messages'][1]['content'])
            assert task['sources'][0]['text']=='Original raw captions.'
            release.set()
            wait_for(client,lambda:any(a['kind']=='image' for a in [media for media in client.get('/api/v3/publication/'+card['id']).json()['media'] if media['url'] is not None]))
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
                value=wait_for(client,lambda:(value if (value:=client.get('/api/v3/publication/'+card['id']).json())['summarized_content'].get('points') is not None else None))
                assert value['summarized_content']['points']==[]
            else:
                wait_for(client,lambda:'Temporary caption error' in reasons(client.get('/api/v3/publication/'+card['id']).json()))
                assert client.get('/api/v3/publication/'+card['id']).json()['summarized_content'].get('points') is None
            assert not any(request.url.path=='/v1/chat/completions' for request in models)
            with database(root) as db:
                row=db.execute('SELECT status , processing_metadata FROM publications WHERE id = %s',(card['id'],)).fetchone()
                if missing:
                    assert row[0]=='ready' and json.loads(row[1])['summary']['reason']=='no_input'
                else:
                    assert row[0]=='pending_collection' and 'summary' not in json.loads(row[1])


def test_youtube_summary_uses_filtered_captions(tmp_path):
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
            result={'hidden':False,'removed':['Ad text.'] if task['prompt']=='Remove advertising' else [],'reason':'Advertising'}
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
        return native.handler(request)
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,
            rss_transport=httpx.MockTransport(response),youtube_captions=Captions(),
            youtube_history=History([VIDEO]),ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':'Remove advertising'})
        subscribe(client)
        presence(client,'reader')
        card=wait_for(client,lambda:next((card for card in read_all_cards(client, '/api/v3/cards') if client.get('/api/v3/publication/'+card['id']).json()['summarized_content'].get('points')),None))
        assert any(call['operation']=='filter' for call in calls)
        summary=next(call for call in calls if call['operation']=='theses')
        assert summary['sources'][0]['text']=='Useful words. '
        from tests.support.storage import database
        from oko.storage.processing_input import digest
        with database(root) as db:
            saved=db.execute('SELECT processing_metadata , content, filtered_content FROM publications WHERE id = %s',(card['id'],)).fetchone()
            metadata=json.loads(saved[0])
            blocks=saved[1]['blocks']
            for document in (saved[1],json.loads(saved[2])):
                youtube=document.get('extra',{}).get('youtube',{})
                assert 'captions' not in youtube and 'captions_digest' not in youtube
            index=next(i for i,b in enumerate(blocks) if b.get('role')=='subtitles')
            media=blocks[index]['media_id']
        assert metadata['summary']['input_hash']==digest(summary['sources'])
        material=client.get('/api/v3/publication/'+card['id']).json()
        assert subtitle_text(client,material)=='Useful words. Ad text.'
        assert '\n\n'.join(b['text'] for b in material['filtered_content']['blocks'] if b['type']=='text' and b['role']=='body')=='Useful words. '
        old_summary=material['summarized_content'].get('points')
        client.patch('/api/v3/settings/oko/global',json={'prompt_filters':'Keep all'})
        assert client.post('/api/v3/materials/'+card['id']+'/filter/retry').status_code==200
        wait_for(client,lambda:client.get('/api/v3/publication/'+card['id']).json()['processing_metadata'].get('filter',{}).get('prompt')=='Keep all')
        assert client.get('/api/v3/publication/'+card['id']).json()['summarized_content'].get('points')==old_summary
        assert client.post('/api/v3/materials/'+card['id']+'/regenerate').status_code==200
        wait_for(client,lambda:len([call for call in calls if call['operation']=='theses'])==2)
        assert [call for call in calls if call['operation']=='theses'][-1]['sources'][0]['text']=='Useful words. Ad text.'


def test_youtube_rediscovery_keeps_saved_title_media_and_processing(tmp_path):
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
        card=wait_for(client,lambda:next((card for card in read_all_cards(client, '/api/v3/cards') if client.get('/api/v3/publication/'+card['id']).json()['summarized_content'].get('points') and any(a['kind']=='image' for a in [a for a in client.get('/api/v3/publication/'+card['id']).json()['media'] if a['url'] is not None])),None))
        original=client.get('/api/v3/publication/'+card['id']).json()
        caption_url=next(a['url'] for a in [media for media in original['media'] if media['url'] is not None] if a['kind']=='file')
        count=sum(request.url.path=='/v1/chat/completions' for request in models)
        phase[0]=True
        client.post('/api/v3/collect')
        wait_for(client,lambda:client.get('/api/v3/status/details').json()['work']==[{'phase':'idle'}])
        updated=client.get('/api/v3/publication/'+card['id']).json()
        assert updated['title']=='Old title'
        assert subtitle_text(client,updated)=='Stable original captions.'
        assert updated['summarized_content'].get('points')==original['summarized_content'].get('points')
        assert next(a['url'] for a in [media for media in updated['media'] if media['url'] is not None] if a['kind']=='file')==caption_url
        assert captions_calls==[VIDEO] and sum(request.url.path=='/v1/chat/completions' for request in models)==count
        cover=next(a['url'] for a in [media for media in updated['media'] if media['url'] is not None] if a['kind']=='image')
        assert client.get(cover).content==PNG


def test_missing_captions_are_downloaded_again_after_reader_detects_loss(tmp_path):
    from tests.support.storage import database
    root = publication_root(tmp_path)
    calls = []
    class Captions:
        async def fetch(self, video):
            calls.append(video)
            return 'Recovered captions.'
    def response(request):
        return httpx.Response(200,text=feed_xml()) if request.url.path=='/feeds/videos.xml' else httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,
            rss_transport=httpx.MockTransport(response),youtube_captions=Captions(),
            youtube_history=History([VIDEO]),ai_transport=model_transport([])),headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
        subscribe(client)
        presence(client,'reader')
        card=wait_for(client,lambda:next((c for c in read_all_cards(client,'/api/v3/cards') if client.get('/api/v3/publication/'+c['id']).json()['summarized_content'].get('points')),None))
        with database(root) as db:
            key=db.execute("SELECT storage_key FROM media_files WHERE publication_id=%s AND resource_key='youtube:subtitles'",(card['id'],)).fetchone()[0]
        (root/key).unlink()
        client.get('/api/v3/publication/'+card['id'])
        wait_for(client,lambda: len(calls)>=2 and subtitle_text(client,client.get('/api/v3/publication/'+card['id']).json())=='Recovered captions.',timeout=30)
        assert calls==[VIDEO,VIDEO]


def test_youtube_description_is_saved_as_common_block(tmp_path):
    from tests.support.storage import database
    root = publication_root(tmp_path)
    async def response(request):
        if request.url.path == '/feeds/videos.xml':
            return httpx.Response(200, text=feed_xml().replace('<media:group>', '<summary>Saved description</summary><media:group>'))
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    class Captions:
        async def fetch(self, video):
            return 'Caption text'
    with TestClient(create_app(root, token='test', api_only=True, rss_enabled=True,
            rss_transport=httpx.MockTransport(response), youtube_captions=Captions(),
            youtube_history=History([VIDEO])), headers=HEADERS) as client:
        subscribe(client)
        presence(client, 'reader')
        card = wait_for(client, lambda: next(iter(read_all_cards(client, '/api/v3/cards')), None))
        def with_description():
            data=client.get('/api/v3/publication/'+card['id']).json()
            return data if any(block.get('role')=='description' for block in data['content'].get('blocks',[])) else None
        publication = wait_for(client,with_description)
        assert any(block.get('role')=='description' and block.get('text')=='Saved description' for block in publication['content']['blocks'])
    with database(root) as db:
        content = db.execute('SELECT content FROM publications WHERE id=%s', (card['id'],)).fetchone()[0]
        assert any(block.get('role') == 'description' and block.get('text') == 'Saved description' for block in content['blocks'])
        assert '_reader' not in content['extra']
