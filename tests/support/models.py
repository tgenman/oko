import json, httpx


def model_transport(calls):
    def reply(request):
        calls.append(request)
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'local:latest', 'owned_by': 'llamacpp', 'meta': {'size': 100}}]})
        if request.url.path == '/props':
            return httpx.Response(200, json={'model_path': '/models/local.gguf', 'build_info': 'b-test'})
        body = json.loads(request.content)
        source = json.loads(body['messages'][1]['content'])['sources'][0]
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps({'points': [{'text': 'Просмотрено удаляет текст.', 'sources': [{'id': source['id'], 'url': source['url']}]}]})}}]})
    return httpx.MockTransport(reply)
