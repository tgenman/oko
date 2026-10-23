from tests.support.storage import database
from tests.support.reader import wait_for
from tests.support.telegram import connected_feed
from tests.support.presence import presence, close_presence
from tests.support.telegram import connect_request
from tests.support.subscriptions import saved_sources
from tests.support.telegram import Telegram, connect, settled
from pathlib import Path
import pytest
import time
from fastapi.testclient import TestClient
from tests.support.app import create_app

HEADERS = {'Authorization': 'Bearer test-token'}


def test_public_channel_connects_reads_and_viewed_does_not_resurrect(tmp_path: Path):
    kwargs = dict(token='test-token', telegram_client=Telegram())
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        assert [row['configuration_ref'] for row in client.get('/api/v3/telegram/profiles').json()['configurations']] == ['fixture']
        feed = connect(client)
        assert feed['kind'] == 'telegram' and feed['connection'] == 'ready'
        card = settled(client)[0]
        material = client.get('/api/v3/materials/' + card['id']).json()
        assert material['content']['text'] == 'Текст канала' and (material['content']['completeness']['state']=='complete') is True
        assert material['source_url'] == 'https://t.me/c/1234567890/11'
        client.post('/api/v3/materials/' + card['id'] + '/viewed')
        assert client.get('/api/v3/materials/' + card['id']).status_code == 404
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        time.sleep(.15)
        assert client.get('/api/v3/cards').json() == []


def test_album_partial_media_survives_restart_retry_and_deletes_all_parts(tmp_path: Path):
    from oko.collection.contracts import SourceError
    class Album(Telegram):
        available = False
        rows = [dict(id=21, date=time.time()-60, text='Подпись альбома', group=777, media=dict(kind='image', mime='image/jpeg', extension='jpg')),
                dict(id=22, date=time.time()-60, text='', group=777, media=dict(kind='video', mime='video/mp4', extension='mp4')),
                dict(id=23, date=time.time()-60, text='', group=777, media=dict(kind='circle', mime='video/mp4', extension='mp4'))]
        async def page(self, account, peer, after, since):
            return dict(messages=self.rows if after == 0 else [], more=False, gap=None)
        async def messages(self, account, peer, ids):
            return self.rows
        async def download(self, account, peer, id):
            if id == 22 and not self.available:
                raise SourceError('Обрыв загрузки Telegram')
            return {21: b'jpeg-image', 22: b'mp4-video', 23: b'mp4-circle'}[id]
    tg = Album()
    kwargs = dict(token='test-token', telegram_client=tg)
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        connect(client)
        card = settled(client)[0]
        item = card['id']
        material = client.get('/api/v3/materials/' + item).json()
        assert (material['content']['completeness']['state']=='complete') is False
        assert [int(identity) for identity in material['source']['message_ids']] == [21, 22, 23]
        assert len(material['content']['attachments']) == 2
        assert {a['kind'] for a in material['content']['attachments']} == {'image', 'circle'}
        assert 'Обрыв' in material['content']['completeness'].get('message','')
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        material = client.get('/api/v3/materials/' + item).json()
        assert len(material['content']['attachments']) == 2 and not (material['content']['completeness']['state']=='complete')
        tg.available = True
        client.post('/api/v3/materials/' + item + '/retry')
        card = settled(client)[0]
        assert (card['completeness']['state']=='complete') is True
        assert len(card['attachments']) == 3
        for attachment in card['attachments']:
            response = client.get(attachment['url'])
            assert response.status_code == 200
            assert response.content in (b'jpeg-image', b'mp4-video', b'mp4-circle')
        client.post('/api/v3/materials/' + item + '/viewed')
        assert all(client.get(a['url']).status_code == 404 for a in card['attachments'])
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()


