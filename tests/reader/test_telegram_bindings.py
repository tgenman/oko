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
        assert not client.get('/api/v3/cards').json()
        assert client.get('/api/v3/materials/'+material).status_code == 404
        duplicate = client.post('/api/v3/telegram/connect', json=body)
        assert duplicate.status_code == 409
        assert duplicate.json()['details']['existing_feeds'][0]['feed_id'] == feed
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['connection'] == 'deleting'
        status = client.get('/api/v3/status')
        assert status.status_code == 200
        assert any(work['phase'] == 'deleting' for work in status.json()['work'])
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
        assert client.get('/api/v3/materials/'+material).status_code == 404
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


def legacy_feeds(root, *, topics=False, accounts=('one', 'two', 'three')):
    """A pre-upgrade database: helpers never run an account or migrate a user."""
    from oko.storage.store import Store
    from oko.subscriptions.subscriptions import key
    from oko.collection.contracts import Material, Publication
    store = Store(root, lambda _: None)
    result = []
    for account in accounts:
        identity = f'telegram-peer:{account}:-1001234567890' + (':topic:20' if topics else '')
        feed = dict(id=key(identity))
        with store.db:
            store.db.execute('INSERT OR IGNORE INTO folders VALUES (?,?,0)', (key('folder:Old'),'Old'))
            store.db.execute("""INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport,
                telegram_peer,telegram_account,telegram_username,telegram_topic,auto_read)
                VALUES (?,?,?,'telegram',?,?,'connected','telegram',?,?,?,?,0)""",
                (feed['id'], identity, account, key('folder:Old'), '["@shared"]', -1001234567890, account, 'shared', 20 if topics else None))
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
        assert {client.get('/api/v3/materials/'+card['id']).json()['content']['text'] for card in client.get('/api/v3/cards').json()} == {'one','two','three'}
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
        state = client.get('/api/v3/status').json()
        assert state['runtime_state'] == 'unavailable'
        assert state['problems']['current'][0]['code'] == 'storage_unavailable'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_web_binding_needs_no_account_and_duplicates_are_normalized(tmp_path):
    import httpx
    from tests.support.telegram import settled
    from tests.support.telegram_web import page
    raw = page([(392, 'Public post')]).replace('fixture', 'telegram').encode()
    calls = []
    def reply(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=raw)
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        response = client.post('/api/v3/telegram/connect', json={'source_locator':'https://t.me/s/Telegram/392', 'folder':'Web'})
        assert response.status_code == 200, response.text
        feed = response.json()['feed_id']
        assert response.json()['access'] == {'kind':'web', 'username':'telegram', 'preview_url':'https://t.me/s/telegram'}
        assert settled(client)[0]['source']['message_ids'] == ['392']
        collected_calls = len(calls)
        assert collected_calls >= 1
        assert client.post('/api/v3/telegram/connect', json={'source_locator':'@TELEGRAM', 'folder':'Other'}).status_code == 409
        assert len(calls) == collected_calls
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
        client.post('/api/v3/collect')
        for _ in range(100):
            if client.get('/api/v3/telegram/profiles').json()['personal']['state'] == 'identity_mismatch': break
            time.sleep(.02)
        assert client.get('/api/v3/telegram/profiles').json()['personal']['state'] == 'identity_mismatch'
        assert len(tg.reads) == before and not tg.acks
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['connection'] == 'unavailable'
        assert client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'one'}).json()['code'] == 'profile_in_use'
        assert client.get('/api/v3/materials/'+material).status_code == 200
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
            old_material = client.get('/api/v3/cards').json()[0]['id']
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
            assert client.get('/api/v3/materials/'+old_material).status_code == 404
            assert not (tmp_path/'content'/old_material).exists()
            assert not (tmp_path/'staging'/old_material).exists()
    finally:
        release.set()


def test_legacy_adoption_cannot_replace_unknown_original_person_with_another_profile(tmp_path):
    feed = legacy_feeds(tmp_path, accounts=('one',))[0]
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=Accounts()), headers=HEADERS) as client:
        before = client.get('/api/v3/subscriptions').json()
        assert client.put('/api/v3/telegram/profiles/collection', json={'configuration_ref':'two'}).status_code == 200
        response = client.post('/api/v3/telegram/connect', json=dict(source_locator='-1001234567890',access={'kind':'client','profile':'collection'},folder='Other',auto_read=False))
        assert response.status_code == 409
        assert client.get('/api/v3/subscriptions').json() == before
        material = client.get('/api/v3/cards').json()[0]['id']
        assert client.get('/api/v3/materials/'+material).json()['content']['text'] == 'one'


