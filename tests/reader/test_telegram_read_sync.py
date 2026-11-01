from tests.support.cards import read_all_cards
from datetime import date
from tests.support.presence import presence, close_presence
import time
import pytest
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.telegram import Telegram, connect, settled

H = {'Authorization': 'Bearer test'}


class ReadTelegram(Telegram):
    def __init__(self):
        self.read_max = 0
        self.acks = []
        self.history = [dict(id=i, date=time.time()-60, text=f'Сообщение {i}', group=None, media=None) for i in (11, 29)]
    async def page(self, account, peer, after, since):
        return dict(messages=await self.messages(account,peer,[m['id'] for m in self.history if m['date'] >= since and m['id'] > after]), more=False)
    async def messages(self, account, peer, ids):
        return [m for m in self.history if m['id'] in ids]
    async def read_state(self, account, peer, since):
        return dict(read_max=self.read_max, initial=max([0]+[m['id'] for m in self.history if m['date'] < since]))
    async def read_window(self, account, peer, after, until):
        return dict(ids=[m['id'] for m in self.history if after < m['id'] <= until], more=False)
    async def acknowledge(self, account, peer, max_id):
        self.acks.append(max_id)
        self.read_max = max(self.read_max, max_id)


def wait_for(client, predicate):
    for _ in range(200):
        presence(client, 'test')
        status = client.get('/api/v3/status/details').json()
        if predicate(status):
            return status
        time.sleep(.02)
    raise AssertionError(status)


def test_late_view_deletes_content_but_confirmation_waits_and_survives_restart(tmp_path):
    network = ReadTelegram()
    kwargs = dict(token='test', telegram_client=network)
    with TestClient(create_app(tmp_path, **kwargs), headers=H) as client:
        presence(client, 'test')
        connect(client)
        cards = settled(client, 2)
        late = next(c for c in cards if [int(identity) for identity in client.get('/api/v3/publication/' + c['id']).json()['source']['message_ids']] == [29])['id']
        assert client.post('/api/v3/materials/' + late + '/viewed').status_code == 200
        assert client.get('/api/v3/publication/' + late).status_code == 404
        status = wait_for(client, lambda s: any(r.get('pending') == 1 and r.get('waiting') for r in s.get('telegram_reads', [])))
        assert status['telegram_reads'][0]['waiting']=='telegram_read_frontier'
        assert network.acks == []
    with TestClient(create_app(tmp_path, **kwargs), headers=H) as client:
        presence(client, 'test')
        earlier = settled(client)[0]['id']
        assert client.get('/api/v3/publication/' + late).status_code == 404
        assert client.post('/api/v3/materials/' + earlier + '/viewed').status_code == 200
        wait_for(client, lambda s: any(r.get('confirmed') == '29' and r.get('pending') == 0 for r in s.get('telegram_reads', [])))
        assert network.acks == [29]
        assert read_all_cards(client, '/api/v3/cards') == []


def test_auto_read_requires_complete_media_and_does_not_delete_originals(tmp_path):
    from oko.collection.contracts import SourceError
    class Media(ReadTelegram):
        broken = True
        async def messages(self, account, peer, ids):
            rows = await super().messages(account, peer, ids)
            return [dict(m, media=dict(kind='image', mime='image/jpeg', extension='jpg')) if m['id'] == 11 else m for m in rows]
        async def download(self, account, peer, id):
            if self.broken:
                raise SourceError('Обрыв изображения')
            return b'complete-image'
    network = Media()
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        presence(client, 'test')
        feed = connect(client)
        client.patch('/api/v3/feeds/' + feed['id'], json={'auto_read': True})
        cards = settled(client, 2)
        earlier = next(c for c in cards if [int(identity) for identity in client.get('/api/v3/publication/' + c['id']).json()['source']['message_ids']] == [11])['id']
        status = wait_for(client, lambda s: any(r.get('pending') == 1 and r.get('waiting') for r in s.get('telegram_reads', [])))
        assert network.acks == []
        assert not client.get('/api/v3/publication/' + earlier).json()['completeness']['state']=='complete'
        network.broken = False
        client.post('/api/v3/materials/' + earlier + '/retry')
        wait_for(client, lambda s: any(r.get('confirmed') == '29' for r in s.get('telegram_reads', [])))
        assert network.acks == [29]
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
        material = client.get('/api/v3/publication/' + earlier).json()
        assert material['completeness']['state']=='complete' and client.get([media for media in material['media'] if media['role'] == 'attachment'][0]['url']).content == b'complete-image'