def test_paged_history_resumes_committed_position_and_errors_do_not_block_other_feeds(tmp_path: Path):
    from oko.collection.contracts import SourceError
    class Pages(Telegram):
        async def info(self, account, peer):
            return dict(peer=peer if isinstance(peer,int) else -1001234567891 if peer == '@denied' else -1001234567890, title=str(peer), kind='group', forum=False)
        async def page(self, account, peer, after, since):
            if peer == -1001234567891:
                raise SourceError('Нет доступа к Telegram-источнику', 120)
            id = 101 if after < 101 else 205 if after < 205 else None
            return dict(messages=[dict(id=id, date=time.time()-60, text=f'Сообщение {id}', group=None, media=None)] if id else [], more=id == 101, gap='История могла иметь пробел' if after == 101 else None)
        async def messages(self, account, peer, ids):
            return [dict(id=id, date=time.time()-60, text=f'Сообщение {id}', group=None, media=None) for id in ids]
    tg = Pages()
    kwargs = dict(token='test-token', telegram_client=tg)
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        connect(client, '@denied')
        connect(client)
        cards = settled(client, 2)
        assert {c['title'] for c in cards} == {'Сообщение 101', 'Сообщение 205'}
        assert any(h['history']['state']!='absent' and h['history']['progress']['warnings'] for h in client.get('/api/v3/status').json()['initial_histories'])
        assert any(p['message']=='Нет доступа к Telegram-источнику' for p in client.get('/api/v3/status').json()['problems']['current'])
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        assert len(settled(client, 2)) == 2
        client.post('/api/v3/collect')
        time.sleep(.15)
        assert len(client.get('/api/v3/cards').json()) == 2


def test_private_group_access_forum_and_account_errors_are_explicit(tmp_path: Path):
    from oko.collection.contracts import SourceError
    class Peers(Telegram):
        async def info(self, account, peer):
            if peer == '@denied':
                raise SourceError('Нет доступа к Telegram-источнику')
            return dict(peer=-12345, title='Приватная группа', kind='group', forum=peer == '@forum')
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=Peers()), headers=HEADERS) as client:
        presence(client, 'test')
        assert connect_request(client, json=dict(account='fixture', peer='@denied', folder='Личное', auto_read=False)).json()['message'] == 'Нет доступа к Telegram-источнику'
        assert connect_request(client, json=dict(account='fixture', peer='@forum', topic_id=1, folder='Личное', auto_read=False)).status_code == 422
        assert client.get('/api/v3/subscriptions').json()['feeds'] == []
        feed = connect(client, '-12345', 'Личное')
        assert feed['access']['peer_id'] == '-12345'
        assert len(client.get('/api/v3/subscriptions').json()['feeds']) == 1
        duplicate = connect_request(client, json=dict(account='fixture', peer='-12345', folder='Другое', auto_read=False))
        assert duplicate.status_code == 409
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['folder_id'] == feed['folder_id']
        card = settled(client)[0]
        assert card['source_url'] == 'tg://openmessage?chat_id=12345&message_id=11'


def test_late_video_after_viewed_cannot_publish_or_leave_files(tmp_path: Path):
    import asyncio
    import threading
    downloading, released = threading.Event(), threading.Event()
    class Video(Telegram):
        rows = [dict(id=11, date=time.time()-60, text='Кружочек', group=None, media=dict(kind='circle', mime='video/mp4', extension='mp4'))]
        async def page(self, account, peer, after, since):
            return dict(messages=self.rows, more=False)
        async def messages(self, account, peer, ids):
            return self.rows
        async def download(self, account, peer, id):
            downloading.set()
            while not released.is_set():
                try:
                    await asyncio.sleep(.01)
                except asyncio.CancelledError:
                    continue
            return b'late-video'
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=Video()), headers=HEADERS) as client:
        presence(client, 'test')
        connect(client)
        assert downloading.wait(2)
        item = client.get('/api/v3/cards').json()[0]['id']
        assert client.post('/api/v3/materials/' + item + '/viewed').status_code == 200
        released.set()
        time.sleep(.1)
        assert client.get('/api/v3/materials/' + item).status_code == 404
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()


