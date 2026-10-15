"""Telegram source and cancellable worker for Oko-owned Telethon client."""
from oko.diagnostics import record_caught
from oko.subscriptions.history import InitialHistory
import asyncio
from collections.abc import Callable
from contextlib import suppress
import json
import hashlib
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from oko.collection.contracts import SourceError, SourceDeleted
from oko.collection.contracts import Attachment, Material, Publication


class TelegramClient(Protocol):
    async def identity(self, account: str) -> int: ...
    async def accounts(self) -> list[str]: ...
    async def topics(self, account: str, peer: str | int) -> list[dict[str, Any]]: ...
    async def info(self, account: str, peer: str | int, topic_id: int | None = None) -> dict[str, Any]: ...
    async def page(self, account: str, peer: int, after: int, since: float, topic_id: int | None = None) -> dict[str, Any]: ...
    async def history_page(self, account: str, peer: int, upper: int, before: int, since: float, topic_id: int | None = None) -> dict[str, Any]: ...
    async def messages(self, account: str, peer: int, ids: list[int], topic_id: int | None = None) -> list[dict[str, Any]]: ...
    async def download(self, account: str, peer: int, id: int, topic_id: int | None = None) -> bytes: ...
    async def read_state(self, account: str, peer: int, since: float, topic_id: int | None = None) -> dict[str, Any]: ...
    async def read_window(self, account: str, peer: int, after: int, until: int, topic_id: int | None = None) -> dict[str, Any]: ...
    async def acknowledge(self, account: str, peer: int, max_id: int, topic_id: int | None = None) -> None: ...
    async def close(self) -> None: ...


class TelegramPeer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    account: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    peer: str = Field(min_length=1, max_length=100, pattern=r'^(@?[a-zA-Z][a-zA-Z0-9_]{3,31}|-[0-9]+)$')


class ConnectTelegram(TelegramPeer):
    topic_id: int | None = Field(default=None, ge=1, le=2147483647)
    folder: str = Field(min_length=1, max_length=200)
    feed_id: str | None = None
    auto_read: bool
    initial_history: InitialHistory | None = None


