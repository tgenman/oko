import json, httpx
from tests.support.reader import setup_reader
from tests.support.models import model_transport


def configure(client):
    setup_reader(client)
    client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
    client.patch('/api/settings/oko/global', json={'processor': 'llamacpp'})


def digest_transport(calls):
    native = model_transport([])
    def reply(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        source = task['sources'][0]
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps({'points': [{'text': 'Темы: библиотека и космос.', 'sources': [{'id': source['id'], 'url': source['url']}]}]})}}]})
    return httpx.MockTransport(reply)