def test_imported_telegram_feed_requires_explicit_removal_before_client_connection(tmp_path: Path):
    saved_sources(tmp_path, [('https://rsshub.test/telegram/channel/fixture', 'Канал', 'Новости')])
    class Imported(Telegram):
        async def info(self, account, peer):
            return dict(peer=-1001234567890, title='Канал', kind='channel', forum=False, username='fixture')
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=Imported()), headers=HEADERS) as client:
        imported = client.get('/api/v3/subscriptions').json()['feeds'][0]
        presence(client, 'test')
        rejected=connect_request(client,json=dict(account='fixture',peer='@fixture',folder='Новости',auto_read=False))
        assert rejected.status_code==409 and rejected.json()['code']=='duplicate_source'
        assert client.get('/api/v3/subscriptions').json()['feeds']==[imported]
        assert client.delete('/api/v3/feeds/'+imported['id']).status_code==200
        wait_for(client,lambda: client.get('/api/v3/subscriptions').json()['feeds']==[])
        feed = connect(client)
        assert feed['id'] != imported['id']
        assert len(client.get('/api/v3/subscriptions').json()['feeds']) == 1
        client.patch('/api/v3/feeds/' + feed['id'], json={'auto_read': True, 'read_history': True})
        card = settled(client)[0]
        assert (card['completeness']['state']=='complete')
        client.post('/api/v3/materials/' + card['id'] + '/viewed')
        assert client.get('/api/v3/cards').json() == []


