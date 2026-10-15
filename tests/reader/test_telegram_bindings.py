"""Telegram profile/binding/claim lifecycle through the existing HTTP seam."""
from tests.support.app import create_app
from tests.support.telegram import Telegram
from tests.support.v3 import TestClient

HEADERS = {'Authorization': 'Bearer test'}


class Accounts(Telegram):
    users = {'one': 9007199254740993, 'two': 202}

    def __init__(self):
        self.users = dict(type(self).users)
        self.acks = []

    async def acknowledge(self, account, peer, max_id):
        self.acks.append(max_id)

    async def history_page(self, account, peer, upper, before, since):
        page = await self.page(account, peer, 0, since)
        return dict(messages=page['messages'], upper=11, next_before=11, more=False)

    async def accounts(self):
        return list(self.users)

    async def identity(self, account):
        return self.users[account]


def test_profiles_require_verified_distinct_people_and_same_person_can_reauthorize(tmp_path):
    tg = Accounts()
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=tg), headers=HEADERS) as client:
        state = client.get('/api/v3/telegram/profiles')
        assert state.status_code == 200
        assert state.json()['personal']['state'] == 'unconfigured'
        saved = client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref': 'one'})
        assert saved.status_code == 200
        assert saved.json()['verified_user_id'] == '9007199254740993'
        again = client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref': 'one'})
        assert again.status_code == 200
        conflict = client.put('/api/v3/telegram/profiles/collection', json={'configuration_ref': 'one'})
        assert conflict.status_code == 409
        assert conflict.json()['code'] == 'profile_account_conflict'
        assert client.put('/api/v3/telegram/profiles/collection', json={'configuration_ref': 'two'}).status_code == 200


def test_client_claims_are_global_and_access_is_immutable(tmp_path):
    tg = Accounts()
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=tg), headers=HEADERS) as client:
        assert client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref': 'one'}).status_code == 200
        assert client.put('/api/v3/telegram/profiles/collection', json={'configuration_ref': 'two'}).status_code == 200
        body = dict(source_locator='https://t.me/s/Fixture/11', folder='First', access={'kind': 'client', 'profile': 'personal'})
        result = client.post('/api/v3/telegram/connect', json=body)
        assert result.status_code == 200, result.text
        feed = result.json()['feed_id']
        duplicate = client.post('/api/v3/telegram/connect', json={**body, 'folder': 'Other', 'access': {'kind': 'client', 'profile': 'collection'}})
        assert duplicate.status_code == 409
        assert duplicate.json()['details']['existing_feeds'][0]['feed_id'] == feed
        catalog = client.get('/api/v3/subscriptions').json()['feeds']
        assert len(catalog) == 1 and catalog[0]['access']['kind'] == 'client'
        assert catalog[0]['access']['account_user_id'] == '9007199254740993'
        assert client.patch('/api/v3/feeds/'+feed, json={'access': {'kind': 'web'}}).json()['code'] == 'access_immutable'
        occupied = client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref': 'two'})
        assert occupied.status_code == 409 and occupied.json()['code'] == 'profile_in_use'


def test_delete_reserves_claim_until_restart_cleanup_then_readd_has_fresh_id(tmp_path):
    import time
    from tests.support.telegram import settled
    tg = Accounts()
    blocked = True
    def fault(point):
        if point == 'before_delete' and blocked:
            raise OSError('synthetic disk unavailable')
    body = dict(source_locator='@fixture', folder='Inbox', access={'kind': 'client', 'profile': 'personal'})
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=tg, fault=fault), headers=HEADERS) as client:
        client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref': 'one'})
        feed = client.post('/api/v3/telegram/connect', json=body).json()['feed_id']
        cards = settled(client)
        material = cards[0]['id']
        assert client.delete('/api/v3/feeds/'+feed).json() == {'feed_id': feed, 'state': 'deleting'}
        assert not client.get('/api/cards').json()
        assert client.get('/api/materials/'+material).status_code == 404
        duplicate = client.post('/api/v3/telegram/connect', json=body)
        assert duplicate.status_code == 409
        assert duplicate.json()['details']['existing_feeds'][0]['feed_id'] == feed
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['connection'] == 'deleting'
        assert not tg.acks
    blocked = False
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=tg), headers=HEADERS) as client:
        for _ in range(100):
            if not client.get('/api/v3/subscriptions').json()['feeds']:
                break
            time.sleep(.02)
        assert not client.get('/api/v3/subscriptions').json()['feeds']
        assert not (tmp_path/'content'/material).exists()
        added = client.post('/api/v3/telegram/connect', json=body)
        assert added.status_code == 200, added.text
        assert added.json()['feed_id'] != feed
        assert client.get('/api/materials/'+material).status_code == 404
        assert not tg.acks


