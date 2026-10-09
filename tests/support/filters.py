import json, httpx
from oko.collection.contracts import Material, Publication
from tests.support.models import model_transport


class FilterSource:
    async def discover(self, cursor):
        return ([Publication(str(i), f'https://example.org/filter/{i}', 1800000000-i) for i in range(3)] if cursor is None else []), '3'

    async def fetch(self, publication):
        texts = ['Реклама: купите курс.', 'Открылась библиотека. Реклама: купите курс.', 'Изменён закон о выборах.']
        return Material('Материал ' + publication.id, texts[int(publication.id)])


def transport(calls):
    native = model_transport(calls)
    def respond(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        calls.append(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        if task['operation'] != 'filter':
            return native.handler(request)
        text = task['sources'][0]['text']
        result = dict(hidden=text.startswith('Реклама:'), reason='Реклама' if 'Реклама:' in text else '', removed=['Реклама: купите курс.'] if text.startswith('Открылась') else [])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result)}}]})
    return httpx.MockTransport(respond)