def test_discovery_interruption_keeps_album_identity_and_files_recover(tmp_path: Path):
    failed = False
    def fault(point):
        nonlocal failed
        if point == 'before_discovery_commit' and not failed:
            failed = True
            raise OSError('simulated interruption')
    kwargs = dict(token='test-token', telegram_client=Telegram())
    with TestClient(create_app(tmp_path, fault=fault, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        connect(client)
        for _ in range(100):
            if failed:
                break
            time.sleep(.02)
        assert failed
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        card = settled(client)[0]
        assert (card['completeness']['state']=='complete') and card['title'] == 'Текст канала'
        assert len(client.get('/api/v3/cards').json()) == 1


def fake_sdk_project(tmp_path: Path, monkeypatch):
    """Real Oko worker and configuration, fake external Telethon SDK only."""
    import os
    project = tmp_path / 'sdk-fixture'
    (project / 'telethon').mkdir(parents=True)
    (project / '.env').write_text('TELEGRAM_API_ID=123\nTELEGRAM_API_HASH=synthetic\nTELEGRAM_SESSION_STRING_FIXTURE=synthetic-session\n')
    (project / 'telethon' / 'sessions.py').write_text("class StringSession:\n    def __init__(self, value): self.value = value\n    def save(self): return self.value\n")
    (project / 'telethon' / 'errors.py').write_text('class FloodWaitError(Exception): pass\n')
    (project / 'telethon' / '__init__.py').write_text('''import asyncio
import os
from datetime import datetime, timezone
from types import SimpleNamespace
class Channel:
    id = 1234567890
    title = 'Synthetic channel'
    broadcast = True
    forum = False
    username = 'fixture'
class Chat: pass
types = SimpleNamespace(Channel=Channel, Chat=Chat)
utils = SimpleNamespace(get_peer_id=lambda entity: -1001234567890)
class TelegramClient:
    def __init__(self, session, *args, **kwargs): self.session = session
    async def connect(self): pass
    async def disconnect(self): pass
    async def get_me(self): return type('User', (), {'id': 10001})()
    async def is_user_authorized(self): return os.environ.get('SDK_FIXTURE_UNAUTHORIZED') != 'yes'
    async def get_entity(self, peer): return Channel()
    async def get_messages(self, entity, **params):
        def msg(id):
            return SimpleNamespace(id=id, date=datetime.now(timezone.utc), message='Album' if id == 11 else '', grouped_id=99, photo=True, document=None, media=True, file=SimpleNamespace(size=100))
        if isinstance(params.get('ids'), int): return msg(params['ids'])
        if 'ids' in params: return [msg(id) for id in params['ids']]
        if params.get('limit') == 10:
            return [msg(12)] if params.get('reverse') else []
        return [msg(11)] if not params.get('offset_id') else []
    async def download_media(self, msg, file, progress_callback):
        file.write(b'partial-download')
        file.flush()
        await asyncio.sleep(100)
''')
    monkeypatch.setenv('PYTHONPATH', str(project) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    return project


def test_worker_pipe_reads_album_pages_and_terminates_mid_download(tmp_path: Path, monkeypatch):
    from oko.collection.telegram.source import LocalTelegram
    project = fake_sdk_project(tmp_path, monkeypatch)
    root = tmp_path / 'data'
    bridge = LocalTelegram(project / '.env', root)
    with TestClient(create_app(root, token='test-token', telegram_client=bridge), headers=HEADERS) as client:
        presence(client, 'test')
        connect(client)
        for _ in range(100):
            partial = root / 'staging' / '_telegram' / '10001' / 'media.bin'
            if partial.exists() and partial.stat().st_size:
                break
            time.sleep(.02)
        assert partial.read_bytes() == b'partial-download'
        card = client.get('/api/v3/cards').json()[0]
        assert [int(identity) for identity in card['source']['message_ids']] == [11, 12] and card['title'] == 'Album'
        downloading = bridge.workers[10001].process
        client.post('/api/v3/materials/' + card['id'] + '/viewed')
        assert downloading is not None and downloading.returncode is not None
        # A read acknowledgement may start a new worker, but the interrupted
        # download and its bytes must already be gone when viewed returns.
        assert not partial.exists()
        assert not (root / 'content' / card['id']).exists()
        assert client.get('/api/v3/cards').json() == []


def test_legacy_rss_content_survives_and_cannot_switch_to_client(tmp_path: Path):
    saved_sources(tmp_path, [('https://source.test/telegram/channel/fixture', 'Источник', 'Новости')])
    import sqlite3
    with database(tmp_path) as db:
        db.execute("UPDATE feeds SET transport='rss'")
    import httpx
    from datetime import datetime, timezone
    from email.utils import format_datetime
    class Imported(Telegram):
        async def info(self, account, peer):
            return dict(peer=-1001234567890, title='Канал', kind='channel', forum=False, username='fixture')
    date = format_datetime(datetime.now(timezone.utc))
    xml = f'<rss><channel><item><guid>rss-11</guid><link>https://source.test/old</link><title>RSS-текст</title><description>Анонс</description><pubDate>{date}</pubDate></item></channel></rss>'
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=xml if '/telegram/' in req.url.path else '<article>Статья</article>'))
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=Imported(), rss_enabled=True, rss_transport=transport), headers=HEADERS) as client:
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]
        assert client.patch('/api/v3/feeds/' + feed['id'], json={'transport': 'telegram'}).status_code == 409
        presence(client, 'test')
        settled(client)
        response = connect_request(client, json=dict(account='fixture', peer='@fixture', folder='New', auto_read=False))
        assert response.status_code == 409
        cards = settled(client, 1)
        assert {c['title'] for c in cards} == {'RSS-текст'}
        catalog=client.get('/api/v3/subscriptions').json()['feeds'][0]
        access=catalog['access']
        assert access['kind']=='unconfigured' and catalog['connection']=='needs_connection'
        assert response.json()['details']['existing_feeds'][0]['access']==access
        composition=client.get('/api/v3/reading-composition').json()['feeds'][0]['feed']
        assert composition['access']==access
        assert cards[0]['source']['access']==access
        assert client.get('/api/v3/materials/'+cards[0]['id']).json()['source']['access']==access
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['access']['kind'] == 'unconfigured'


