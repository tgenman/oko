class CodexService:
    def __init__(self):
        self.inputs = []

    async def check(self):
        return dict(available=True, local=False, auth='chatgpt', version='0.136.0', reason='', limits_checked=True)

    async def complete(self, prompt, schema, directory):
        import json
        self.inputs.append(prompt)
        request = json.loads(prompt.split('\n', 1)[1])
        source = request['sources'][0]
        return json.dumps({'points': [{'text': 'Просмотрено удаляет содержимое.', 'sources': [{'id': source['id'], 'url': source['url']}]}]})
