import json, httpx
from tests.support.reader import wait_for
from tests.support.models import model_transport


def event_transport(calls):
    native = model_transport([])
    def reply(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        assert task['operation'] == 'events', f"Event model does not support operation: {task['operation']}"
        calls.append(task)
        sources = task['sources']
        refs = [dict(id=s['id'], url=s['url']) for s in sources]
        groups = []
        library = [(s, r) for s, r in zip(sources, refs) if 'библиотек' in s['text'].lower()]
        if library:
            groups.append(dict(members=[s['id'] for s, _ in library], title='Открылась библиотека', points=[dict(text='Открылась библиотека.', sources=[r for _, r in library][:1])], updates=[dict(text='Мэрия назвала вместимость 30 мест.', sources=[r]) for s, r in library if '30' in s['text']], conflicts=[dict(text='Источники называют 24 и 30 мест.', sources=[r for _, r in library])] if len(library)>1 else []))
        groups += [dict(members=[s['id']], title=s['title'], points=[dict(text=s['text'], sources=[r])], updates=[], conflicts=[]) for s, r in zip(sources, refs) if 'библиотек' not in s['text'].lower()]
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(dict(groups=groups))}}]})
    return httpx.MockTransport(reply)


def configure(client):
    client.patch('/api/settings/oko/global', json={'events': False, 'theses': False, 'digests': False, 'prompt_filters': '', 'processor': 'llamacpp'})
    client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
    client.post('/api/tabs/reader/heartbeat')
    wait_for(client, lambda: len(c := client.get('/api/cards').json()) == 3 and all(x['complete'] for x in c))
    client.patch('/api/settings/oko/global', json={'events': True})