def test_media_file_commit_interruption_recovers_without_duplicate(tmp_path: Path):
    failed = False
    class Photo(Telegram):
        async def page(self, account, peer, after, since):
            return dict(messages=[dict(id=11, date=time.time()-60, text='Фото', group=None, media=dict(kind='image', mime='image/jpeg', extension='jpg'))], more=False)
        async def messages(self, account, peer, ids):
            return (await self.page(account, peer, 0, 0))['messages']
    def fault(point):
        nonlocal failed
        if point == 'after_file_move' and not failed:
            failed = True
            raise OSError('simulated interruption')
    kwargs = dict(token='test-token', telegram_client=Photo())
    with TestClient(create_app(tmp_path, fault=fault, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        connect(client)
        for _ in range(100):
            if failed:
                break
            time.sleep(.02)
        assert failed
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        card = settled(client)[0]
        assert (card['completeness']['state']=='complete') and len(card['attachments']) == 1
        assert client.get(card['attachments'][0]['url']).content == b'media'
        client.post('/api/v3/materials/' + card['id'] + '/viewed')
        assert not (tmp_path / 'content' / card['id']).exists()


def test_new_album_part_updates_existing_material_and_member_ids(tmp_path: Path):
    class Growing(Telegram):
        rows = [dict(id=11, date=time.time()-60, text='Альбом', group=777, media=dict(kind='image', mime='image/jpeg', extension='jpg'))]
        async def page(self, account, peer, after, since):
            return dict(messages=self.rows, more=False)
        async def messages(self, account, peer, ids):
            return self.rows
    tg = Growing()
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=tg), headers=HEADERS) as client:
        presence(client, 'test')
        connect(client)
        original = settled(client)[0]
        tg.rows = tg.rows + [dict(id=12, date=time.time()-60, text='', group=777, media=dict(kind='video', mime='video/mp4', extension='mp4'))]
        client.post('/api/v3/materials/' + original['id'] + '/retry')
        for _ in range(150):
            material = client.get('/api/v3/materials/' + original['id']).json()
            if not material['content'].get('loading') and len(material['content'].get('attachments', [])) == 2:
                break
            time.sleep(.02)
        assert len(client.get('/api/v3/cards').json()) == 1
        assert [int(identity) for identity in material['source']['message_ids']] == [11, 12]
        assert (material['content']['completeness']['state']=='complete') is True and len(material['content']['attachments']) == 2


def test_viewed_album_stays_deleted_when_first_part_is_removed(tmp_path: Path):
    class Album(Telegram):
        rows = [dict(id=11, date=time.time()-60, text='Альбом', group=777, media=None), dict(id=12, date=time.time()-60, text='', group=777, media=None)]
        async def page(self, account, peer, after, since):
            return dict(messages=self.rows, more=False)
        async def messages(self, account, peer, ids):
            return self.rows
    tg = Album()
    kwargs = dict(token='test-token', telegram_client=tg)
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        connect(client)
        card = settled(client)[0]
        client.post('/api/v3/materials/' + card['id'] + '/viewed')
    tg.rows = [dict(id=13, date=time.time()-60, text='Поздняя часть', group=777, media=None)]
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        time.sleep(.15)
        assert client.get('/api/v3/cards').json() == []


def test_local_client_uses_env_without_external_project_or_venv(tmp_path: Path):
    from oko.collection.telegram.source import LocalTelegram
    # Account listing parses configuration without contacting Telegram or copying it.
    env = tmp_path / 'credentials.env'
    env.write_text('TELEGRAM_API_ID=123\nTELEGRAM_API_HASH=synthetic\nTELEGRAM_SESSION_STRING_FIXTURE=synthetic-session\n')
    root = tmp_path / 'data'
    with TestClient(create_app(root, token='test-token', telegram_client=LocalTelegram(env, root)), headers=HEADERS) as client:
        presence(client, 'test')
        result = client.get('/api/v3/telegram/profiles')
        assert result.status_code == 200, result.json()
        assert [row['configuration_ref'] for row in result.json()['configurations']] == ['fixture']
        assert not list(root.rglob('*.session')) and not list(root.rglob('*.env'))


def test_direct_client_never_logs_in_or_copies_an_unauthorized_session(tmp_path: Path, monkeypatch):
    from oko.collection.telegram.source import LocalTelegram
    project = fake_sdk_project(tmp_path, monkeypatch)
    monkeypatch.setenv('SDK_FIXTURE_UNAUTHORIZED', 'yes')
    root = tmp_path / 'data'
    with TestClient(create_app(root, token='test-token', telegram_client=LocalTelegram(project / '.env', root)), headers=HEADERS) as client:
        presence(client, 'test')
        response = client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'fixture'})
        assert response.status_code == 503
        assert response.json()['code'] == 'profile_unavailable'
        assert client.get('/api/v3/subscriptions').json()['feeds'] == []
        assert not list(root.rglob('*.session')) and not list(root.rglob('*.env'))