def test_session_substitution_blocks_collection_and_same_person_recovers(tmp_path):
    tg = Accounts()
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=tg), headers=HEADERS) as client:
        client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref': 'one'})
        tg.users['one'] = 303
        body = dict(source_locator='@fixture', folder='Inbox', access={'kind':'client','profile':'personal'})
        response = client.post('/api/v3/telegram/connect', json=body)
        assert response.status_code == 409 and response.json()['code'] == 'account_identity_mismatch'
        assert client.get('/api/v3/telegram/profiles').json()['personal']['state'] == 'identity_mismatch'
        assert not client.get('/api/v3/subscriptions').json()['feeds']
        tg.users['one'] = 9007199254740993
        assert client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'one'}).status_code == 200
        assert client.post('/api/v3/telegram/connect', json=body).status_code == 200


def legacy_feeds(root, *, topics=False):
    """A pre-upgrade database: helpers never run an account or migrate a user."""
    from oko.storage.store import Store
    from oko.collection.telegram.source import ConnectTelegram
    from oko.collection.contracts import Material, Publication
    store = Store(root, lambda _: None)
    result = []
    for account in ('one', 'two', 'three'):
        feed = store.subscriptions.connect_telegram(ConnectTelegram(account=account, peer='@shared', folder='Old', auto_read=False, topic_id=20 if topics else None),
            dict(peer=-1001234567890, username='shared', title=account))
        result.append(feed['id'])
        store.enqueue_discovery(feed['id'])
        job = store.next_job(0, feeds=[feed['id']])
        store.discovered([Publication('11', 'https://t.me/shared/11', 1., Material('Saved', account))], '{"since":0,"after":11}', job, feed['id'], 'telegram')
    store.db.close()
    return result


def test_legacy_aliases_all_claim_owners_and_saved_material_survive_until_explicit_mapping(tmp_path):
    ids = legacy_feeds(tmp_path)
    tg = Accounts()
    tg.users['three'] = 333
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=tg), headers=HEADERS) as client:
        feeds = client.get('/api/v3/subscriptions').json()['feeds']
        assert [feed['id'] for feed in feeds] == ids
        assert [feed['access']['alias'] for feed in feeds] == ['one', 'two', 'three']
        assert all(feed['connection'] == 'needs_profile_confirmation' for feed in feeds)
        assert {client.get('/api/materials/'+card['id']).json()['text'] for card in client.get('/api/cards').json()} == {'one','two','three'}
        duplicate = client.post('/api/v3/telegram/connect', json={'source_locator':'https://telegram.me/s/SHARED/11', 'folder':'New'})
        assert duplicate.status_code == 409
        assert {f['feed_id'] for f in duplicate.json()['details']['existing_feeds']} == set(ids)
        client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'one'})
        feeds = client.get('/api/v3/subscriptions').json()['feeds']
        assert feeds[0]['id'] == ids[0] and feeds[0]['access']['kind'] == 'client'
        assert {f['access']['alias'] for f in feeds[1:]} == {'two','three'}
        assert len(client.get('/api/v3/telegram/profiles').json()['configurations']) == 3


def test_unexpected_topic_migration_refuses_before_database_changes(tmp_path):
    import hashlib
    legacy_feeds(tmp_path, topics=True)
    path = tmp_path/'state.sqlite'
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=Accounts()), headers=HEADERS) as client:
        error = client.get('/api/status').json()['error']
        assert 'миграция фидов отдельных тем' in error
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_web_binding_needs_no_account_and_duplicates_are_normalized(tmp_path):
    import httpx
    from pathlib import Path
    raw = (Path(__file__).resolve().parents[1]/'fixtures/telegram/public.html').read_bytes()
    calls = []
    def reply(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=raw)
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        response = client.post('/api/v3/telegram/connect', json={'source_locator':'https://t.me/s/Telegram/392', 'folder':'Web'})
        assert response.status_code == 200, response.text
        feed = response.json()['feed_id']
        assert response.json()['access'] == {'kind':'web', 'username':'telegram', 'preview_url':'https://t.me/s/telegram'}
        assert len(calls) == 1
        assert client.post('/api/v3/telegram/connect', json={'source_locator':'@TELEGRAM', 'folder':'Other'}).status_code == 409
        assert len(calls) == 1
        state = client.get('/api/v3/subscriptions').json()['feeds'][0]
        assert state['id'] == feed and 'auto_read' not in state
        assert client.patch('/api/v3/feeds/'+feed, json={'auto_read':False}).status_code == 400
        assert client.get('/api/v3/telegram/profiles').json()['configurations'] == []


