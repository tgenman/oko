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
        return dict(messages=[m for m in self.history if m['date'] >= since and m['id'] > after], more=False)
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
        client.post('/api/tabs/test/heartbeat')
        status = client.get('/api/status').json()
        if predicate(status):
            return status
        time.sleep(.02)
    raise AssertionError(status)


def test_late_view_deletes_content_but_confirmation_waits_and_survives_restart(tmp_path):
    network = ReadTelegram()
    kwargs = dict(token='test', telegram_client=network)
    with TestClient(create_app(tmp_path, **kwargs), headers=H) as client:
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        cards = settled(client, 2)
        late = next(c for c in cards if c['members'] == [29])['id']
        assert client.post('/api/materials/' + late + '/viewed').status_code == 200
        assert client.get('/api/materials/' + late).status_code == 404
        status = wait_for(client, lambda s: any(r.get('pending') == 1 and r.get('waiting') for r in s.get('telegram_reads', [])))
        assert 'раннее' in status['telegram_reads'][0]['waiting']
        assert network.acks == []
    with TestClient(create_app(tmp_path, **kwargs), headers=H) as client:
        client.post('/api/tabs/test/heartbeat')
        earlier = settled(client)[0]['id']
        assert client.get('/api/materials/' + late).status_code == 404
        assert client.post('/api/materials/' + earlier + '/viewed').status_code == 200
        wait_for(client, lambda s: any(r.get('confirmed') == 29 and r.get('pending') == 0 for r in s.get('telegram_reads', [])))
        assert network.acks == [29]
        assert client.get('/api/cards').json() == []


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
        client.post('/api/tabs/test/heartbeat')
        feed = connect(client)
        client.patch('/api/feeds/' + feed['id'], json={'auto_read': True})
        cards = settled(client, 2)
        earlier = next(c for c in cards if c['members'] == [11])['id']
        status = wait_for(client, lambda s: any(r.get('pending') == 1 and r.get('waiting') for r in s.get('telegram_reads', [])))
        assert network.acks == []
        assert not client.get('/api/materials/' + earlier).json()['full']
        network.broken = False
        client.post('/api/materials/' + earlier + '/retry')
        wait_for(client, lambda s: any(r.get('confirmed') == 29 for r in s.get('telegram_reads', [])))
        assert network.acks == [29]
        assert len(client.get('/api/cards').json()) == 2
        material = client.get('/api/materials/' + earlier).json()
        assert material['full'] and client.get(material['attachments'][0]['url']).content == b'complete-image'


def test_old_history_blocks_until_separate_permission_and_external_reads_do_not_delete(tmp_path):
    network = ReadTelegram()
    network.history.insert(0, dict(id=5, date=time.time()-7*86400, text='До импорта', group=None, media=None))
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        client.post('/api/tabs/test/heartbeat')
        feed = connect(client)
        assert feed['read_history'] is False
        cards = settled(client, 2)
        later = next(c for c in cards if c['members'] == [29])['id']
        earlier = next(c for c in cards if c['members'] == [11])['id']
        client.post('/api/materials/' + earlier + '/viewed')
        status = wait_for(client, lambda s: any('Старая' in r.get('waiting', '') for r in s.get('telegram_reads', [])))
        assert network.acks == []
        client.patch('/api/feeds/' + feed['id'], json={'read_history': True})
        wait_for(client, lambda s: any(r.get('confirmed') == 11 for r in s.get('telegram_reads', [])))
        assert network.acks == [11]
        assert client.get('/api/materials/' + later).status_code == 200
        network.read_max = 29
        client.post('/api/collect')
        wait_for(client, lambda s: any(r.get('confirmed') == 29 for r in s.get('telegram_reads', [])))
        assert client.get('/api/materials/' + later).status_code == 200


