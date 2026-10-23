"""Independent collection progress through the reader HTTP interface."""
import asyncio
import threading
import time
import httpx
import pytest
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
        return dict(messages=await self.messages(account,peer,[11]),
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
                cards = client.get('/api/v3/cards').json()
                if all(any(c['feed_id']==feed and (c['availability'] == 'available') for c in cards) for feed in feeds[1:]):
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
    from tests.support.telegram_sdk import channel_sdk
    env, trace = channel_sdk(tmp_path, monkeypatch)
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
    mono, wall = [1000.], [time.time()]
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
            if telegram.calls == 1 and any((c['availability'] == 'available') for c in client.get('/api/v3/cards').json()):
                break
            time.sleep(.02)
        assert telegram.calls == 1
        assert any((c['availability'] == 'available') for c in client.get('/api/v3/cards').json())
    mono[0] = 10
    wall[0] += 30
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        time.sleep(.1)
        assert telegram.calls == 1
        mono[0] += 31
        wall[0] += 31
        deadline = time.monotonic()+2
        while telegram.calls == 1 and time.monotonic()<deadline:
            client.get('/api/v3/status')
            time.sleep(.02)
        assert telegram.calls > 1






@pytest.mark.parametrize('revalidate_first', [True, False])
def test_profile_revalidation_during_worker_start_does_not_contend_for_own_session(tmp_path, monkeypatch, revalidate_first):
    from oko.collection.telegram.source import LocalTelegram
    from tests.support.telegram_sdk import channel_sdk
    env, _ = channel_sdk(tmp_path, monkeypatch)

    async def scenario():
        client = LocalTelegram(env, tmp_path/'data')
        client.expected_users['fixture'] = 10001
        try:
            calls = [client.identity('fixture'), client.info('fixture', '@forum')]
            replies = await asyncio.gather(*(calls if revalidate_first else calls[::-1]), return_exceptions=True)
            identity, info = replies if revalidate_first else replies[::-1]
            assert identity == 10001, str(identity)
            assert isinstance(info, dict), str(info)
            assert info['peer'] == -1001234567890
        finally:
            await client.close()
    asyncio.run(scenario())