def test_old_history_blocks_until_separate_permission_and_external_reads_do_not_delete(tmp_path):
    network = ReadTelegram()
    network.history.insert(0, dict(id=5, date=time.time()-7*86400, text='До импорта', group=None, media=None))
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        presence(client, 'test')
        feed = connect(client,count=2)
        assert feed['read_history'] is False
        cards = settled(client, 2)
        later = next(c for c in cards if [int(identity) for identity in client.get('/api/v3/publication/' + c['id']).json()['source']['message_ids']] == [29])['id']
        earlier = next(c for c in cards if [int(identity) for identity in client.get('/api/v3/publication/' + c['id']).json()['source']['message_ids']] == [11])['id']
        client.post('/api/v3/materials/' + earlier + '/viewed')
        status = wait_for(client, lambda s: any(r.get('waiting')=='telegram_read_frontier' for r in s.get('telegram_reads', [])))
        assert network.acks == []
        client.patch('/api/v3/feeds/' + feed['id'], json={'read_history': True})
        wait_for(client, lambda s: any(r.get('confirmed') == '11' for r in s.get('telegram_reads', [])))
        assert network.acks == [11]
        assert client.get('/api/v3/publication/' + later).status_code == 200
        network.read_max = 29
        client.post('/api/v3/collect')
        wait_for(client, lambda s: any(r.get('confirmed') == '29' for r in s.get('telegram_reads', [])))
        assert client.get('/api/v3/publication/' + later).status_code == 200


def test_new_read_sync_signal_survives_an_in_progress_pass(tmp_path):
    network = ReadTelegram()
    app = create_app(tmp_path, token='test', telegram_client=network)
    with TestClient(app, headers=H) as client:
        presence(client, 'test')
        feed = connect(client, count=2)['id']
        settled(client, 2)
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200

        async def check():
            async with app.state.host.execution._actions:
                store = app.state.host.require_store()
                with store.db:
                    store.read_sync.mark_feed(feed)
                store.read_sync._configuration = store.subscriptions.source_revision()
                steps = store.jobs.sync_reads_steps()
                next(steps)
                with store.db:
                    store.read_sync.mark_feed(feed)
                list(steps)
                return store.db.execute('SELECT needs_sync FROM telegram_read_state WHERE feed=%s', (feed,)).fetchone()[0]

        assert client.portal.call(check)


def test_unknown_ack_result_recovers_without_content_after_restart(tmp_path):
    network = ReadTelegram()
    network.history = network.history[:1]
    def fault(point):
        if point == 'after_telegram_acknowledge':
            raise OSError('lost acknowledgement result')
    with TestClient(create_app(tmp_path, token='test', telegram_client=network, fault=fault), headers=H) as client:
        presence(client, 'test')
        connect(client)
        item = settled(client)[0]['id']
        client.post('/api/v3/materials/' + item + '/viewed')
        wait_for(client, lambda s: bool(s['telegram_reads'] and s['telegram_reads'][0]['error']))
        assert network.acks == [11] and network.read_max == 11
        assert client.get('/api/v3/publication/' + item).status_code == 404
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        presence(client, 'test')
        wait_for(client, lambda s: any(r.get('confirmed') == '11' and not r.get('error') for r in s['telegram_reads']))
        assert network.acks == [11]
        assert client.get('/api/v3/publication/' + item).status_code == 404
        assert read_all_cards(client, '/api/v3/cards') == []


