"""Telegram source and cancellable worker for Oko-owned Telethon client."""
import asyncio
from collections.abc import Callable
from contextlib import suppress
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from oko.collection.contracts import SourceError
from oko.collection.contracts import Attachment, Material, Publication


class TelegramClient(Protocol):
    async def accounts(self) -> list[str]: ...
    async def info(self, account: str, peer: str | int) -> dict[str, Any]: ...
    async def page(self, account: str, peer: int, after: int, since: float) -> dict[str, Any]: ...
    async def messages(self, account: str, peer: int, ids: list[int]) -> list[dict[str, Any]]: ...
    async def download(self, account: str, peer: int, id: int) -> bytes: ...
    async def close(self) -> None: ...


class ConnectTelegram(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    account: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    peer: str = Field(min_length=1, max_length=100, pattern=r'^(@?[a-zA-Z][a-zA-Z0-9_]{3,31}|-[0-9]+)$')
    folder: str = Field(min_length=1, max_length=200)
    feed_id: str | None = None
    auto_read: bool


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

    async def request(self, operation: str, **args: Any) -> Any:
        generation = self.generation
        async with self.lock:
            try:
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
                process.stdin.write((json.dumps(dict(op=operation, **args)) + '\n').encode())
                await process.stdin.drain()
                line = await asyncio.wait_for(process.stdout.readline(), timeout=180 if operation == 'download' else 30)
                if not line:
                    raise SourceError('Telegram-клиент недоступен')
                result = json.loads(line)
                if 'error' in result:
                    raise SourceError(result['error'], result.get('delay', 60))
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
                await self.close()
                raise SourceError('Telegram-клиент недоступен') from None

    async def accounts(self) -> list[str]:
        value: list[str] = await self.request('accounts')
        return value

    async def info(self, account: str, peer: str | int) -> dict[str, Any]:
        value: dict[str, Any] = await self.request('info', account=account, peer=peer)
        return value

    async def page(self, account: str, peer: int, after: int, since: float) -> dict[str, Any]:
        value: dict[str, Any] = await self.request('page', account=account, peer=peer, after=after, since=since)
        return value

    async def messages(self, account: str, peer: int, ids: list[int]) -> list[dict[str, Any]]:
        value: list[dict[str, Any]] = await self.request('messages', account=account, peer=peer, ids=ids)
        return value

    async def download(self, account: str, peer: int, id: int) -> bytes:
        value: bytes = await self.request('download', account=account, peer=peer, id=id)
        return value

    async def close(self) -> None:
        self.generation += 1
        process, self.process = self.process, None
        if process and process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        shutil.rmtree(self.spool, ignore_errors=True)


class TelegramSource:
    def __init__(self, client: TelegramClient, account: str, peer: int, initial_days: int, username: str | None = None) -> None:
        self.username = username
        self.client, self.account, self.peer, self.initial_days = client, account, peer, initial_days
        self.more = False
        self.gap: str | None = None

    async def resolve(self) -> None:
        # StringSession does not persist Telethon's entity/access-hash cache.
        # Refill it in the current worker, but never change the feed's identity.
        if self.username:
            info = await self.client.info(self.account, '@' + self.username)
            if info['peer'] != self.peer:
                raise SourceError('Username Telegram теперь принадлежит другому источнику; переподключите фид')

    def url(self, id: int) -> str:
        return f'https://t.me/c/{-self.peer-1000000000000}/{id}' if self.peer < -1000000000000 else f'tg://openmessage?chat_id={-self.peer}&message_id={id}'

    async def discover(self, cursor: str | None) -> tuple[list[Publication], str]:
        await self.resolve()
        state: dict[str, Any] = json.loads(cursor) if cursor else dict(after=0, since=time.time()-self.initial_days*86400)
        batch = await self.client.page(self.account, self.peer, state['after'], state['since'])
        self.more, self.gap = batch['more'], batch.get('gap')
        groups: dict[str, list[dict[str, Any]]] = {}
        for msg in batch['messages']:
            group = 'album:' + str(msg['group']) if msg['group'] else str(msg['id'])
            groups.setdefault(group, []).append(msg)
        publications = []
        for messages in groups.values():
            messages.sort(key=lambda m: m['id'])
            if max(m['id'] for m in messages) <= state['after'] or max(m['date'] for m in messages) < state['since']:
                continue
            text = '\n\n'.join(m['text'] for m in messages if m['text'])
            id = messages[0]['id']
            publications.append(Publication('album:' + str(messages[0]['group']) if messages[0]['group'] else str(id), self.url(id), messages[0]['date'],
                                           Material(text.split('\n')[0][:160] or 'Telegram · медиа', text, False),
                                           tuple(m['id'] for m in messages)))
        state['after'] = max([state['after']] + [m for p in publications for m in p.members])
        if self.more and state['after'] == (json.loads(cursor)['after'] if cursor else 0):
            raise SourceError('Telegram не продвинул позицию сбора')
        return publications, json.dumps(state)

    async def fetch(self, publication: Publication) -> Material:
        await self.resolve()
        rows = await self.client.messages(self.account, self.peer, list(publication.members) or [int(publication.id)])
        teaser = publication.teaser or Material('Публикация недоступна', '')
        if not rows:
            return Material(teaser.title, teaser.text, False, 'Публикация недоступна в Telegram', attachments=teaser.attachments)
        text = '\n\n'.join(msg['text'] for msg in rows if msg['text']) or teaser.text
        attachments = []
        reasons = []
        if set(publication.members) - {msg['id'] for msg in rows}:
            reasons.append('Часть альбома недоступна в Telegram')
        for msg in rows:
            media = msg['media']
            if not media:
                continue
            if media['kind'] == 'unsupported':
                reasons.append('Тип вложения пока не поддерживается')
                continue
            try:
                data = await self.client.download(self.account, self.peer, msg['id'])
                if not data:
                    raise SourceError('Пустое вложение Telegram')
                attachments.append(Attachment(f"media-{msg['id']}.{media['extension']}", data, media['kind'], media['mime']))
            except SourceError as error:
                reasons.append(error.label)
        names = {a.name for a in attachments}
        attachments.extend(a for a in teaser.attachments if a.name not in names)
        if any(msg['group'] and time.time()-msg['date'] < 5 for msg in rows):
            reasons.append('Альбом ещё публикуется; повторите загрузку')
        return Material(text.split('\n')[0][:160] or 'Telegram · медиа', text, not reasons,
                        '; '.join(dict.fromkeys(reasons)), attachments=tuple(attachments), members=tuple(sorted({m['id'] for m in rows} | set(publication.members))))