def test_unknown_ack_result_recovers_without_content_after_restart(tmp_path):
    network = ReadTelegram()
    network.history = network.history[:1]
    def fault(point):
        if point == 'after_telegram_acknowledge':
            raise OSError('lost acknowledgement result')
    with TestClient(create_app(tmp_path, token='test', telegram_client=network, fault=fault), headers=H) as client:
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        item = settled(client)[0]['id']
        client.post('/api/materials/' + item + '/viewed')
        wait_for(client, lambda s: bool(s['telegram_reads'] and s['telegram_reads'][0]['error']))
        assert network.acks == [11] and network.read_max == 11
        assert client.get('/api/materials/' + item).status_code == 404
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        client.post('/api/tabs/test/heartbeat')
        wait_for(client, lambda s: any(r.get('confirmed') == 11 and not r.get('error') for r in s['telegram_reads']))
        assert network.acks == [11]
        assert client.get('/api/materials/' + item).status_code == 404
        assert client.get('/api/cards').json() == []


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
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        cards = settled(client, 2)
        earlier = next(c for c in cards if c['members'] == [11])['id']
        later = next(c for c in cards if c['members'] == [29])['id']
        client.post('/api/materials/' + earlier + '/viewed')
        wait_for(client, lambda s: any(r.get('error') for r in s['telegram_reads']))
        assert client.get('/api/materials/' + earlier).status_code == 404
        network.broken = False
        wait_for(client, lambda s: any(r.get('confirmed') == 11 for r in s['telegram_reads']))
        assert network.acks == [11]
        assert client.get('/api/materials/' + later).status_code == 200


def test_folder_completion_confirms_unique_message_set(tmp_path):
    network = ReadTelegram()
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        client.post('/api/tabs/test/heartbeat')
        client.patch('/api/settings/oko/global', json={})
        feed = connect(client)
        settled(client, 2)
        preview = client.post('/api/folders/' + feed['folder_id'] + '/completion/preview').json()
        assert preview['originals'] == 2
        response = client.post('/api/folders/' + feed['folder_id'] + '/completion', json={'token': preview['token']})
        assert response.status_code == 200
        wait_for(client, lambda s: any(r.get('confirmed') == 29 for r in s['telegram_reads']))
        assert network.acks == [29]
        assert client.get('/api/cards').json() == []


@pytest.mark.parametrize('action', ['stop', 'close', 'transport'])
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
            client.post('/api/tabs/test/heartbeat')
            feed = connect(client)
            cards = settled(client, 2)
            earlier = next(c for c in cards if c['members'] == [11])['id']
            client.post('/api/materials/' + earlier + '/viewed')
            assert network.started.wait(3)
            if action == 'stop':
                client.post('/api/stop')
            elif action == 'close':
                client.post('/api/tabs/test/close')
            else:
                client.patch('/api/feeds/' + feed['id'], json={'transport': 'rss'})
            network.release.set()
            time.sleep(.1)
            assert network.acks == []
    finally:
        network.release.set()


def test_legacy_viewed_tombstone_retains_right_to_confirm(tmp_path):
    import sqlite3
    network = ReadTelegram()
    kwargs = dict(token='test', telegram_client=network)
    with TestClient(create_app(tmp_path, **kwargs), headers=H) as client:
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        cards = settled(client, 2)
        later = next(c for c in cards if c['members'] == [29])['id']
        client.post('/api/materials/' + later + '/viewed')
    # Fixture models a store created before the read queue existed.
    with sqlite3.connect(tmp_path / 'state.sqlite') as db:
        db.executescript("DROP TABLE telegram_read_members; DROP TABLE telegram_read_state; DELETE FROM jobs WHERE kind='read';")
    with TestClient(create_app(tmp_path, **kwargs), headers=H) as client:
        client.post('/api/tabs/test/heartbeat')
        status = wait_for(client, lambda s: any(r.get('pending') == 1 and r.get('waiting') for r in s['telegram_reads']))
        assert network.acks == []
        assert client.get('/api/materials/' + later).status_code == 404
        earlier = settled(client)[0]['id']
        client.post('/api/materials/' + earlier + '/viewed')
        wait_for(client, lambda s: any(r.get('confirmed') == 29 for r in s['telegram_reads']))
        assert network.acks == [29]


