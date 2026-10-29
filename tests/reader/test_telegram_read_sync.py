from tests.support.cards import read_all_cards
from tests.support.storage import database
from datetime import date, timedelta
from tests.support.presence import presence, close_presence
from tests.support.telegram import connect_request
import time
import threading
import pytest
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.telegram import Telegram, connect, settled

H = {'Authorization': 'Bearer test'}


def test_runtime_auto_read_batches_eligibility_and_keeps_http_responsive(tmp_path):
    network = ReadTelegram()
    network.history = [dict(id=i, date=time.time()-60, text=f'Synthetic {i}', group=None, media=None)
                       for i in range(11,43)]
    app = create_app(tmp_path, token='test', api_only=True, telegram_client=network)
    with TestClient(app, headers=H) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses':False,'prompt_filters':''})
        feed = connect(client, count=32)
        settled(client, 32)
        assert client.put('/api/v3/power', json={'enabled':False}).status_code == 200
        queries = []
        started = threading.Event()
        def trace(query):
            queries.append(query)
            if query.startswith('SELECT member FROM telegram_read_members') and ' AND member IN (' in query:
                started.set()
                time.sleep(.08)
        client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(trace))
        try:
            before = time.monotonic()
            assert client.patch('/api/v3/feeds/'+feed['id'], json={'auto_read':True}).status_code == 200
            assert time.monotonic()-before < 1
            assert started.wait(2)
            before = time.monotonic()
            assert client.get('/api/v3/cards').status_code == 200
            assert time.monotonic()-before < 1
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                if any(row['pending'] == 32 for row in client.get('/api/v3/status/details').json()['telegram_reads']):
                    break
                time.sleep(.02)
            assert any(row['pending'] == 32 for row in client.get('/api/v3/status/details').json()['telegram_reads'])
        finally:
            client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(None))
        member_points = [q for q in queries if any(q.startswith(f'SELECT members FROM {table} WHERE id=') for table in ('items', 'items_read', 'items_by_feed', 'unviewed_items_by_feed'))]
        eligibility_batches = [q for q in queries if q.startswith('SELECT member FROM telegram_read_members') and ' AND member IN (' in q]
        assert member_points == []
        assert 1 <= len(eligibility_batches) <= 8, len(eligibility_batches)
        assert network.acks == []


def test_postgres_runtime_auto_read_frontier_follows_latest_setting(postgres_url, tmp_path):
    network = ReadTelegram()
    network.history = [dict(id=i, date=time.time()-60, text=f'Synthetic {i}', group=None, media=None)
                       for i in range(11,43)]
    with TestClient(create_app(tmp_path, token='test', api_only=True, database_url=postgres_url,
                    telegram_client=network), headers=H) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses':False,'prompt_filters':''})
        assert client.put('/api/v3/power', json={'enabled':True}).status_code == 200
        feed = connect(client, count=32)
        settled(client, 32)
        assert client.put('/api/v3/power', json={'enabled':False}).status_code == 200
        assert client.patch('/api/v3/feeds/'+feed['id'], json={'auto_read':True}).status_code == 200
        wait_for(client, lambda status: any(row['pending'] == 32 for row in status['telegram_reads']))
        assert client.patch('/api/v3/feeds/'+feed['id'], json={'auto_read':False}).status_code == 200
        wait_for(client, lambda status: all(row['pending'] == 0 for row in status['telegram_reads']))
        assert network.acks == []


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


def test_reobserving_unchanged_page_schedules_read_frontier_check(tmp_path):
    network = ReadTelegram()
    app = create_app(tmp_path, token='test', telegram_client=network)
    with TestClient(app, headers=H) as client:
        presence(client, 'test')
        feed = connect(client,count=2)['id']
        settled(client, 2)
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200

        def observe():
            store = app.state.host.require_store()
            with store.db:
                cursor = store.db.execute('SELECT cursor FROM feeds WHERE id = %s', (feed,)).fetchone()[0]
                store.db.execute('UPDATE telegram_read_state SET needs_sync=false WHERE feed = %s', (feed,))
                store.read_sync.observed(feed, (), cursor)
                return store.db.execute('SELECT needs_sync FROM telegram_read_state WHERE feed = %s', (feed,)).fetchone()[0]

        assert client.portal.call(observe)


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


def test_saved_and_viewed_are_independent_read_reasons(tmp_path):
    network = ReadTelegram()
    app = create_app(tmp_path, token='test', telegram_client=network)
    with TestClient(app, headers=H) as client:
        presence(client, 'test')
        feed = connect(client, count=2)['id']
        settled(client, 2)
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        assert client.patch('/api/v3/feeds/'+feed, json={'auto_read':False}).status_code == 200

        async def check():
            async with app.state.host.execution._actions:
                store = app.state.host.require_store()
                with store.db:
                    store.read_sync.eligible(feed, [11], 'viewed')
                    store.read_sync.eligible(feed, [11], 'saved')
                row = store.db.execute('SELECT saved,viewed FROM telegram_read_members WHERE feed=%s AND member=11', (feed,)).fetchone()
                request = store.read_sync.request(feed)
                return tuple(row), request['eligible'] if request else None

        reasons, eligible = client.portal.call(check)
        assert reasons == (True, True)
        assert 11 in eligible


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