def test_confirmed_alias_cannot_be_reassigned_to_another_peer(tmp_path):
    class Aliases(Accounts):
        async def info(self, account, peer):
            first = peer == '@first'
            return dict(peer=-1001234567890 if first else -1009999999999, title='First' if first else 'Second',
                kind='channel',forum=False,username='first' if first else 'second',usernames=['shared'])
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=Aliases()), headers=HEADERS) as client:
        client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'one'})
        body = dict(source_locator='@first',folder='Inbox',access={'kind':'client','profile':'personal'})
        first = client.post('/api/v3/telegram/connect', json=body).json()['feed_id']
        response = client.post('/api/v3/telegram/connect', json={**body, 'source_locator':'@second'})
        assert response.status_code == 409 and response.json()['code'] == 'source_identity_conflict'
        assert response.json()['details']['feed_id'] == first
        feeds = client.get('/api/v3/subscriptions').json()['feeds']
        assert len(feeds) == 1 and feeds[0]['id'] == first and feeds[0]['title'] == 'First'


def test_failed_or_redirected_preview_never_creates_a_binding(tmp_path):
    import httpx
    from pathlib import Path
    landing = (Path(__file__).resolve().parents[1]/'fixtures/telegram/unavailable.html').read_bytes()
    responses = [httpx.Response(200, content=landing), httpx.Response(302, headers={'location':'https://example.test/private'})]
    calls = []
    def reply(request):
        calls.append(str(request.url))
        return responses.pop(0)
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        for _ in range(2):
            response = client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture',folder='Web'))
            assert response.status_code == 503 and response.json()['code'] == 'source_unavailable'
            assert not client.get('/api/v3/subscriptions').json()['feeds']
    assert len(calls) == 2 and all(url.startswith('https://t.me/') for url in calls)



def test_late_discovery_cannot_complete_a_recreated_feed_job(tmp_path):
    import asyncio
    import threading
    import time
    import httpx
    from email.utils import formatdate
    from tests.support.feed_url import verify, add
    old_started, new_started = threading.Event(), threading.Event()
    old_release, new_release = threading.Event(), threading.Event()
    calls = 0
    stamp = formatdate(time.time(), usegmt=True)
    async def reply(request):
        nonlocal calls
        if request.url.path == '/feed.xml':
            calls += 1
            count = calls
            if count in (2,4):
                (old_started if count == 2 else new_started).set()
                event = old_release if count == 2 else new_release
                deadline = time.monotonic() + 3
                while not event.is_set() and time.monotonic() < deadline:
                    try:
                        await asyncio.sleep(.005)
                    except asyncio.CancelledError:
                        pass
                if not event.is_set():
                    raise TimeoutError('synthetic wait expired')
            title = 'Late old' if count == 2 else 'Current'
            return httpx.Response(200, text=f'<rss><channel><title>RSS</title><item><guid>1</guid><link>https://example.org/article</link><title>{title}</title><pubDate>{stamp}</pubDate></item></channel></rss>')
        return httpx.Response(200, text='<article>Current text</article>')
    try:
        with TestClient(create_app(tmp_path, token='test', api_only=True, rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
            first = add(client, verify(client), initial_history={'mode':'count','count':1}).json()['id']
            assert old_started.wait(2)
            assert client.delete('/api/v3/feeds/'+first).status_code == 200
            for _ in range(100):
                if not client.get('/api/v3/subscriptions').json()['feeds']: break
                time.sleep(.02)
            add(client, verify(client), initial_history={'mode':'count','count':1})
            assert new_started.wait(2)
            old_release.set()
            time.sleep(.1)
            assert not client.get('/api/v3/cards').json()
            new_release.set()
            from tests.support.telegram import settled
            assert settled(client)[0]['title'] == 'Current'
    finally:
        old_release.set()
        new_release.set()


def test_stopped_pending_connect_cannot_commit_a_late_binding(tmp_path):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    started, release = threading.Event(), threading.Event()
    class LateMetadata(Accounts):
        async def info(self, account, peer):
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(.005)
                except asyncio.CancelledError:
                    pass
            return await super().info(account, peer)
    with TestClient(create_app(tmp_path,token='test',api_only=True,telegram_client=LateMetadata()),headers=HEADERS) as client:
        assert client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'one'}).status_code==200
        with ThreadPoolExecutor(1) as pool:
            connecting=pool.submit(client.post,'/api/v3/telegram/connect',json=dict(source_locator='@late',folder='Late',access={'kind':'client','profile':'personal'}))
            try:
                assert started.wait(2)
                assert client.post('/api/v3/stop').status_code==200
            finally:
                release.set()
            result=connecting.result(3)
        assert result.status_code==409,result.text
        assert client.get('/api/v3/status').json()['runtime_state']=='stopped'
    with TestClient(create_app(tmp_path,token='test',api_only=True,telegram_client=Accounts()),headers=HEADERS) as client:
        assert client.get('/api/v3/subscriptions').json()['feeds']==[]
