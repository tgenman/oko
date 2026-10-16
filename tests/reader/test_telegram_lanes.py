"""Independent collection progress through the reader HTTP interface."""
import asyncio
import threading
import time
import httpx
from tests.support.telegram_web import page

from tests.support.app import create_app
from tests.support.telegram import Telegram
from tests.support.v3 import TestClient


class TwoAccounts(Telegram):
    def __init__(self):
        self.downloading = threading.Event()
        self.release = threading.Event()
        self.cancelled = threading.Event()

    async def accounts(self):
        return ['one', 'two']

    async def identity(self, account):
        return {'one': 101, 'two': 202}[account]

    async def info(self, account, peer):
        return dict(peer=-1000000000101 if account == 'one' else -1000000000202,
                    title=account, kind='channel', forum=False)

    async def history_page(self, account, peer, upper, before, since):
        return dict(messages=(await self.page(account, peer, 0, 0))['messages'],
                    upper=11, next_before=11, more=False)

    async def messages(self, account, peer, ids):
        rows = await super().messages(account, peer, ids)
        if account == 'one':
            rows[0]['media'] = dict(kind='image', extension='jpg', mime='image/jpeg')
        return rows

    async def download(self, account, peer, id):
        self.downloading.set()
        try:
            while not self.release.is_set():
                await asyncio.sleep(.01)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return b'fixture'


def test_blocked_personal_download_keeps_web_and_collection_live_until_stop(tmp_path):
    telegram = TwoAccounts()
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=telegram,
                    rss_transport=httpx.MockTransport(lambda r: httpx.Response(200,text=page([(11,'Web completed')])))),
                    headers={'Authorization': 'Bearer test'}) as client:
        for role, account in [('personal', 'one'), ('collection', 'two')]:
            assert client.put('/api/v3/telegram/profiles/'+role,
                              json={'configuration_ref': account}).status_code == 200
        feeds = []
        try:
            for role, name in [('personal', 'first'), ('collection', 'second')]:
                response = client.post('/api/v3/telegram/connect', json=dict(
                    source_locator='@'+name, folder=role,
                    access={'kind': 'client', 'profile': role}))
                assert response.status_code == 200, response.text
                feeds.append(response.json()['feed_id'])
                if role == 'personal':
                    assert telegram.downloading.wait(2)
            web = client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Web'))
            assert web.status_code == 200, web.text
            feeds.append(web.json()['feed_id'])
            assert telegram.downloading.wait(2)
            deadline = time.monotonic()+2
            while time.monotonic() < deadline:
                cards = client.get('/api/cards').json()
                if all(any(c['feed_id']==feed and c['complete'] for c in cards) for feed in feeds[1:]):
                    break
                time.sleep(.02)
            else:
                raise AssertionError('Web or collection account stalled behind personal download')
            assert not telegram.release.is_set()
            assert client.post('/api/v3/stop').status_code == 200
            assert telegram.cancelled.wait(2)
            assert client.get('/api/v3/status').json()['runtime_state'] == 'stopped'
            assert not list(tmp_path.rglob('*.jpg'))
        finally:
            telegram.release.set()