def test_ack_error_keeps_queue_and_retry_never_crosses_unviewed_message(tmp_path):
    from oko.collection.contracts import SourceError
    class Failing(ReadTelegram):
        broken = True
        async def acknowledge(self, account, peer, max_id):
            if self.broken:
                raise SourceError('Telegram временно недоступен', .1)
            await super().acknowledge(account, peer, max_id)
    network = Failing()
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        presence(client, 'test')
        connect(client)
        cards = settled(client, 2)
        earlier = next(c for c in cards if [int(identity) for identity in client.get('/api/v3/publication/' + c['id']).json()['source']['message_ids']] == [11])['id']
        later = next(c for c in cards if [int(identity) for identity in client.get('/api/v3/publication/' + c['id']).json()['source']['message_ids']] == [29])['id']
        client.post('/api/v3/materials/' + earlier + '/viewed')
        wait_for(client, lambda s: any(r.get('error') for r in s['telegram_reads']))
        assert client.get('/api/v3/publication/' + earlier).status_code == 404
        network.broken = False
        wait_for(client, lambda s: any(r.get('confirmed') == '11' for r in s['telegram_reads']))
        assert network.acks == [11]
        assert client.get('/api/v3/publication/' + later).status_code == 200


@pytest.mark.parametrize('action', ['stop', 'close', 'delete'])
def test_cancelled_history_check_cannot_send_a_late_ack(tmp_path, action):
    import asyncio
    import threading
    class Delayed(ReadTelegram):
        started = threading.Event()
        release = threading.Event()
        async def read_window(self, account, peer, after, until):
            self.started.set()
            while not self.release.is_set():
                try:
                    await asyncio.sleep(.01)
                except asyncio.CancelledError:
                    pass
            return await super().read_window(account, peer, after, until)
    network = Delayed()
    try:
        with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
            presence(client, 'test')
            feed = connect(client)
            cards = settled(client, 2)
            earlier = next(c for c in cards if [int(identity) for identity in client.get('/api/v3/publication/' + c['id']).json()['source']['message_ids']] == [11])['id']
            client.post('/api/v3/materials/' + earlier + '/viewed')
            assert network.started.wait(3)
            if action == 'stop':
                client.post('/api/v3/stop')
            elif action == 'close':
                close_presence(client, 'test')
            else:
                client.delete('/api/v3/feeds/' + feed['id'])
            network.release.set()
            time.sleep(.1)
            assert network.acks == []
    finally:
        network.release.set()


def test_disabling_auto_read_requires_view_for_saved_unconfirmed_post_after_restart(tmp_path):
    from oko.collection.contracts import SourceError
    class Partial(ReadTelegram):
        async def messages(self, account, peer, ids):
            rows = await super().messages(account, peer, ids)
            return [dict(m, media=dict(kind='image', mime='image/jpeg', extension='jpg')) if m['id'] == 11 else m for m in rows]
        async def download(self, account, peer, id):
            raise SourceError('Обрыв изображения')
    network = Partial()
    kwargs = dict(token='test', telegram_client=network)
    with TestClient(create_app(tmp_path, **kwargs), headers=H) as client:
        presence(client, 'test')
        feed = connect(client)
        client.patch('/api/v3/feeds/' + feed['id'], json={'auto_read': True})
        cards = settled(client, 2)
        later = next(c for c in cards if [int(identity) for identity in client.get('/api/v3/publication/' + c['id']).json()['source']['message_ids']] == [29])['id']
        earlier = next(c for c in cards if [int(identity) for identity in client.get('/api/v3/publication/' + c['id']).json()['source']['message_ids']] == [11])['id']
        wait_for(client, lambda s: any(r.get('pending') == 1 and r.get('waiting') for r in s['telegram_reads']))
        assert network.acks == []
        assert client.patch('/api/v3/feeds/' + feed['id'], json={'auto_read': False}).status_code == 200
    with TestClient(create_app(tmp_path, **kwargs), headers=H) as client:
        presence(client, 'test')
        client.post('/api/v3/materials/' + earlier + '/viewed')
        wait_for(client, lambda s: any(int(r.get('confirmed') or '0') >= 11 for r in s['telegram_reads']))
        assert network.acks == [11]
        assert client.get('/api/v3/publication/' + later).status_code == 200
        client.post('/api/v3/materials/' + later + '/viewed')
        wait_for(client, lambda s: any(r.get('confirmed') == '29' for r in s['telegram_reads']))
        assert network.acks == [11, 29]