def test_large_initial_history_is_confirmed_in_verified_pages(tmp_path):
    class Paged(ReadTelegram):
        async def read_window(self, account, peer, after, until):
            ids = [m['id'] for m in self.history if after < m['id'] <= until]
            return dict(ids=ids[:100], more=len(ids) >= 100)
    network = Paged()
    network.history = [dict(id=i, date=time.time()-7*86400, text='Старая история', group=None, media=None) for i in range(1,102)]
    network.history.append(dict(id=999, date=time.time()-60, text='Новая публикация', group=None, media=None))
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        client.post('/api/tabs/test/heartbeat')
        feed = connect(client)
        settled(client)
        client.patch('/api/feeds/' + feed['id'], json={'auto_read': True, 'read_history': True})
        wait_for(client, lambda s: any(r.get('confirmed') == 999 for r in s['telegram_reads']))
        assert network.acks == [100, 999]
        assert len(client.get('/api/cards').json()) == 1


def test_deleted_remote_message_does_not_leave_confirmation_waiting_forever(tmp_path):
    network = ReadTelegram()
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        cards = settled(client, 2)
        later = next(c for c in cards if c['members'] == [29])['id']
        earlier = next(c for c in cards if c['members'] == [11])['id']
        network.history = network.history[:1]
        assert client.get('/api/materials/' + later).status_code == 200
        client.post('/api/materials/' + later + '/viewed')
        wait_for(client, lambda s: any(r.get('pending') == 0 and not r.get('waiting') for r in s['telegram_reads']))
        assert network.acks == []
        assert client.get('/api/materials/' + earlier).status_code == 200
        client.post('/api/materials/' + earlier + '/viewed')
        wait_for(client, lambda s: any(r.get('confirmed') == 11 for r in s['telegram_reads']))
        assert network.acks == [11]


def test_complete_early_post_is_auto_confirmed_before_loading_later_post(tmp_path):
    import asyncio
    import threading
    class SlowLater(ReadTelegram):
        started = threading.Event()
        release = threading.Event()
        async def messages(self, account, peer, ids):
            if ids == [29]:
                self.started.set()
                while not self.release.is_set():
                    await asyncio.sleep(.01)
            return await super().messages(account, peer, ids)
    network = SlowLater()
    try:
        with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
            client.post('/api/tabs/test/heartbeat')
            response = client.post('/api/telegram/connect', json=dict(account='fixture', peer='@fixture', folder='Новости', auto_read=True))
            assert response.status_code == 200
            assert network.started.wait(3)
            assert network.acks == [11]
            network.release.set()
            wait_for(client, lambda s: any(r.get('confirmed') == 29 for r in s['telegram_reads']))
            assert network.acks == [11, 29]
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
        client.post('/api/tabs/test/heartbeat')
        feed = connect(client)
        client.patch('/api/feeds/' + feed['id'], json={'auto_read': True})
        cards = settled(client, 2)
        later = next(c for c in cards if c['members'] == [29])['id']
        earlier = next(c for c in cards if c['members'] == [11])['id']
        wait_for(client, lambda s: any(r.get('pending') == 1 and r.get('waiting') for r in s['telegram_reads']))
        assert network.acks == []
        assert client.patch('/api/feeds/' + feed['id'], json={'auto_read': False}).status_code == 200
    with TestClient(create_app(tmp_path, **kwargs), headers=H) as client:
        client.post('/api/tabs/test/heartbeat')
        client.post('/api/materials/' + earlier + '/viewed')
        wait_for(client, lambda s: any(r.get('confirmed') >= 11 for r in s['telegram_reads']))
        assert network.acks == [11]
        assert client.get('/api/materials/' + later).status_code == 200
        client.post('/api/materials/' + later + '/viewed')
        wait_for(client, lambda s: any(r.get('confirmed') == 29 for r in s['telegram_reads']))
        assert network.acks == [11, 29]
