from tests.support.telegram import Telegram, connect, settled
from pathlib import Path
import time
from fastapi.testclient import TestClient
from oko.reader.app import create_app

HEADERS = {'Authorization': 'Bearer test-token'}


def test_public_channel_connects_reads_and_viewed_does_not_resurrect(tmp_path: Path):
    kwargs = dict(token='test-token', telegram_client=Telegram())
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        assert client.get('/api/telegram/accounts').json() == ['fixture']
        feed = connect(client)
        assert feed['kind'] == 'telegram' and feed['connection'] == 'connected'
        card = settled(client)[0]
        material = client.get('/api/materials/' + card['id']).json()
        assert material['text'] == 'Текст канала' and material['full'] is True
        assert material['url'] == 'https://t.me/c/1234567890/11'
        client.post('/api/materials/' + card['id'] + '/viewed')
        assert client.get('/api/materials/' + card['id']).status_code == 404
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        time.sleep(.15)
        assert client.get('/api/cards').json() == []


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
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        card = settled(client)[0]
        item = card['id']
        material = client.get('/api/materials/' + item).json()
        assert material['full'] is False
        assert material['members'] == [21, 22, 23]
        assert len(material['attachments']) == 2
        assert {a['kind'] for a in material['attachments']} == {'image', 'circle'}
        assert 'Обрыв' in material['reason']
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        material = client.get('/api/materials/' + item).json()
        assert len(material['attachments']) == 2 and not material['full']
        tg.available = True
        client.post('/api/materials/' + item + '/retry')
        card = settled(client)[0]
        assert card['full'] is True
        assert len(card['attachments']) == 3
        for attachment in card['attachments']:
            response = client.get(attachment['url'])
            assert response.status_code == 200
            assert response.content in (b'jpeg-image', b'mp4-video', b'mp4-circle')
        client.post('/api/materials/' + item + '/viewed')
        assert all(client.get(a['url']).status_code == 404 for a in card['attachments'])
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()


def test_paged_history_resumes_committed_position_and_errors_do_not_block_other_feeds(tmp_path: Path):
    from oko.collection.contracts import SourceError
    class Pages(Telegram):
        async def info(self, account, peer):
            return dict(peer=-1001234567891 if peer == '@denied' else -1001234567890, title=str(peer), kind='group', forum=False)
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
        client.post('/api/tabs/test/heartbeat')
        connect(client, '@denied')
        connect(client)
        cards = settled(client, 2)
        assert {c['title'] for c in cards} == {'Сообщение 101', 'Сообщение 205'}
        assert any(s['gap'] for s in client.get('/api/status').json()['sources'])
        assert 'Нет доступа к Telegram-источнику' in client.get('/api/status').json()['errors']
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        assert len(settled(client, 2)) == 2
        client.post('/api/collect')
        time.sleep(.15)
        assert len(client.get('/api/cards').json()) == 2


def test_private_group_access_forum_and_account_errors_are_explicit(tmp_path: Path):
    from oko.collection.contracts import SourceError
    class Peers(Telegram):
        async def info(self, account, peer):
            if peer == '@denied':
                raise SourceError('Нет доступа к Telegram-источнику')
            return dict(peer=-12345, title='Приватная группа', kind='group', forum=peer == '@forum')
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=Peers()), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        assert client.post('/api/telegram/connect', json=dict(account='fixture', peer='@denied', folder='Личное', auto_read=False)).json()['detail'] == 'Нет доступа к Telegram-источнику'
        assert 'Forum' in client.post('/api/telegram/connect', json=dict(account='fixture', peer='@forum', folder='Личное', auto_read=False)).json()['detail']
        assert client.get('/api/subscriptions').json()['feeds'] == []
        feed = connect(client, '-12345', 'Личное')
        assert feed['telegram_peer'] == -12345
        assert len(client.get('/api/subscriptions').json()['feeds']) == 1
        assert client.post('/api/telegram/connect', json=dict(account='fixture', peer='-12345', folder='Другое', auto_read=False)).status_code == 400
        assert connect(client, '-12345', 'Личное')['id'] == feed['id']
        card = settled(client)[0]
        assert card['url'] == 'tg://openmessage?chat_id=12345&message_id=11'


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
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        assert downloading.wait(2)
        item = client.get('/api/cards').json()[0]['id']
        assert client.post('/api/materials/' + item + '/viewed').status_code == 200
        released.set()
        time.sleep(.1)
        assert client.get('/api/materials/' + item).status_code == 404
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()


def test_imported_telegram_feed_connects_without_duplicate_and_read_settings_are_inert(tmp_path: Path):
    class Imported(Telegram):
        async def info(self, account, peer):
            return dict(peer=-1001234567890, title='Канал', kind='channel', forum=False, username='fixture')
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=Imported()), headers=HEADERS) as client:
        imported = client.post('/api/import', json={'opml': '<opml><body><outline text="Новости"><outline text="Канал" xmlUrl="https://rsshub.test/telegram/channel/fixture"/></outline></body></opml>'}).json()['feeds'][0]
        client.post('/api/tabs/test/heartbeat')
        feed = connect(client)
        assert feed['id'] == imported['id']
        assert len(client.get('/api/subscriptions').json()['feeds']) == 1
        client.patch('/api/feeds/' + feed['id'], json={'auto_read': True, 'read_history': True})
        card = settled(client)[0]
        assert card['full']
        client.post('/api/materials/' + card['id'] + '/viewed')
        assert client.get('/api/cards').json() == []


