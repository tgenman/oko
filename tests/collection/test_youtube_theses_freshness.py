from oko.collection.contracts import DiscoveryResult, FetchResult, FetchRequirements, FetchResources
from tests.support.presence import presence, close_presence
import json
import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.subscriptions import saved_sources
from oko.collection.contracts import Material, Publication
from oko.storage.store import Store
from tests.support.reader import HEADERS, wait_for
from tests.support.models import model_transport
from tests.support.youtube import PNG, VIDEO, feed_xml, subscribe


class Captions:
    def __init__(self):
        self.calls = []

    async def fetch(self, video):
        self.calls.append(video)
        return 'Модель принимает только текст. Источники сохраняются точно.'


def wait(client, predicate):
    def ready():
        cards = client.get('/api/v3/cards').json()
        if len(cards) == 1:
            material = client.get('/api/v3/materials/' + cards[0]['id']).json()
            if predicate(material):
                return material
        return None
    return wait_for(client, ready)


def transport():
    def reply(request):
        if request.url.path == '/feeds/videos.xml':
            return httpx.Response(200, text=feed_xml())
        assert request.url.host == 'i.ytimg.com'
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    return httpx.MockTransport(reply)


@pytest.mark.parametrize('prompt_filters', ['', 'Remove advertisements'])
def test_metadata_updates_reuse_caption_theses_across_restart(tmp_path, prompt_filters):
    captions, calls = Captions(), []
    metadata = {'title': 'Initial title', 'preview': PNG}

    def reply(request):
        if request.url.path == '/feeds/videos.xml':
            xml = feed_xml().replace('О границах модели', metadata['title'])
            return httpx.Response(200, text=xml)
        assert request.url.host == 'i.ytimg.com'
        return httpx.Response(200, content=metadata['preview'], headers={'content-type': 'image/png'})

    native = model_transport(calls)

    def model_reply(request):
        if request.url.path == '/v1/chat/completions':
            task = json.loads(json.loads(request.content)['messages'][1]['content'])
            if task['operation'] == 'filter':
                result = {'hidden': False, 'reason': 'Advertisement', 'removed': ['Модель принимает только текст.']}
                return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
                    'message': {'role': 'assistant', 'content': json.dumps(result)}}]})
        return native.handler(request)

    options = dict(token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply),
                   youtube_captions=captions, ai_transport=httpx.MockTransport(model_reply))

    def summary_calls():
        return sum(r.url.path == '/v1/chat/completions' for r in calls)

    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={
                     'processor': 'llamacpp', 'prompt_filters': prompt_filters})
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080',
                   'model': 'local:latest', 'local_execution': True})
        subscribe(client)
        presence(client, 'reader')
        original = wait(client, lambda m: m['theses']['result'] and not m['content']['loading'])
        assert summary_calls() == 1 and captions.calls == [VIDEO]
        request = next(r for r in calls if r.url.path == '/v1/chat/completions')
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        assert task['sources'][0]['text'] == original['content']['text']

        for field, value in [('title', 'Updated title'), ('preview', PNG + b'changed')]:
            metadata[field] = value
            client.post('/api/v3/collect')
            updated = wait(client, lambda m: m['content']['revision_id'] != original['content']['revision_id'] and not m['content']['loading']
                           and m['filter']['attempt']['state'] in ('disabled','ready'))
            assert updated['content']['text'] == original['content']['text']
            assert updated['theses']['result'] == original['theses']['result']
            assert summary_calls() == 1 and captions.calls == [VIDEO]
            original = updated

    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        presence(client, 'reader')
        material = wait(client, lambda m: m['theses']['result'] and not m['content']['loading'])
        assert material['theses']['result'] == original['theses']['result']
        assert summary_calls() == 1 and captions.calls == [VIDEO]
        client.post('/api/v3/materials/' + material['id'] + '/viewed')
        assert not (tmp_path / 'content' / material['id']).exists()


def test_changed_caption_text_requires_new_theses(tmp_path):
    class SavedCaptions:
        fetch_requirements = FetchRequirements()
        changes = None
        text = 'First saved transcript'

        async def discover(self, cursor):
            return DiscoveryResult(tuple([Publication(VIDEO, 'https://www.youtube.com/watch?v=' + VIDEO, 1800000000)]), 'one')

        async def fetch(self, publication, resources=FetchResources()):
            return FetchResult(Material('Video', self.text, captions='available'))

    # Synthetic revisions exercise text freshness without querying real subtitles.
    saved_sources(tmp_path, [('https://www.youtube.com/feeds/videos.xml?channel_id=UC'+'a'*22, 'Video', 'Видео')])
    with TestClient(create_app(tmp_path, token='test'), headers=HEADERS) as client:
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]['id']
    source, calls = SavedCaptions(), []
    with TestClient(create_app(tmp_path, token='test', source=source, source_feed=feed,
                               ai_transport=model_transport(calls)), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp'})
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080',
                   'model': 'local:latest', 'local_execution': True})
        presence(client, 'reader')
        original = wait(client, lambda m: m['theses']['result'] and not m['content']['loading'])
        source.text = 'Changed saved transcript'
        client.post('/api/v3/collect')
        wait(client, lambda m: m['theses']['result'] and m['content']['text'] == source.text and not m['content']['loading'])
        requests = [json.loads(json.loads(r.content)['messages'][1]['content'])
                    for r in calls if r.url.path == '/v1/chat/completions']
        assert [r['sources'][0]['text'] for r in requests] == [original['content']['text'], source.text]
        assert requests[0]['sources'][0]['version'] != requests[1]['sources'][0]['version']


@pytest.mark.parametrize('interrupted', [False, True])
def test_previous_caption_signature_is_upgraded_without_model_call(tmp_path, interrupted):
    captions, calls = Captions(), []
    options = dict(token='test', rss_enabled=True, rss_transport=transport(),
                   youtube_captions=captions, ai_transport=model_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp'})
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080',
                   'model': 'local:latest', 'local_execution': True})
        subscribe(client)
        presence(client, 'reader')
        original = wait(client, lambda m: m['theses']['result'] and not m['content']['loading'])

    # Seed the previously persisted format; acceptance is checked through HTTP.
    store = Store(tmp_path, lambda point: None)
    try:
        signature = store.processing.theses._signature(original['id'], legacy=True)
        path = store.path(original['id']).parent / 'theses.md'
        raw, text = path.read_text().split('\n\n', 1)
        header = json.loads(raw)
        header['signature'] = signature
        path.write_text(json.dumps(header) + '\n\n' + text)
        with store.db:
            store.db.execute('UPDATE thesis_inputs SET signature=? WHERE item=?', (signature, original['id']))
        if interrupted:
            def fault(point):
                if point == 'after_theses_move':
                    raise OSError('Interrupted signature upgrade')
            store.fault = fault
            with pytest.raises(OSError, match='Interrupted signature upgrade'):
                store.processing.recover()
    finally:
        store.db.close()

    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        presence(client, 'reader')
        material = wait(client, lambda m: m['theses']['result'] and not m['content']['loading'])
        assert material['theses']['result'] == original['theses']['result']
        assert sum(r.url.path == '/v1/chat/completions' for r in calls) == 1
        assert captions.calls == [VIDEO]