def test_subprocess_downloads_have_separate_account_pipes_and_spools(tmp_path, monkeypatch):
    from oko.collection.telegram.source import LocalTelegram
    from tests.support.telegram_forum_sdk import forum_sdk
    env, trace = forum_sdk(tmp_path, monkeypatch)
    env.write_text(env.read_text()+'TELEGRAM_SESSION_NAME_SECOND=second.session\n')
    (env.parent/'second.session').touch()
    sdk = env.parent/'sitecustomize.py'
    sdk.write_text(sdk.read_text()+'''
original_init = FakeClient.__init__
def init(self, session, *args, **kwargs):
    original_init(self, session, *args, **kwargs)
    self.fixture_user = 202 if 'second.session' in str(session) else 101
FakeClient.__init__ = init
async def identity(self):
    return type('User', (), {'id':self.fixture_user})()
FakeClient.get_me = identity
async def download(self, message, file, progress_callback):
    import asyncio
    self.log(op='download-start', user=self.fixture_user)
    if self.fixture_user == 101:
        await asyncio.sleep(30)
    file.write(b'account-two')
    return True
FakeClient.download_media = download
''')

    async def scenario():
        client = LocalTelegram(env, tmp_path/'data')
        blocked = None
        try:
            assert await client.identity('fixture') == 101
            assert await client.identity('second') == 202
            client.expected_users.update(fixture=101, second=202)
            blocked = asyncio.create_task(client.download('fixture', -1001234567890, 140))
            for _ in range(100):
                if trace.exists() and 'download-start' in trace.read_text():
                    break
                await asyncio.sleep(.01)
            else:
                raise AssertionError('first worker did not start download')
            assert await asyncio.wait_for(client.download('second', -1001234567890, 140), 2) == b'account-two'
            assert not blocked.done()
        finally:
            if blocked:
                blocked.cancel()
                try:
                    await blocked
                except asyncio.CancelledError:
                    pass
            await client.close()
        assert not list((tmp_path/'data'/'staging').rglob('media.bin'))
    asyncio.run(scenario())


class InterleavedForum(Telegram):
    retry_delay = .05

    def __init__(self):
        self.acks = []
        self.failures = 0

    async def info(self, account, peer):
        return dict(peer=-1001234567890, title='Forum', kind='group', forum=True)

    async def page(self, account, peer, after, since):
        return dict(messages=[dict(id=i, date=time.time()-60, text='Message '+str(i),
                    group=None, media=None) for i in range(11, 15) if i > after], more=False)

    async def history_page(self, account, peer, upper, before, since):
        return dict(messages=(await self.page(account, peer, 0, 0))['messages'],
                    upper=14, next_before=11, more=False)

    async def messages(self, account, peer, ids):
        return [m for m in (await self.page(account, peer, 0, 0))['messages'] if m['id'] in ids]

    async def read_window(self, account, peer, after, until):
        ids = [i for i in range(11, 15) if after < i <= until]
        return dict(ids=ids, more=False, topics={str(i): {11:1, 12:20, 13:30, 14:50}[i] for i in ids})

    async def acknowledge(self, account, peer, max_id, topic_id=None):
        from oko.collection.contracts import SourceError
        assert topic_id is not None, 'must never acknowledge the whole forum'
        if topic_id == 50 and not self.failures:
            self.failures += 1
            raise SourceError('synthetic topic retry', self.retry_delay)
        self.acks.append((topic_id, max_id))


def test_forum_frontier_never_crosses_other_topic_and_partial_success_is_not_replayed(tmp_path):
    from tests.support.telegram import settled
    telegram = InterleavedForum()
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=telegram),
                    headers={'Authorization': 'Bearer test'}) as client:
        assert client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'fixture'}).status_code == 200
        assert client.post('/api/v3/telegram/connect', json=dict(source_locator='@forum', folder='Forum',
                    access={'kind':'client','profile':'personal'})).status_code == 200
        cards = {c['members'][0]:c['id'] for c in settled(client, 4)}
        client.post('/api/materials/'+cards[14]+'/viewed')
        client.post('/api/materials/'+cards[11]+'/viewed')
        deadline = time.monotonic()+2
        while not telegram.acks and time.monotonic() < deadline:
            time.sleep(.02)
        assert telegram.acks == [(1,11)]
        client.post('/api/materials/'+cards[12]+'/viewed')
        client.post('/api/materials/'+cards[13]+'/viewed')
        deadline = time.monotonic()+2
        while (50,14) not in telegram.acks and time.monotonic() < deadline:
            time.sleep(.02)
        assert telegram.acks == [(1,11),(20,12),(30,13),(50,14)]
        assert telegram.failures == 1


