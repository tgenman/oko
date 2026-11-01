"""Telegram profile/binding/claim lifecycle through the existing HTTP seam."""
from tests.support.cards import read_all_cards
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
        card = settled(client)[0]
        material = client.get('/api/v3/publication/' + card['id']).json()
        assert material['source']['message_ids'] == ['392']
        collected_calls = len(calls)
        assert collected_calls >= 1
        assert client.post('/api/v3/telegram/connect', json={'source_locator':'@TELEGRAM', 'folder':'Other'}).status_code == 409
        assert len(calls) == collected_calls
        state = client.get('/api/v3/subscriptions').json()['feeds'][0]
        assert state['id'] == feed and 'auto_read' not in state
        assert client.patch('/api/v3/feeds/'+feed, json={'auto_read':False}).status_code == 400
        assert client.get('/api/v3/telegram/profiles').json()['configurations'] == []


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
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=Late()), headers=HEADERS) as client:
        try:
            client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'one'})
            body = dict(source_locator='@fixture',folder='Inbox',access={'kind':'client','profile':'personal'})
            old_feed = client.post('/api/v3/telegram/connect', json=body).json()['feed_id']
            assert started.wait(2)
            old_material = read_all_cards(client, '/api/v3/cards')[0]['id']
            assert client.delete('/api/v3/feeds/'+old_feed).status_code == 200
            release.set()
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
            assert client.get('/api/v3/publication/'+old_material).status_code == 404
            assert not (tmp_path/'content'/old_material).exists()
            assert not (tmp_path/'staging'/old_material).exists()
        finally:
            release.set()
