"""Combined reading paths through HTTP and replaceable external services."""
import json
import time

import httpx
from fastapi.testclient import TestClient

from oko.reader.app import create_app
from tests.support.codex import CodexService
from tests.support.digests import digest_transport
from tests.support.events import event_transport
from tests.support.filters import transport as filter_transport
from tests.support.reader import HEADERS, wait_for
from tests.support.telegram import Telegram, connect, settled


class ReadingTelegram(Telegram):
    def __init__(self):
        self.read_max = 0
        self.acks = []
        self.history = [dict(id=i, date=time.time()-60, text=text, group=None, media=None)
                        for i, text in [(11, 'Реклама: купите курс.'),
                                        (29, 'Открылась библиотека. Реклама: купите курс.'),
                                        (42, 'В библиотеке 30 мест.')]]

    async def page(self, account, peer, after, since):
        return dict(messages=[m for m in self.history if m['id'] > after and m['date'] >= since], more=False)

    async def messages(self, account, peer, ids):
        return [m for m in self.history if m['id'] in ids]

    async def read_state(self, account, peer, since):
        return dict(read_max=self.read_max, initial=0)

    async def read_window(self, account, peer, after, until):
        return dict(ids=[m['id'] for m in self.history if after < m['id'] <= until], more=False)

    async def acknowledge(self, account, peer, max_id):
        self.acks.append(max_id)
        self.read_max = max(self.read_max, max_id)


def reading_transport():
    filters = filter_transport([])
    events = event_transport([])
    digests = digest_transport([])

    def respond(request):
        if request.url.path == '/v1/chat/completions':
            task = json.loads(json.loads(request.content)['messages'][1]['content'])
            if task['operation'] == 'events':
                return events.handler(request)
            if task['operation'].startswith('digest'):
                return digests.handler(request)
        return filters.handler(request)

    return httpx.MockTransport(respond)


def test_filtered_telegram_digest_completion_recovers_confirmation_without_content(tmp_path):
    network = ReadingTelegram()

    def fault(point):
        if point == 'after_telegram_acknowledge':
            raise OSError('confirmation result lost')

    options = dict(token='test', telegram_client=network, ai_transport=reading_transport())
    with TestClient(create_app(tmp_path, fault=fault, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        feed = connect(client)
        originals = settled(client, 3)
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'events': True, 'theses': True, 'prompt_filters': 'Убери рекламу.'})
        event = wait_for(client, lambda: next((c for c in client.get('/api/cards').json() if c.get('event')), None))
        assert len(event['sources']) == 2
        hidden = wait_for(client, lambda: next((c for c in client.get('/api/cards?show_hidden=true').json() if c.get('filter', {}).get('hidden')), None))
        identity = client.post('/api/digests/folder/' + feed['folder_id'], json={}).json()['id']
        digest = wait_for(client, lambda: m if (m := client.get('/api/materials/' + identity).json()).get('digest') else None)
        assert {s['id'] for s in digest['sources']} == {c['id'] for c in originals}
        assert hidden['id'] in {s['id'] for s in digest['sources']}
        assert network.acks == []
        preview = client.post('/api/folders/' + feed['folder_id'] + '/completion/preview').json()
        assert preview['cards'] == 1 and preview['originals'] == 3
        assert client.post('/api/folders/' + feed['folder_id'] + '/completion', json={'token': preview['token']}).status_code == 200
        wait_for(client, lambda: any(r.get('error') for r in client.get('/api/status').json()['telegram_reads']))
        assert network.acks == [42]
        assert client.get('/api/cards?show_hidden=true').json() == []
        for identity in [identity, event['id'], *[c['id'] for c in originals]]:
            assert client.get('/api/materials/' + identity).status_code == 404
        assert not list((tmp_path / 'content').iterdir())
        assert not list((tmp_path / 'events').glob('*'))
        assert not list((tmp_path / 'digests').glob('*'))

    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        wait_for(client, lambda: any(r.get('confirmed') == 42 and not r.get('error') for r in client.get('/api/status').json()['telegram_reads']))
        assert network.acks == [42]
        assert client.get('/api/cards?show_hidden=true').json() == []
        assert not list((tmp_path / 'content').iterdir())
        assert all('библиотек'.encode() not in p.read_bytes() and 'купите курс'.encode() not in p.read_bytes()
                   for p in tmp_path.rglob('*') if p.is_file())


def test_local_mixed_digest_cannot_transfer_private_content_when_switched_to_codex(tmp_path):
    class MixedTelegram(ReadingTelegram):
        async def info(self, account, peer):
            return dict(peer=-1001234567890 if peer == '@public' else -1009876543210,
                        title='Public' if peer == '@public' else 'Private', kind='channel', forum=False)

        async def page(self, account, peer, after, since):
            return dict(messages=await self.messages(account, peer, [11]) if after < 11 else [], more=False)

        async def messages(self, account, peer, ids):
            if 11 not in ids:
                return []
            return [dict(id=11, date=time.time()-60, text='PUBLIC-ORIGINAL' if peer == -1001234567890 else 'PRIVATE-ORIGINAL', group=None, media=None)]

    calls = []
    local = digest_transport(calls)
    service = CodexService()
    with TestClient(create_app(tmp_path, token='test', telegram_client=MixedTelegram(), ai_transport=local, codex_client=service), headers=HEADERS) as client:
        client.patch('/api/settings/oko/global', json={'events': False})
        client.post('/api/tabs/reader/heartbeat')
        public = connect(client, peer='@public')
        private = connect(client, peer='@private')
        originals = settled(client, 2)
        assert public['folder_id'] == private['folder_id']
        assert client.patch('/api/feeds/' + public['id'], json={'public_ai': True}).status_code == 200
        client.put('/api/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        identity = client.post('/api/digests/folder/' + public['folder_id'], json={}).json()['id']
        wait_for(client, lambda: client.get('/api/materials/' + identity).json().get('digest'))
        assert any({s['text'] for s in c['sources']} == {'PUBLIC-ORIGINAL', 'PRIVATE-ORIGINAL'} for c in calls)
        client.patch('/api/settings/oko/global', json={'processor': 'codex'})
        wait_for(client, lambda: any('публичн' in e.lower() for e in client.get('/api/status').json()['errors']))
        assert client.get('/api/materials/' + identity).status_code == 404
        public_item = next(c['id'] for c in originals if c['feed_id'] == public['id'])
        wait_for(client, lambda: client.get('/api/materials/' + public_item).json().get('theses'))
        assert service.inputs
        transferred = [json.loads(prompt.split('\n', 1)[1]) for prompt in service.inputs]
        assert all(task['operation'] == 'theses' for task in transferred)
        assert all(s['text'] == 'PUBLIC-ORIGINAL' for task in transferred for s in task['sources'])
        assert all('PRIVATE-ORIGINAL' not in prompt and 'Темы: библиотека и космос.' not in prompt for prompt in service.inputs)
        private_item = next(c['id'] for c in originals if c['feed_id'] == private['id'])
        assert client.get('/api/materials/' + private_item).json()['theses'] is None
