from tests.support.presence import presence, close_presence
import json, httpx
from oko.collection.contracts import Material, Publication
from tests.support.models import model_transport
from tests.support.reader import wait_for


class FilterSource:
    async def discover(self, cursor):
        return ([Publication(str(i), f'https://example.org/filter/{i}', 1800000000-i) for i in range(3)] if cursor is None else []), '3'

    async def fetch(self, publication):
        texts = ['Реклама: купите курс.', 'Открылась библиотека. Реклама: купите курс.', 'Изменён закон о выборах.']
        return Material('Материал ' + publication.id, texts[int(publication.id)])


def transport(calls, *, theses=False):
    native = model_transport(calls)
    def respond(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        if task['operation'] != 'filter':
            assert theses and task['operation'] == 'theses', f"Filter model does not support operation: {task['operation']}"
            return native.handler(request)
        calls.append(request)
        text = task['sources'][0]['text']
        result = dict(hidden=text.startswith('Реклама:'), reason='Реклама' if 'Реклама:' in text else '', removed=['Реклама: купите курс.'] if text.startswith('Открылась') else [])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result)}}]})
    return httpx.MockTransport(respond)


def setup_filters(client):
    client.patch('/api/v3/settings/oko/global', json={'theses': False, 'prompt_filters': '', 'processor': 'llamacpp'})
    presence(client, 'reader')
    wait_for(client, lambda: len(c := client.get('/api/v3/cards').json()) == 3 and all((x['availability'] == 'available') for x in c))
    client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
    client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp', 'prompt_filters': 'Убирай рекламу. Сохраняй законы.'})