def test_direct_client_prefers_existing_string_session_to_stale_file_setting(tmp_path: Path, monkeypatch):
    from oko.collection.telegram.source import LocalTelegram
    project = fake_sdk_project(tmp_path, monkeypatch)
    (project / '.env').write_text('TELEGRAM_API_ID=123\nTELEGRAM_API_HASH=synthetic\nTELEGRAM_SESSION_NAME=missing-old-file\nTELEGRAM_SESSION_STRING=synthetic-session\n')
    root = tmp_path / 'data'
    with TestClient(create_app(root, token='test-token', telegram_client=LocalTelegram(project / '.env', root)), headers=HEADERS) as client:
        presence(client, 'test')
        response = connect_request(client, json=dict(account='default', peer='@fixture', folder='Новости', auto_read=False))
        assert response.status_code == 200, response.json()
        assert response.json()['access']['kind']=='client'
        assert response.json()['access']['account_user_id']=='10001'
        assert client.get('/api/v3/telegram/profiles').json()['personal']['configuration_ref']=='default'
        assert not list(project.rglob('*.session'))


def test_public_peer_is_resolved_again_after_restart_without_session_entity_cache(tmp_path: Path):
    from oko.collection.contracts import SourceError
    class ColdSession(Telegram):
        resolved = False
        text = 'До перезапуска'
        async def info(self, account, peer):
            if peer not in ('fixture', '@fixture'):
                raise SourceError('Нет доступа к Telegram-источнику')
            self.resolved = True
            return dict(peer=-1001234567890, title='Канал', kind='channel', forum=False, username='fixture')
        async def page(self, account, peer, after, since):
            if not self.resolved:
                raise SourceError('Нет доступа к Telegram-источнику')
            return await super().page(account, peer, after, since)
        async def messages(self, account, peer, ids):
            if not self.resolved:
                raise SourceError('Нет доступа к Telegram-источнику')
            return [dict(id=11, date=time.time()-60, text=self.text, group=None, media=None)]
        async def close(self):
            self.resolved = False
    original = ColdSession()
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=original), headers=HEADERS) as client:
        presence(client, 'test')
        connect(client)
        item = settled(client)[0]['id']
    # Simulate the user's existing database from before username persistence.
    import sqlite3
    with sqlite3.connect(tmp_path / 'state.sqlite') as database:
        # The old schema predates current feed triggers as well as this column.
        for trigger in database.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='feeds'").fetchall():
            database.execute('DROP TRIGGER "' + trigger[0].replace('"', '""') + '"')
        database.execute('ALTER TABLE feeds DROP COLUMN telegram_username')
    def interrupt_migration(point):
        if point == 'before_telegram_username_commit':
            raise OSError('simulated migration interruption')
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=ColdSession(), fault=interrupt_migration), headers=HEADERS) as client:
        assert client.get('/api/v3/status').json()['runtime_state']=='unavailable'
        assert client.get('/api/v3/cards').status_code==503
    restarted = ColdSession()
    restarted.text = 'После перезапуска'
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=restarted), headers=HEADERS) as client:
        presence(client, 'test')
        assert client.post('/api/v3/materials/' + item + '/retry').status_code == 200
        for _ in range(100):
            material = client.get('/api/v3/materials/' + item).json()
            if material['content'].get('text') == 'После перезапуска' and not material['content'].get('loading'):
                break
            time.sleep(.02)
        assert material['content']['text'] == 'После перезапуска'