def test_flood_wait_survives_a_new_monotonic_origin_and_does_not_block_other_account(tmp_path):
    from oko.collection.contracts import SourceError
    class Limited(TwoAccounts):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.release.set()

        async def history_page(self, account, peer, upper, before, since):
            if account == 'one':
                self.calls += 1
                if self.calls == 1:
                    raise SourceError.from_worker('synthetic FloodWait', {'error_type':'FloodWaitError'}, 60)
            return await super().history_page(account, peer, upper, before, since)
    telegram = Limited()
    mono, wall = [1000.], [10000.]
    options = dict(token='test', api_only=True, telegram_client=telegram,
                   clock=lambda:mono[0], wall_clock=lambda:wall[0])
    headers = {'Authorization':'Bearer test'}
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        for role, account in [('personal','one'),('collection','two')]:
            client.put('/api/v3/telegram/profiles/'+role,json={'configuration_ref':account})
            assert client.post('/api/v3/telegram/connect',json=dict(source_locator='@'+role,folder=role,
                    access={'kind':'client','profile':role})).status_code == 200
        deadline = time.monotonic()+2
        while time.monotonic()<deadline:
            if telegram.calls == 1 and any(c['complete'] for c in client.get('/api/cards').json()):
                break
            time.sleep(.02)
        assert telegram.calls == 1
        assert any(c['complete'] for c in client.get('/api/cards').json())
    mono[0] = 10
    wall[0] += 30
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        time.sleep(.1)
        assert telegram.calls == 1
        mono[0] += 31
        wall[0] += 31
        deadline = time.monotonic()+2
        while telegram.calls == 1 and time.monotonic()<deadline:
            client.get('/api/status')
            time.sleep(.02)
        assert telegram.calls > 1


def test_server_read_topics_do_not_block_viewed_posts_from_new_import(tmp_path):
    from tests.support.telegram import settled
    class AlreadyRead(InterleavedForum):
        async def read_window(self, account, peer, after, until):
            result = await super().read_window(account, peer, after, until)
            result['read_ids'] = [i for i in result['ids'] if i <= 12]
            return result
    telegram = AlreadyRead()
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=telegram),
                    headers={'Authorization':'Bearer test'}) as client:
        client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'fixture'})
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@forum',folder='Forum',
                    access={'kind':'client','profile':'personal'}, initial_history={'mode':'count','count':'2'}))
        cards = {c['members'][0]:c['id'] for c in settled(client,2)}
        assert set(cards) == {13,14}
        client.post('/api/materials/'+cards[13]+'/viewed')
        deadline = time.monotonic()+2
        while not telegram.acks and time.monotonic()<deadline:
            time.sleep(.02)
        assert telegram.acks == [(30,13)]
        assert not telegram.failures


def test_partial_forum_ack_survives_restart_without_replaying_successful_topics(tmp_path):
    from tests.support.telegram import settled
    telegram = InterleavedForum()
    telegram.retry_delay = 60
    offset = [0]
    options = dict(token='test',api_only=True,telegram_client=telegram,
                   clock=lambda:time.monotonic()+offset[0])
    headers = {'Authorization':'Bearer test'}
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'fixture'})
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@forum',folder='Forum',
                    access={'kind':'client','profile':'personal'}))
        for card in settled(client,4):
            client.post('/api/materials/'+card['id']+'/viewed')
        deadline = time.monotonic()+2
        while not telegram.failures and time.monotonic()<deadline:
            time.sleep(.02)
        assert telegram.failures == 1
        assert telegram.acks == [(1,11),(20,12),(30,13)]
    offset[0] = 61
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        deadline = time.monotonic()+2
        while (50,14) not in telegram.acks and time.monotonic()<deadline:
            client.get('/api/status')
            time.sleep(.02)
        assert telegram.acks == [(1,11),(20,12),(30,13),(50,14)]
