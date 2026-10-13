class CodexService:
    def __init__(self):
        self.inputs = []

    async def check(self):
        return dict(available=True, local=False, auth='chatgpt', version='0.136.0', reason='', limits_checked=True)

    async def catalog(self):
        return [dict(model='synthetic', display_name='Synthetic', default=True,
                     reasoning_efforts=['low'], default_reasoning_effort='low', fast_supported=False)]

    async def complete(self, prompt, schema, directory, choice):
        import json
        self.inputs.append(prompt)
        request = json.loads(prompt.split('\n', 1)[1])
        source = request['sources'][0]
        return json.dumps({'points': [{'text': 'Просмотрено удаляет содержимое.', 'sources': [{'id': source['id'], 'url': source['url']}]}]})


def configure_codex(client):
    client.post('/api/tabs/reader/heartbeat')
    config = {kind: dict(model='synthetic', reasoning_effort='low', fast=False)
              for kind in ('filter', 'theses', 'events', 'digest')}
    response = client.put('/api/processors/codex', json=config)
    assert response.status_code == 200, response.text