def test_reassigned_public_username_cannot_replace_connected_peer(tmp_path: Path):
    class Reassigned(Telegram):
        changed = False
        async def info(self, account, peer):
            return dict(peer=-1009876543210 if self.changed else -1001234567890,
                        title='Канал', kind='channel', forum=False, username='fixture')
        async def messages(self, account, peer, ids):
            assert not self.changed, 'Must reject the reassigned username before fetching'
            return await super().messages(account, peer, ids)
    original = Reassigned()
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=original), headers=HEADERS) as client:
        presence(client, 'test')
        connect(client)
        item = settled(client)[0]['id']
    changed = Reassigned()
    changed.changed = True
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=changed), headers=HEADERS) as client:
        presence(client, 'test')
        client.post('/api/v3/materials/' + item + '/retry')
        for _ in range(100):
            errors = client.get('/api/v3/status').json()['problems']['current']
            if any(e['code']=='source_identity_conflict' for e in errors):
                break
            time.sleep(.02)
        assert any(e['code']=='source_identity_conflict' for e in errors)
        assert client.get('/api/v3/materials/' + item).json()['content']['text'] == 'Текст канала'



def test_reconnect_former_public_channel_by_numeric_peer_does_not_restore_old_username(tmp_path: Path):
    class FormerPublic(Telegram):
        private = False
        async def info(self, account, peer):
            if self.private and not isinstance(peer, int):
                from oko.collection.contracts import SourceError
                raise SourceError('Нет доступа к Telegram-источнику')
            return dict(peer=-1001234567890, title='Канал', kind='channel', forum=False,
                        username=None if self.private else 'fixture')
        async def messages(self, account, peer, ids):
            return [dict(id=11, date=time.time()-60, text='Теперь приватный' if self.private else 'Публичный', group=None, media=None)]
    tg = FormerPublic()
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=tg), headers=HEADERS) as client:
        client.get('/api/v3/subscriptions')
        presence(client, 'test')
        feed = connect(client)
        item = settled(client)[0]['id']
        tg.private = True
        response = connect_request(client, json=dict(account='fixture', peer='-1001234567890', folder='Новости', auto_read=False, feed_id=feed['id']))
        assert response.status_code == 422
        before=client.get('/api/v3/subscriptions').json()
        assert before['feeds'][0]['id']==feed['id']
        assert client.delete('/api/v3/feeds/'+feed['id']).status_code==200
        wait_for(client,lambda: client.get('/api/v3/subscriptions').json()['feeds']==[])
        replacement=connect(client,'-1001234567890')
        assert replacement['id']!=feed['id']
        assert client.get('/api/v3/materials/'+item).status_code==404
        item=settled(client)[0]['id']
        for _ in range(100):
            material = client.get('/api/v3/materials/' + item).json()
            if material['content'].get('text') == 'Теперь приватный' and not material['content'].get('loading'):
                break
            time.sleep(.02)
        assert material['content']['text'] == 'Теперь приватный'
        assert material['source_url']=='https://t.me/c/1234567890/11'