class LocalTelegram:
    """Oko-owned Telethon worker; existing credentials remain at their original path."""
    def __init__(self, env_file: Path, root: Path) -> None:
        self.env_file = (env_file / '.env' if env_file.is_dir() else env_file).resolve()
        self.root = root.resolve()
        self.process: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()
        self.spool = self.root / 'staging' / '_telegram'
        self.allowed: Callable[[], bool] = lambda: True
        self.generation = 0
        self.expected_users: dict[str, int] = {}
        self._configuration_digest: str | None = None

    async def request(self, operation: str, **args: Any) -> Any:
        generation = self.generation
        async with self.lock:
            try:
                if self.env_file.is_file():
                    digest = hashlib.sha256(self.env_file.read_bytes()).hexdigest()
                    if self._configuration_digest is not None and digest != self._configuration_digest:
                        await self.close()
                        generation = self.generation
                    self._configuration_digest = digest
                if generation != self.generation or not self.allowed():
                    raise SourceError('Подключение Telegram остановлено')
                if not self.process or self.process.returncode is not None:
                    if not self.env_file.is_file():
                        raise SourceError('Окружение Telegram-клиента недоступно')
                    self.spool.mkdir(parents=True, exist_ok=True, mode=0o700)
                    self.process = await asyncio.create_subprocess_exec(
                        sys.executable, '-m', 'oko.collection.telegram.worker', str(self.spool), str(self.env_file),
                        cwd=Path(__file__).resolve().parents[3], stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=4*1024*1024)
                process = self.process
                assert process.stdin and process.stdout
                if args.get('account') in self.expected_users and operation != 'identity':
                    args['expected_user_id'] = self.expected_users[args['account']]
                process.stdin.write((json.dumps(dict(op=operation, **args)) + '\n').encode())
                await process.stdin.drain()
                line = await asyncio.wait_for(process.stdout.readline(), timeout=180 if operation == 'download' else 30)
                if not line:
                    raise SourceError('Telegram-клиент недоступен')
                result = json.loads(line)
                if 'error' in result:
                    raise SourceError.from_worker(result['error'], result, result.get('delay', 60))
                if operation == 'download':
                    path = self.spool / 'media.bin'
                    try:
                        return path.read_bytes()
                    finally:
                        path.unlink(missing_ok=True)
                return result['value']
            except SourceError:
                raise
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception:
                record_caught('source.request')
                await self.close()
                raise SourceError('Telegram-клиент недоступен') from None

    async def identity(self, account: str) -> int:
        value = await self.request('identity', account=account)
        if type(value) is not int or value <= 0:
            raise SourceError('Telegram не подтвердил личность аккаунта')
        return value

    async def accounts(self) -> list[str]:
        value: list[str] = await self.request('accounts')
        return value

    async def topics(self, account: str, peer: str | int) -> list[dict[str, Any]]:
        value: list[dict[str, Any]] = await self.request('topics', account=account, peer=peer)
        return value

    async def info(self, account: str, peer: str | int, topic_id: int | None = None) -> dict[str, Any]:
        value: dict[str, Any] = await self.request('info', account=account, peer=peer, topic_id=topic_id)
        return value

    async def page(self, account: str, peer: int, after: int, since: float, topic_id: int | None = None) -> dict[str, Any]:
        value: dict[str, Any] = await self.request('page', account=account, peer=peer, after=after, since=since, topic_id=topic_id)
        return value

    async def history_page(self, account: str, peer: int, upper: int, before: int, since: float, topic_id: int | None = None) -> dict[str, Any]:
        value: dict[str, Any] = await self.request('history_page', account=account, peer=peer, upper=upper, before=before, since=since, topic_id=topic_id)
        return value

    async def messages(self, account: str, peer: int, ids: list[int], topic_id: int | None = None) -> list[dict[str, Any]]:
        value: list[dict[str, Any]] = await self.request('messages', account=account, peer=peer, ids=ids, topic_id=topic_id)
        return value

    async def download(self, account: str, peer: int, id: int, topic_id: int | None = None) -> bytes:
        value: bytes = await self.request('download', account=account, peer=peer, id=id, topic_id=topic_id)
        return value

    async def read_state(self, account: str, peer: int, since: float, topic_id: int | None = None) -> dict[str, Any]:
        value: dict[str, Any] = await self.request('read_state', account=account, peer=peer, since=since, topic_id=topic_id)
        return value

    async def read_window(self, account: str, peer: int, after: int, until: int, topic_id: int | None = None) -> dict[str, Any]:
        value: dict[str, Any] = await self.request('read_window', account=account, peer=peer, after=after, until=until, topic_id=topic_id)
        return value

    async def acknowledge(self, account: str, peer: int, max_id: int, topic_id: int | None = None) -> None:
        if max_id <= 0:
            raise SourceError('Неверная граница прочитанного Telegram')
        result = await self.request('acknowledge', account=account, peer=peer, max_id=max_id, topic_id=topic_id)
        if result is not True:
            # A repeated already-read boundary can return False. Verify the
            # server's actual frontier before treating it as failure.
            state = await self.read_state(account, peer, time.time(), topic_id=topic_id)
            if state['read_max'] < max_id:
                raise SourceError('Telegram не подтвердил отметку прочитанного')

    async def close(self) -> None:
        self.generation += 1
        process, self.process = self.process, None
        if process and process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        shutil.rmtree(self.spool, ignore_errors=True)