def test_concurrent_connect_has_one_winner_without_updating_folder(tmp_path):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    both, release = threading.Event(), threading.Event()
    class Delayed(Accounts):
        calls = 0
        async def info(self, account, peer):
            self.calls += 1
            if self.calls >= 2:
                both.set()
            while not release.is_set():
                await asyncio.sleep(.005)
            return await super().info(account, peer)
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=Delayed()), headers=HEADERS) as client:
        client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'one'})
        with ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(client.post, '/api/v3/telegram/connect', json=dict(source_locator=source, folder=folder, access={'kind':'client','profile':'personal'}))
                    for source,folder in [('@fixture','First'),('https://t.me/s/FIXTURE/11','Second')]]
            try:
                assert both.wait(2)
            finally:
                release.set()
            replies = [job.result(3) for job in jobs]
        assert sorted(r.status_code for r in replies) == [200,409]
        winner = next(r.json()['feed_id'] for r in replies if r.status_code == 200)
        assert len(client.get('/api/v3/subscriptions').json()['feeds']) == 1
        assert next(r for r in replies if r.status_code == 409).json()['details']['existing_feeds'][0]['feed_id'] == winner


def test_replacing_profile_rejects_late_connect_metadata(tmp_path):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    started, release = threading.Event(), threading.Event()
    class Delayed(Accounts):
        async def info(self, account, peer):
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(.005)
                except asyncio.CancelledError:
                    pass
            return await super().info(account, peer)
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=Delayed()), headers=HEADERS) as client:
        client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'one'})
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(client.post, '/api/v3/telegram/connect', json=dict(source_locator='@fixture',folder='Inbox',access={'kind':'client','profile':'personal'}))
            try:
                assert started.wait(2)
                replaced = client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'two'})
                assert replaced.status_code == 200
            finally:
                release.set()
            assert pending.result(3).status_code in (409,503)
        assert not client.get('/api/v3/subscriptions').json()['feeds']


def test_used_profile_detects_external_replacement_and_recovers_same_person(tmp_path):
    import time
    from tests.support.telegram import settled
    class Observed(Accounts):
        reads = []
        async def page(self, account, peer, after, since):
            self.reads.append(account)
            return await super().page(account, peer, after, since)
    tg = Observed()
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=tg), headers=HEADERS) as client:
        client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'one'})
        feed = client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture',folder='Inbox',access={'kind':'client','profile':'personal'})).json()['feed_id']
        material = settled(client)[0]['id']
        tg.users['one'] = 303
        before = len(tg.reads)
        client.post('/api/collect')
        for _ in range(100):
            if client.get('/api/v3/telegram/profiles').json()['personal']['state'] == 'identity_mismatch': break
            time.sleep(.02)
        assert client.get('/api/v3/telegram/profiles').json()['personal']['state'] == 'identity_mismatch'
        assert len(tg.reads) == before and not tg.acks
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['connection'] == 'unavailable'
        assert client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'one'}).json()['code'] == 'profile_in_use'
        assert client.get('/api/materials/'+material).status_code == 200
        tg.users['restored'] = 9007199254740993
        assert client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'restored'}).status_code == 200
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['id'] == feed
        assert settled(client)[0]['id'] == material


def test_deleted_feed_rejects_late_media_even_after_readd(tmp_path):
    import asyncio
    import threading
    import time
    started, release = threading.Event(), threading.Event()
    class Late(Accounts):
        async def page(self, account, peer, after, since):
            page = await super().page(account, peer, after, since)
            page['messages'][0]['media'] = dict(kind='video',mime='video/mp4',extension='mp4')
            return page
        async def messages(self, account, peer, ids):
            return (await self.page(account,peer,0,0))['messages']
        async def download(self, account, peer, id):
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(.005)
                except asyncio.CancelledError:
                    pass
            return b'late video'
    try:
        with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=Late()), headers=HEADERS) as client:
            client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'one'})
            body = dict(source_locator='@fixture',folder='Inbox',access={'kind':'client','profile':'personal'})
            old_feed = client.post('/api/v3/telegram/connect', json=body).json()['feed_id']
            assert started.wait(2)
            old_material = client.get('/api/cards').json()[0]['id']
            assert client.delete('/api/v3/feeds/'+old_feed).status_code == 200
            for _ in range(100):
                if not client.get('/api/v3/subscriptions').json()['feeds']: break
                time.sleep(.02)
            new = client.post('/api/v3/telegram/connect', json=body)
            assert new.status_code == 200, new.text
            assert new.json()['feed_id'] != old_feed
            release.set()
            from tests.support.telegram import settled
            card = settled(client)[0]
            assert card['feed_id'] == new.json()['feed_id'] and card['id'] != old_material
            assert client.get('/api/materials/'+old_material).status_code == 404
            assert not (tmp_path/'content'/old_material).exists()
            assert not (tmp_path/'staging'/old_material).exists()
    finally:
        release.set()