def test_folder_completion_confirms_unique_message_set(tmp_path):
    network = ReadTelegram()
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        presence(client, 'test')
        client.patch('/api/v3/settings/oko/global', json={})
        feed = connect(client)
        settled(client, 2)
        preview = client.post('/api/v3/folders/' + feed['folder_id'] + '/completion/preview').json()
        assert preview['publications_count'] == 2
        response = client.post('/api/v3/folders/' + feed['folder_id'] + '/completion', json={'token': preview['token']})
        assert response.status_code == 200
        wait_for(client, lambda s: any(r.get('confirmed') == '29' for r in s['telegram_reads']))
        assert network.acks == [29]
        assert read_all_cards(client, '/api/v3/cards') == []


def test_feed_completion_uses_existing_safe_telegram_frontier(tmp_path):
    network = ReadTelegram()
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        presence(client, 'test')
        client.patch('/api/v3/settings/oko/global', json={})
        feed = connect(client)
        settled(client, 2)
        preview = client.post('/api/v3/feeds/' + feed['id'] + '/completion/preview').json()
        assert preview['publications_count'] == 2
        response = client.post('/api/v3/feeds/' + feed['id'] + '/completion', json={'token': preview['token']})
        assert response.status_code == 200
        wait_for(client, lambda s: any(r.get('confirmed') == '29' for r in s['telegram_reads']))
        assert network.acks == [29]
        assert read_all_cards(client, '/api/v3/cards') == []


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




def test_large_initial_history_is_confirmed_in_verified_pages(tmp_path):
    class Paged(ReadTelegram):
        async def read_window(self, account, peer, after, until):
            ids = [m['id'] for m in self.history if after < m['id'] <= until]
            return dict(ids=ids[:100], more=len(ids) >= 100)
    network = Paged()
    network.history = [dict(id=i, date=time.time()-7*86400, text='Старая история', group=None, media=None) for i in range(1,102)]
    network.history.append(dict(id=999, date=time.time()-60, text='Новая публикация', group=None, media=None))
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        presence(client, 'test')
        feed = connect(client,count=1)
        settled(client)
        client.patch('/api/v3/feeds/' + feed['id'], json={'auto_read': True, 'read_history': True})
        wait_for(client, lambda s: any(r.get('confirmed') == '999' for r in s['telegram_reads']))
        assert network.acks == [100, 999]
        assert len(read_all_cards(client, '/api/v3/cards')) == 1


def test_deleted_remote_message_does_not_leave_confirmation_waiting_forever(tmp_path):
    network = ReadTelegram()
    with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
        presence(client, 'test')
        connect(client)
        cards = settled(client, 2)
        later = next(c for c in cards if [int(identity) for identity in client.get('/api/v3/publication/' + c['id']).json()['source']['message_ids']] == [29])['id']
        earlier = next(c for c in cards if [int(identity) for identity in client.get('/api/v3/publication/' + c['id']).json()['source']['message_ids']] == [11])['id']
        network.history = network.history[:1]
        assert client.get('/api/v3/publication/' + later).status_code == 200
        client.post('/api/v3/materials/' + later + '/viewed')
        wait_for(client, lambda s: any(r.get('pending') == 0 and not r.get('waiting') for r in s['telegram_reads']))
        assert network.acks == []
        assert client.get('/api/v3/publication/' + earlier).status_code == 200
        client.post('/api/v3/materials/' + earlier + '/viewed')
        wait_for(client, lambda s: any(r.get('confirmed') == '11' for r in s['telegram_reads']))
        assert network.acks == [11]


def test_complete_early_post_is_auto_confirmed_before_loading_later_post(tmp_path):
    import asyncio
    import threading
    class SlowLater(ReadTelegram):
        started = threading.Event()
        release = threading.Event()
        async def messages(self, account, peer, ids):
            rows=await super().messages(account,peer,ids)
            return [dict(row,media=dict(kind='image',mime='image/jpeg',extension='jpg')) if row['id']==29 else row for row in rows]
        async def download(self, account, peer, id):
            assert id==29
            self.started.set()
            while not self.release.is_set():
                await asyncio.sleep(.01)
            return b'late-image'
    network = SlowLater()
    later = network.history.pop()
    try:
        with TestClient(create_app(tmp_path, token='test', telegram_client=network), headers=H) as client:
            presence(client, 'test')
            response = connect_request(client, json=dict(account='fixture', peer='@fixture', folder='Новости', auto_read=True, initial_history={'mode':'count','count':1}))
            assert response.status_code == 200
            wait_for(client, lambda state: network.acks == [11])
            network.history.append(later)
            assert client.post('/api/v3/collect').status_code == 200
            assert network.started.wait(3)
            assert network.acks == [11]
            network.release.set()
            wait_for(client, lambda s: any(r.get('confirmed') == '29' for r in s['telegram_reads']))
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