@pytest.mark.parametrize('advance', [True, False])
def test_owned_worker_verifies_readback_when_ack_returns_false(tmp_path: Path, monkeypatch, advance):
    from oko.collection.telegram.source import LocalTelegram
    project = fake_sdk_project(tmp_path, monkeypatch)
    sdk = project / 'telethon' / '__init__.py'
    code = sdk.read_text().replace('self.session = session', 'self.session = session; self.read_max = 0')
    code = code.replace('types = SimpleNamespace(Channel=Channel, Chat=Chat)', 'types = SimpleNamespace(Channel=Channel, Chat=Chat, InputDialogPeer=lambda peer: peer)')
    code += '''
    async def get_input_entity(self, entity): return entity
    async def __call__(self, request):
        return SimpleNamespace(dialogs=[SimpleNamespace(read_inbox_max_id=self.read_max)])
    async def send_read_acknowledge(self, entity, *, max_id):
        assert max_id == 12 and max_id > 0
        if os.environ.get('SDK_FIXTURE_ACK_NO_ADVANCE') != 'yes':
            self.read_max = max_id
        return False
'''
    code = code.replace("        if isinstance(params.get('ids'), int):", "        if 'max_id' in params:\n            return [msg(i) for i in (11, 12) if params.get('offset_id', 0) < i < params['max_id']]\n        if isinstance(params.get('ids'), int):")
    code = code.replace("        await asyncio.sleep(100)", "        return file")
    sdk.write_text(code)
    (project / 'telethon' / 'functions.py').write_text('from types import SimpleNamespace\nmessages=SimpleNamespace(GetPeerDialogsRequest=lambda peers: peers)\n')
    if not advance:
        monkeypatch.setenv('SDK_FIXTURE_ACK_NO_ADVANCE', 'yes')
    root = tmp_path / 'data'
    with TestClient(create_app(root, token='test-token', telegram_client=LocalTelegram(project / '.env', root)), headers=HEADERS) as client:
        presence(client, 'test')
        connect(client)
        card = settled(client)[0]
        assert [int(identity) for identity in card['source']['message_ids']] == [11, 12]
        assert client.post('/api/v3/materials/' + card['id'] + '/viewed').status_code == 200
        for _ in range(200):
            status = client.get('/api/v3/status').json()
            if any(r['confirmed'] == '12' if advance else bool(r['error']) for r in status['telegram_reads']):
                break
            time.sleep(.02)
        assert status['telegram_reads'][0]['confirmed'] == ('12' if advance else None)
        if not advance:
            assert 'не подтвердил' in status['telegram_reads'][0]['error']
            assert status['telegram_reads'][0]['pending'] == 2
        assert client.get('/api/v3/materials/' + card['id']).status_code == 404

def test_worker_descending_snapshot_expands_boundary_album(tmp_path: Path, monkeypatch):
    import asyncio
    from oko.collection.telegram.source import LocalTelegram
    project = fake_sdk_project(tmp_path, monkeypatch)
    async def check():
        bridge = LocalTelegram(project / '.env', tmp_path / 'data')
        try:
            page = await bridge.history_page('fixture', -1001234567890, 0, 0, 0)
            assert [m['id'] for m in page['messages']] == [11,12]
            assert page['upper'] == 12
            assert page['next_before'] == 11
            assert not page['more']
        finally:
            await bridge.close()
    asyncio.run(check())


def test_worker_service_actions_keep_read_ids_but_do_not_consume_post_count(tmp_path: Path, monkeypatch):
    import asyncio
    from oko.collection.telegram.source import LocalTelegram, TelegramSource
    project = fake_sdk_project(tmp_path, monkeypatch)
    sdk = project / 'telethon' / '__init__.py'
    text = sdk.read_text().replace("return [msg(11)] if not params.get('offset_id') else []", "return [SimpleNamespace(id=20, date=datetime.now(timezone.utc), grouped_id=None, action=object()), msg(11)] if not params.get('offset_id') else []")
    sdk.write_text(text)
    async def scenario():
        bridge = LocalTelegram(project / '.env', tmp_path / 'data')
        try:
            source = TelegramSource(bridge, 'fixture', -1001234567890, 1)
            source.initial_history = dict(mode='count', count=1, since=None, paused=False)
            publications = (await source.discover(None)).publications
            assert [p.members for p in publications] == [(11, 12)]
            window = await bridge.read_window('fixture', -1001234567890, 0, 20)
            assert 20 in window['ids']
        finally:
            await bridge.close()
    asyncio.run(scenario())


def test_owned_client_streams_one_file_into_adapter_output(tmp_path,monkeypatch):
    import asyncio
    from io import BytesIO
    from oko.collection.telegram.source import LocalTelegram
    project=fake_sdk_project(tmp_path,monkeypatch)
    sdk=project/'telethon'/'__init__.py'
    sdk.write_text(sdk.read_text().replace('await asyncio.sleep(100)','return file.name'))
    async def scenario():
        bridge=LocalTelegram(project/'.env',tmp_path/'data')
        output=BytesIO()
        try:
            size=await bridge.download_stream('fixture',-1001234567890,11,output)
            assert size==len(b'partial-download') and output.getvalue()==b'partial-download'
            assert not list((tmp_path/'data'/'staging'/'_telegram').rglob('media.bin'))
        finally:
            await bridge.close()
    asyncio.run(scenario())
