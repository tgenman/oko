from tests.support.publications import summary,subtitle_text,loading,filter_state
from tests.support.cards import read_all_cards
from tests.support.presence import presence, close_presence
import json
import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.app import create_app
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
        cards = read_all_cards(client, '/api/v3/cards')
        if len(cards) == 1:
            material = client.get('/api/v3/publication/' + cards[0]['id']).json()
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
        original = wait(client, lambda m: summary(m) and not loading(m))
        assert summary_calls() == 1 and captions.calls == [VIDEO]
        request = next(r for r in calls if r.url.path == '/v1/chat/completions')
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        expected=' Источники сохраняются точно.' if prompt_filters else subtitle_text(client,original)
        assert task['sources'][0]['text'] == expected

        for field, value in [('title', 'Updated title'), ('preview', PNG + b'changed')]:
            metadata[field] = value
            client.post('/api/v3/collect')
            updated = wait(client, lambda m: not loading(m) and filter_state(m) in ('disabled','ready'))
            assert updated['content']==original['content'] and updated['media_ids']==original['media_ids']
            assert subtitle_text(client,updated) == subtitle_text(client,original)
            assert summary(updated)==summary(original)
            assert summary_calls() == 1 and captions.calls == [VIDEO]
            original = updated

    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        presence(client, 'reader')
        material = wait(client, lambda m: summary(m) and not loading(m))
        assert summary(material)==summary(original)
        assert summary_calls() == 1 and captions.calls == [VIDEO]
        client.post('/api/v3/materials/' + material['id'] + '/viewed')
        assert not (tmp_path / 'content' / material['id']).exists()


def test_changed_caption_text_requires_new_theses(tmp_path):
    class SavedCaptions:
        text = 'First saved transcript'

        async def fetch(self, video):
            assert video == VIDEO
            return self.text

    captions, calls = SavedCaptions(), []
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=transport(),
                               youtube_captions=captions, ai_transport=model_transport(calls)), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp'})
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080',
                   'model': 'local:latest', 'local_execution': True})
        subscribe(client)
        presence(client, 'reader')
        original = wait(client, lambda m: summary(m) and not loading(m))
        old_hash = original['processing_metadata']['summary']['input_hash']
        captions.text = 'Changed saved transcript'
        response = client.post('/api/v3/materials/' + original['id'] + '/rebuild')
        assert response.status_code == 200, response.text
        updated = wait(client, lambda m: summary(m) and not loading(m)
                       and m['processing_metadata']['summary']['input_hash'] != old_hash)
        requests = [json.loads(json.loads(r.content)['messages'][1]['content'])
                    for r in calls if r.url.path == '/v1/chat/completions']
        assert [r['sources'][0]['text'] for r in requests] == ['First saved transcript', captions.text]
        assert requests[0]['sources'][0]['version'] != requests[1]['sources'][0]['version']
        assert subtitle_text(client, updated) == captions.text
