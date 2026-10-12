from pathlib import Path
import time

from fastapi.testclient import TestClient

from oko.reader.app import create_app
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


def connect_topic(client, topic_id):
    response = client.post('/api/telegram/connect', json=dict(account='fixture', peer='@forum',
                          folder='Форумы', auto_read=False, topic_id=topic_id))
    assert response.status_code == 200, response.json()
    return response.json()


def test_topics_connect_independently_and_viewed_confirms_only_selected_topic(tmp_path: Path):
    tg = ForumTelegram()
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=tg), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        topics = client.post('/api/telegram/topics', json=dict(account='fixture', peer='@forum'))
        assert topics.status_code == 200
        assert [t['id'] for t in topics.json()] == [1, 20, 30]
        first, second = connect_topic(client, 20), connect_topic(client, 30)
        assert first['id'] != second['id']
        assert first['telegram_topic'] == 20
        assert connect_topic(client, 20)['id'] == first['id']
        assert len(client.get('/api/subscriptions').json()['feeds']) == 2
        cards = settled(client, 2)
        one = next(c for c in cards if c['feed_id'] == first['id'])
        assert one['url'] == 'https://t.me/c/1234567890/20/120'
        assert client.get('/api/materials/' + one['id']).json()['text'] == 'Тема 20'
        assert tg.confirmations == []
        assert client.post('/api/materials/' + one['id'] + '/viewed').status_code == 200
        for _ in range(100):
            if tg.confirmations:
                break
            time.sleep(.02)
        assert tg.confirmations == [(20, 120)]
        assert client.get('/api/cards').json()[0]['feed_id'] == second['id']
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=tg), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        assert settled(client)[0]['feed_id'] == second['id']
        feeds = client.get('/api/subscriptions').json()['feeds']
        assert {f['telegram_topic'] for f in feeds} == {20, 30}


def test_forum_requires_an_existing_topic_and_general_is_a_separate_feed(tmp_path: Path):
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=ForumTelegram()), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        missing = client.post('/api/telegram/connect', json=dict(account='fixture', peer='@forum', folder='Форумы', auto_read=False))
        assert missing.status_code == 400 and 'Выберите тему' in missing.json()['detail']
        assert client.post('/api/telegram/connect', json=dict(account='fixture', peer='@forum', folder='Форумы', auto_read=False, topic_id=99)).status_code == 409
        assert client.get('/api/subscriptions').json()['feeds'] == []
        feed = connect_topic(client, 1)
        assert feed['telegram_topic'] == 1
        assert settled(client)[0]['title'] == 'Тема 1'


def test_owned_worker_scopes_sdk_history_media_and_read_requests(tmp_path: Path, monkeypatch):
    import asyncio
    import json
    from oko.collection.contracts import SourceError
    from oko.collection.telegram.source import LocalTelegram
    from tests.support.telegram_forum_sdk import forum_sdk
    env, trace = forum_sdk(tmp_path, monkeypatch)
    async def check():
        client = LocalTelegram(env, tmp_path / 'data')
        try:
            assert [t['id'] for t in await client.topics('fixture', '@forum')] == [1, 20, 30]
            info = await client.info('fixture', '@forum', topic_id=20)
            assert info['forum'] and info['title'] == 'Forum · Engineering'
            page = await client.page('fixture', info['peer'], 0, 0, topic_id=20)
            assert [m['id'] for m in page['messages']] == [140, 141]
            assert [m['id'] for m in await client.messages('fixture', info['peer'], [131, 140, 142], topic_id=20)] == [140, 141]
            assert await client.download('fixture', info['peer'], 140, topic_id=20) == b'fixture-image'
            try:
                await client.download('fixture', info['peer'], 142, topic_id=20)
            except SourceError:
                pass
            else:
                raise AssertionError('foreign topic media must be rejected')
            state = await client.read_state('fixture', info['peer'], time.time(), topic_id=20)
            assert state == dict(read_max=0, initial=141)
            assert (await client.read_window('fixture', info['peer'], 0, 141, topic_id=20))['ids'] == [140, 141]
            await client.acknowledge('fixture', info['peer'], 141, topic_id=20)
            assert (await client.read_state('fixture', info['peer'], time.time(), topic_id=30))['read_max'] == 0
            assert (await client.read_state('fixture', info['peer'], time.time(), topic_id=20))['read_max'] == 141
            general = await client.page('fixture', info['peer'], 0, 0, topic_id=1)
            assert general['messages'] == [] and general['more'] and general['next_after'] == 130
            general = await client.page('fixture', info['peer'], general['next_after'], 0, topic_id=1)
            assert [m['id'] for m in general['messages']] == [131]
            assert (await client.read_window('fixture', info['peer'], 0, 131, topic_id=1))['ids'] == [131]
            await client.acknowledge('fixture', info['peer'], 131, topic_id=1)
        finally:
            await client.close()
    asyncio.run(check())
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert not any(c['op'] == 'BROAD_ACK' for c in calls)
    assert [(c['topic'], c['maximum']) for c in calls if c['op'] == 'ReadDiscussionRequest'] == [(20, 141), (1, 131)]


def test_general_empty_history_page_advances_and_album_media_survive_reader_restart(tmp_path: Path, monkeypatch):
    from oko.collection.telegram.source import LocalTelegram
    from tests.support.telegram_forum_sdk import forum_sdk
    env, _ = forum_sdk(tmp_path, monkeypatch)
    root = tmp_path / 'data'
    def app():
        return create_app(root, token='test-token', telegram_client=LocalTelegram(env, root))
    with TestClient(app(), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        general, engineering = connect_topic(client, 1), connect_topic(client, 20)
        cards = settled(client, 2)
        assert {c['feed_id'] for c in cards} == {general['id'], engineering['id']}
        album = next(c for c in cards if c['feed_id'] == engineering['id'])
        material = client.get('/api/materials/' + album['id']).json()
        assert material['members'] == [140, 141] and len(material['attachments']) == 2
        for attachment in material['attachments']:
            assert client.get(attachment['url']).content == b'fixture-image'
    with TestClient(app(), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        assert len(settled(client, 2)) == 2
        assert client.post('/api/materials/' + album['id'] + '/viewed').status_code == 200
        assert client.get('/api/cards').json()[0]['feed_id'] == general['id']
        assert client.get('/api/materials/' + album['id']).status_code == 404
        for attachment in material['attachments']:
            assert client.get(attachment['url']).status_code == 404


def test_topic_autoread_and_rebinding_do_not_affect_other_topics(tmp_path: Path):
    tg = ForumTelegram()
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=tg), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        first = connect_topic(client, 20)
        connect_topic(client, 30)
        settled(client, 2)
        bad = client.post('/api/telegram/connect', json=dict(account='fixture', peer='@forum', folder='Форумы',
                          auto_read=False, topic_id=30, feed_id=first['id']))
        assert bad.status_code == 400
        assert client.patch('/api/feeds/' + first['id'], json=dict(auto_read=True)).status_code == 200
        for _ in range(100):
            if tg.confirmations:
                break
            time.sleep(.02)
        assert tg.confirmations == [(20, 120)]
        assert len(client.get('/api/cards').json()) == 2
