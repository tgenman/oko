from pathlib import Path
import time

from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.telegram import Telegram, settled

HEADERS = {'Authorization': 'Bearer test-token'}


class ForumTelegram(Telegram):
    def __init__(self):
        self.confirmations = []

    async def topics(self, account, peer):
        return [dict(id=1, title='Общее'), dict(id=20, title='Разработка'), dict(id=30, title='Новости')]

    async def info(self, account, peer, topic_id=None):
        from oko.collection.contracts import SourceError
        if topic_id not in (None, 1, 20, 30):
            raise SourceError('Тема недоступна')
        return dict(peer=-1001234567890, title='Форум' + (f' · {topic_id}' if topic_id else ''),
                    kind='group', forum=True, username='forum', topic_id=topic_id)

    def rows(self, topic_id):
        return [dict(id=topic_id + 100, date=time.time()-60, text=f'Тема {topic_id}', group=None, media=None)]

    async def page(self, account, peer, after, since, topic_id=None):
        return dict(messages=[m for m in self.rows(topic_id) if m['id'] > after], more=False)

    async def messages(self, account, peer, ids, topic_id=None):
        return [m for m in self.rows(topic_id) if m['id'] in ids]

    async def read_state(self, account, peer, since, topic_id=None):
        return dict(read_max=0, initial=0)

    async def read_window(self, account, peer, after, until, topic_id=None):
        return dict(ids=[m['id'] for m in self.rows(topic_id) if after < m['id'] <= until], more=False)

    async def acknowledge(self, account, peer, max_id, topic_id=None):
        self.confirmations.append((topic_id, max_id))


def test_owned_worker_collects_all_topics_and_keeps_read_scopes_internal(tmp_path: Path, monkeypatch):
    import asyncio
    import json
    from oko.collection.telegram.source import LocalTelegram
    from tests.support.telegram_forum_sdk import forum_sdk
    env, trace = forum_sdk(tmp_path, monkeypatch)
    async def check():
        client = LocalTelegram(env, tmp_path/'data')
        try:
            info = await client.info('fixture', '@forum')
            first = await client.page('fixture', info['peer'], 0, 0)
            assert len(first['messages']) == 100
            second = await client.page('fixture', info['peer'], first['next_after'], 0)
            assert [m['id'] for m in second['messages']] == [131,140,141,142]
            assert await client.download('fixture', info['peer'], 140) == b'fixture-image'
            window = await client.read_window('fixture', info['peer'], 130, 142)
            assert window['topics'] == {'131':1,'140':20,'141':20,'142':30}
            await client.acknowledge('fixture', info['peer'], 131, topic_id=1)
            await client.acknowledge('fixture', info['peer'], 141, topic_id=20)
            window = await client.read_window('fixture', info['peer'], 130, 142)
            assert window['read_ids'] == [131,140,141]
        finally:
            await client.close()
    asyncio.run(check())
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert all(c['reply_to'] is None for c in calls if c['op'] == 'history')
    assert not any(c['op'] == 'BROAD_ACK' for c in calls)


def test_whole_forum_worker_keeps_general_new_deleted_topics_and_refuses_broad_ack(tmp_path, monkeypatch):
    """SDK request safety, not evidence of Telegram server acknowledgement effects."""
    import asyncio
    import json
    import pytest
    from oko.collection.contracts import SourceError
    from oko.collection.telegram.source import LocalTelegram
    from tests.support.telegram_forum_sdk import forum_sdk
    env, trace = forum_sdk(tmp_path, monkeypatch)
    monkeypatch.setenv('SDK_WHOLE_FORUM', '1')

    async def check():
        worker = LocalTelegram(env, tmp_path / 'data')
        try:
            info = await worker.info('fixture', '@forum')
            page = await worker.page('fixture', info['peer'], 130, 0)
            assert [m['id'] for m in page['messages']] == [131,140,141,142,143,144]
            window = await worker.read_window('fixture', info['peer'], 130, 144)
            assert window['topics']['143'] == 50 and window['topics']['144'] == 60
            await worker.acknowledge('fixture', info['peer'], 143, topic_id=50)
            with pytest.raises(SourceError, match='Нет доступа'):
                await worker.acknowledge('fixture', info['peer'], 144, topic_id=60)
            # A deleted topic must never widen to a broad acknowledgement.
            with pytest.raises(SourceError, match='Нет доступа'):
                await worker.acknowledge('fixture', -1001234567890, 144)
            # Topic-specific General still uses explicit ReadDiscussion, never
            # a higher global frontier that would cross unprocessed messages.
            await worker.acknowledge('fixture', -1001234567890, 131, topic_id=1)
        finally:
            await worker.close()
    asyncio.run(check())
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert not any(c['op'] == 'BROAD_ACK' for c in calls)
    assert [(c['topic'], c['maximum']) for c in calls if c['op'] == 'ReadDiscussionRequest'] == [(50,143),(1,131)]

def test_forum_is_one_binding_and_legacy_topic_creation_is_rejected(tmp_path):
    from tests.support.telegram import configure
    with TestClient(create_app(tmp_path, token='test-token', api_only=True, telegram_client=ForumTelegram()), headers=HEADERS) as client:
        configure(client)
        response = client.post('/api/v3/telegram/connect', json=dict(account='fixture', peer='@forum', folder='Forum', auto_read=False, topic_id=20))
        assert response.status_code == 422
        assert not client.get('/api/v3/subscriptions').json()['feeds']
        connected = client.post('/api/v3/telegram/connect', json=dict(source_locator='@forum', folder='Forum', access={'kind':'client','profile':'personal'}))
        assert connected.status_code == 200, connected.text
        feeds = client.get('/api/v3/subscriptions').json()['feeds']
        assert len(feeds) == 1 and feeds[0]['access']['kind'] == 'client'
        assert 'topic_id' not in feeds[0]['access']


def test_worker_checks_expected_person_before_history_media_and_ack(tmp_path, monkeypatch):
    import asyncio
    import json
    import pytest
    from oko.collection.contracts import SourceError
    from oko.collection.telegram.source import LocalTelegram
    from tests.support.telegram_forum_sdk import forum_sdk
    identity = tmp_path/'identity'
    identity.write_text('10001')
    monkeypatch.setenv('SDK_IDENTITY_FILE', str(identity))
    env, trace = forum_sdk(tmp_path, monkeypatch)
    async def check():
        worker = LocalTelegram(env, tmp_path/'data')
        try:
            assert await worker.identity('fixture') == 10001
            worker.expected_users['fixture'] = 10001
            identity.write_text('202')
            for op, params in [('page',dict(after=0,since=0)), ('download',dict(id=140)), ('acknowledge',dict(max_id=141))]:
                with pytest.raises(SourceError):
                    await worker.request(op, account='fixture', peer=-1001234567890, topic_id=20, **params)
        finally:
            await worker.close()
    asyncio.run(check())
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [c['id'] for c in calls if c['op'] == 'identity'] == [10001,202,202,202]
    assert not any(c['op'] in ('history','download','ReadDiscussionRequest','BROAD_ACK') for c in calls)