def test_discovery_interruption_keeps_album_identity_and_files_recover(tmp_path: Path):
    failed = False
    def fault(point):
        nonlocal failed
        if point == 'before_discovery_commit' and not failed:
            failed = True
            raise OSError('simulated interruption')
    kwargs = dict(token='test-token', telegram_client=Telegram())
    with TestClient(create_app(tmp_path, fault=fault, **kwargs), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        for _ in range(100):
            if client.get('/api/status').json()['errors']:
                break
            time.sleep(.02)
        assert failed
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        card = settled(client)[0]
        assert card['full'] and card['title'] == 'Текст канала'
        assert len(client.get('/api/cards').json()) == 1


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
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        for _ in range(100):
            partial = root / 'staging' / '_telegram' / 'media.bin'
            if partial.exists() and partial.stat().st_size:
                break
            time.sleep(.02)
        assert partial.read_bytes() == b'partial-download'
        card = client.get('/api/cards').json()[0]
        assert card['members'] == [11, 12] and card['title'] == 'Album'
        client.post('/api/materials/' + card['id'] + '/viewed')
        assert not (root / 'staging' / '_telegram').exists()
        assert not (root / 'content' / card['id']).exists()
        assert client.get('/api/cards').json() == []


def test_switching_rss_transport_keeps_telegram_cursor_separate(tmp_path: Path):
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
        feed = client.post('/api/import', json={'opml': '<opml><body><outline text="Новости"><outline xmlUrl="https://source.test/telegram/channel/fixture"/></outline></body></opml>'}).json()['feeds'][0]
        client.patch('/api/feeds/' + feed['id'], json={'transport': 'rss'})
        client.post('/api/tabs/test/heartbeat')
        settled(client)
        connect(client)
        cards = settled(client, 2)
        assert {c['title'] for c in cards} == {'RSS-текст', 'Текст канала'}


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
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        for _ in range(100):
            if client.get('/api/status').json()['errors']:
                break
            time.sleep(.02)
        assert failed
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        card = settled(client)[0]
        assert card['full'] and len(card['attachments']) == 1
        assert client.get(card['attachments'][0]['url']).content == b'media'
        client.post('/api/materials/' + card['id'] + '/viewed')
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
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        original = settled(client)[0]
        tg.rows = tg.rows + [dict(id=12, date=time.time()-60, text='', group=777, media=dict(kind='video', mime='video/mp4', extension='mp4'))]
        client.post('/api/collect')
        for _ in range(150):
            material = client.get('/api/materials/' + original['id']).json()
            if not material.get('loading') and len(material.get('attachments', [])) == 2:
                break
            time.sleep(.02)
        assert len(client.get('/api/cards').json()) == 1
        assert material['members'] == [11, 12]
        assert material['full'] is True and len(material['attachments']) == 2


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
        client.post('/api/tabs/test/heartbeat')
        connect(client)
        card = settled(client)[0]
        client.post('/api/materials/' + card['id'] + '/viewed')
    tg.rows = [dict(id=13, date=time.time()-60, text='Поздняя часть', group=777, media=None)]
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        time.sleep(.15)
        assert client.get('/api/cards').json() == []


def test_local_client_uses_env_without_external_project_or_venv(tmp_path: Path):
    from oko.collection.telegram.source import LocalTelegram
    # Account listing parses configuration without contacting Telegram or copying it.
    env = tmp_path / 'credentials.env'
    env.write_text('TELEGRAM_API_ID=123\nTELEGRAM_API_HASH=synthetic\nTELEGRAM_SESSION_STRING_FIXTURE=synthetic-session\n')
    root = tmp_path / 'data'
    with TestClient(create_app(root, token='test-token', telegram_client=LocalTelegram(env, root)), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        result = client.get('/api/telegram/accounts')
        assert result.status_code == 200, result.json()
        assert result.json() == ['fixture']
        assert not list(root.rglob('*.session')) and not list(root.rglob('*.env'))


def test_direct_client_never_logs_in_or_copies_an_unauthorized_session(tmp_path: Path, monkeypatch):
    from oko.collection.telegram.source import LocalTelegram
    project = fake_sdk_project(tmp_path, monkeypatch)
    monkeypatch.setenv('SDK_FIXTURE_UNAUTHORIZED', 'yes')
    root = tmp_path / 'data'
    with TestClient(create_app(root, token='test-token', telegram_client=LocalTelegram(project / '.env', root)), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        response = client.post('/api/telegram/connect', json=dict(account='fixture', peer='@fixture', folder='Новости', auto_read=False))
        assert response.status_code == 409
        assert response.json()['detail'] == 'Требуется авторизация существующего Telegram-клиента'
        assert client.get('/api/subscriptions').json()['feeds'] == []
        assert not list(root.rglob('*.session')) and not list(root.rglob('*.env'))


def test_direct_client_prefers_existing_string_session_to_stale_file_setting(tmp_path: Path, monkeypatch):
    from oko.collection.telegram.source import LocalTelegram
    project = fake_sdk_project(tmp_path, monkeypatch)
    (project / '.env').write_text('TELEGRAM_API_ID=123\nTELEGRAM_API_HASH=synthetic\nTELEGRAM_SESSION_NAME=missing-old-file\nTELEGRAM_SESSION_STRING=synthetic-session\n')
    root = tmp_path / 'data'
    with TestClient(create_app(root, token='test-token', telegram_client=LocalTelegram(project / '.env', root)), headers=HEADERS) as client:
        client.post('/api/tabs/test/heartbeat')
        response = client.post('/api/telegram/connect', json=dict(account='default', peer='@fixture', folder='Новости', auto_read=False))
        assert response.status_code == 200, response.json()
        assert response.json()['telegram_account'] == 'default'
        assert not list(project.rglob('*.session'))