class TelegramSource:
    changes = 'Telegram: при сборе перечитываются известные непросмотренные сообщения и вложения. Отсутствующие сообщения сохраняются локально; исчезновение может означать удаление или потерю доступа.'
    def __init__(self, client: TelegramClient, account: str, peer: int, initial_days: int, username: str | None = None, topic_id: int | None = None) -> None:
        self.identity_info: dict[str, Any] | None = None
        self.topic_id = topic_id
        self.username = username
        self.client, self.account, self.peer, self.initial_days = client, account, peer, initial_days
        self.initial_history: dict[str, Any] | None = None
        self.more = False
        self.gap: str | None = None

    @property
    def scope(self) -> dict[str, int]:
        return {'topic_id': self.topic_id} if self.topic_id is not None else {}

    async def resolve(self) -> None:
        # StringSession does not persist Telethon's entity/access-hash cache.
        # Refill it in the current worker, but never change the feed's identity.
        if self.username:
            info = await self.client.info(self.account, '@' + self.username, **self.scope)
            self.identity_info = info
            if info['peer'] != self.peer:
                raise SourceError('Username Telegram теперь принадлежит другому источнику; переподключите фид', code='source-reconnect')

    def url(self, id: int) -> str:
        topic = f'{self.topic_id}/' if self.topic_id else ''
        return f'https://t.me/c/{-self.peer-1000000000000}/{topic}{id}' if self.peer < -1000000000000 else f'tg://openmessage?chat_id={-self.peer}&message_id={id}'

    async def discover(self, cursor: str | None) -> tuple[list[Publication], str]:
        await self.resolve()
        if self.initial_history:
            return await self.discover_history(cursor)
        state: dict[str, Any] = json.loads(cursor) if cursor else dict(after=0, since=time.time()-self.initial_days*86400)
        batch = await self.client.page(self.account, self.peer, state['after'], state['since'], **self.scope)
        self.more, self.gap = batch['more'], batch.get('gap')
        dates = {m['id']: m['date'] for m in batch['messages']}
        publications = [p for p in reversed(self.publications(batch['messages']))
                        if max(p.members) > state['after'] and max(dates[m] for m in p.members) >= state['since']]
        state['after'] = max([state['after'], batch.get('next_after', 0)] + [m for p in publications for m in p.members])
        if self.more and state['after'] == (json.loads(cursor)['after'] if cursor else 0):
            raise SourceError('Telegram не продвинул позицию сбора')
        return publications, json.dumps(state)

    def publications(self, rows: list[dict[str, Any]], posts_only: bool = False) -> list[Publication]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for msg in rows:
            if posts_only and msg.get('service'):
                continue
            identity = 'album:' + str(msg['group']) if msg['group'] else str(msg['id'])
            groups.setdefault(identity, []).append(msg)
        result = []
        for identity, members in groups.items():
            members.sort(key=lambda m: m['id'])
            text = '\n\n'.join(m['text'] for m in members if m['text'])
            result.append(Publication(identity, self.url(members[0]['id']), members[0]['date'],
                          Material(text.split('\n')[0][:160] or 'Telegram · медиа', text, False),
                          tuple(m['id'] for m in members)))
        return sorted(result, key=lambda p: max(p.members), reverse=True)

    async def discover_history(self, cursor: str | None) -> tuple[list[Publication], str]:
        assert self.initial_history
        config = self.initial_history
        state: dict[str, Any] = json.loads(cursor) if cursor else dict(after=0, since=config['since'] or 0, upper=0,
                                                       before=0, enrolled=0, scan_complete=False, lane='history', oldest=None)
        state.pop('history_ids', None)
        state.pop('history_enroll', None)
        state.setdefault('warnings', ['Telegram не предоставляет атомарный снимок истории: набор выбирается по доступным страницам; удалённые во время выбора публикации могли не попасть в него.'])
        if config.get('paused') and not state['upper'] and not state['scan_complete']:
            # Establish the snapshot boundary without enrolling archive materials.
            # Live requests then start above it even if history was paused before
            # its first successful request. Resume rereads this same archive page.
            batch = await self.client.history_page(self.account, self.peer, 0, 0, state['since'], **self.scope)
            state['upper'] = state['after'] = batch['upper']
            state['lane'] = 'live'
            if not state['upper']:
                state['scan_complete'] = True
                state['history_enroll'] = []
            self.more = bool(state['upper'])
            self.gap = batch.get('gap')
            return [], json.dumps(state)
        # Metadata pages build a durable manifest before count-mode content fetches.
        # Telegram cannot provide an atomic server snapshot. Alternate bounded
        # live requests with archive requests so new messages never wait for it.
        history_lane = not state['scan_complete'] and not config.get('paused') and (state['lane'] == 'history' or not state['upper']) and time.time() >= state.get('history_retry_at', 0)
        if history_lane:
            try:
                batch = await self.client.history_page(self.account, self.peer, state['upper'], state['before'], state['since'], **self.scope)
            except SourceError as error:
                if not state['upper']:
                    raise
                record_caught('Telegram: начальная история')
                state.update(history_error=error.label, history_retry_at=time.time() + error.delay, lane='live')
                state['history_enroll'] = []
                self.more, self.gap = True, error.label
                return [], json.dumps(state)
            state.pop('history_error', None)
            state.pop('history_retry_at', None)
            if not state['upper']:
                state['upper'] = batch['upper']
                state['after'] = batch['upper']
            rows = self.publications(batch['messages'], posts_only=config['mode'] == 'count')
            rows = [p for p in rows if not state['before'] or max(p.members) < state['before']]
            chosen = [p for p in rows if config['mode'] == 'count' or p.published >= state['since']]
            if config['mode'] == 'count':
                chosen = chosen[:max(0, config['count'] - state['enrolled'])]
            state['enrolled'] += len(chosen)
            if chosen:
                state['oldest'] = min([min(p.members) for p in chosen] + ([state['oldest']] if state['oldest'] else []))
            previous = state['before']
            state['before'] = batch['next_before']
            state['scan_complete'] = not batch['more'] or (config['mode'] == 'count' and state['enrolled'] >= config['count']) or (config['mode'] == 'date' and any(p.published < state['since'] for p in rows))
            if not state['scan_complete'] and (state['before'] == previous or not state['before']):
                raise SourceError('Telegram не продвинул позицию истории')
            # Keep the raw cursor: expanded album members can straddle other
            # publications. The maximum-member filter suppresses the same album
            # on the next page without skipping intervening publications.
            state['history_enroll'] = [p.id for p in chosen]
            state['lane'] = 'live'
            publications = chosen
        else:
            batch = await self.client.page(self.account, self.peer, state['after'], state['since'], **self.scope)
            publications = [p for p in self.publications(batch['messages']) if max(p.members) > state['after']]
            state['after'] = max([state['after'], batch.get('next_after', 0)] + [m for p in publications for m in p.members])
            state['lane'] = 'live' if batch['more'] else 'history'
        self.more = bool(batch.get('more')) or (not state['scan_complete'] and not config.get('paused'))
        self.gap = batch.get('gap')
        return publications, json.dumps(state)

    async def fetch(self, publication: Publication) -> Material:
        await self.resolve()
        rows = await self.client.messages(self.account, self.peer, list(publication.members) or [int(publication.id)], **self.scope)
        teaser = publication.teaser or Material('Публикация недоступна', '')
        if not rows:
            if publication.teaser and publication.teaser.full:
                raise SourceDeleted()
            return Material(teaser.title, teaser.text, False, 'Публикация недоступна в Telegram', attachments=teaser.attachments, collection_error=SourceDeleted())
        text = '\n\n'.join(msg['text'] for msg in rows if msg['text'])
        attachments = []
        reasons = []
        collection_error = None
        if set(publication.members) - {msg['id'] for msg in rows}:
            if teaser.full:
                raise SourceDeleted()
            reasons.append('Часть альбома недоступна в Telegram')
        for msg in rows:
            media = msg['media']
            if not media:
                continue
            if media['kind'] == 'unsupported':
                reasons.append('Тип вложения пока не поддерживается')
                continue
            try:
                data = await self.client.download(self.account, self.peer, msg['id'], **self.scope)
                if not data:
                    raise SourceError('Пустое вложение Telegram')
                attachments.append(Attachment(f"media-{msg['id']}.{media['extension']}", data, media['kind'], media['mime']))
            except SourceError as error:
                record_caught('source.fetch')
                reasons.append(error.label)
                if not isinstance(error, SourceDeleted):
                    collection_error = error
        names = {a.name for a in attachments}
        failed_names = {f"media-{m['id']}.{m['media']['extension']}" for m in rows if m['media'] and m['media']['kind'] != 'unsupported'}
        attachments.extend(a for a in teaser.attachments if a.name not in names and (a.name in failed_names or int(a.name.split('-')[1].split('.')[0]) not in {m['id'] for m in rows}))
        if any(msg['group'] and time.time()-msg['date'] < 5 for msg in rows):
            reasons.append('Альбом ещё публикуется; повторите загрузку')
            collection_error = SourceError('Альбом ещё публикуется', 5)
        return Material(text.split('\n')[0][:160] or 'Telegram · медиа', text, not reasons,
                        '; '.join(dict.fromkeys(reasons)), attachments=tuple(attachments), members=tuple(sorted({m['id'] for m in rows} | set(publication.members))), collection_error=collection_error)
