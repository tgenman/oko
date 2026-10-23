from oko.collection import request_observation as observation
from oko.collection.adapters import PublicationAdapter
"""Telegram source and cancellable worker for Oko-owned Telethon client."""
from dataclasses import replace
from oko.diagnostics import record_caught
from oko.subscriptions.history import InitialHistory
import asyncio
from collections.abc import Callable
from contextlib import suppress
import json
import hashlib
from pathlib import Path
import shutil
import os
import sys
import time
from typing import Any, Protocol, BinaryIO

from pydantic import BaseModel, ConfigDict, Field

from oko.collection.contracts import SourceError, SourceDeleted, DiscoveryResult, FetchResult, SourceObservation, SourceIdentity
from oko.collection.contracts import Material, Publication, SourceManifest, ResourceManifest
from oko.collection.telegram.contracts import HistoryEnvelope, Adapter
from oko.collection.telegram.web import WebTelegram
import httpx


class TelegramClient(Protocol):
    async def identity(self, account: str) -> int: ...
    async def accounts(self) -> list[str]: ...
    async def info(self, account: str, peer: str | int) -> dict[str, Any]: ...
    async def page(self, account: str, peer: int, after: int, since: float) -> dict[str, Any]: ...
    async def history_page(self, account: str, peer: int, upper: int, before: int, since: float) -> dict[str, Any]: ...
    async def messages(self, account: str, peer: int, ids: list[int]) -> list[dict[str, Any]]: ...
    async def download(self, account: str, peer: int, id: int) -> bytes: ...
    async def read_state(self, account: str, peer: int, since: float) -> dict[str, Any]: ...
    async def read_window(self, account: str, peer: int, after: int, until: int) -> dict[str, Any]: ...
    async def acknowledge(self, account: str, peer: int, max_id: int) -> None: ...
    async def close(self) -> None: ...






class _TelegramOperations:
    async def request(self, operation: str, **args: Any) -> Any:
        raise NotImplementedError

    async def identity(self, account: str) -> int:
        value = await self.request('identity', account=account)
        if type(value) is not int or value <= 0:
            raise SourceError('Telegram не подтвердил личность аккаунта')
        return value

    async def accounts(self) -> list[str]:
        value: list[str] = await self.request('accounts')
        return value

    async def info(self, account: str, peer: str | int) -> dict[str, Any]:
        value: dict[str, Any] = await self.request('info', account=account, peer=peer)
        return value

    async def page(self, account: str, peer: int, after: int, since: float) -> dict[str, Any]:
        value: dict[str, Any] = await self.request('page', account=account, peer=peer, after=after, since=since)
        return value

    async def history_page(self, account: str, peer: int, upper: int, before: int, since: float) -> dict[str, Any]:
        value: dict[str, Any] = await self.request('history_page', account=account, peer=peer, upper=upper, before=before, since=since)
        return value

    async def messages(self, account: str, peer: int, ids: list[int]) -> list[dict[str, Any]]:
        value: list[dict[str, Any]] = await self.request('messages', account=account, peer=peer, ids=ids)
        return value

    async def download(self, account: str, peer: int, id: int) -> bytes:
        value: bytes = await self.request('download', account=account, peer=peer, id=id)
        return value

    async def read_state(self, account: str, peer: int, since: float) -> dict[str, Any]:
        value: dict[str, Any] = await self.request('read_state', account=account, peer=peer, since=since)
        return value

    async def read_window(self, account: str, peer: int, after: int, until: int) -> dict[str, Any]:
        value: dict[str, Any] = await self.request('read_window', account=account, peer=peer, after=after, until=until)
        return value

    async def acknowledge(self, account: str, peer: int, max_id: int) -> None:
        if max_id <= 0:
            raise SourceError('Неверная граница прочитанного Telegram')
        result = await self.request('acknowledge', account=account, peer=peer, max_id=max_id)
        if result is not True:
            # A repeated already-read boundary can return False. Verify the
            # server's actual frontier before treating it as failure.
            state = await self.read_state(account, peer, time.time())
            if state['read_max'] < max_id:
                raise SourceError('Telegram не подтвердил отметку прочитанного')

class _TelegramWorker(_TelegramOperations):
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
        observation.stage('Загрузка вложения' if operation == 'download' else 'Ожидание Telegram worker')
        output = args.pop('_output', None)
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
                        if output is not None:
                            size=0
                            with path.open('rb') as source:
                                while chunk:=source.read(1024*1024):
                                    size+=len(chunk)
                                    if size>200*1024*1024:
                                        raise SourceError('Вложение превышает лимит 200 МиБ',code='media-too-large',retryable=False)
                                    if output.write(chunk)!=len(chunk):
                                        raise OSError('Incomplete Telegram media output')
                                    await asyncio.sleep(0)
                            return size
                        return path.read_bytes()
                    finally:
                        path.unlink(missing_ok=True)
                return result['value']
            except SourceError as error:
                if error.remote_type != 'FloodWaitError':
                    await self.close()
                raise
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception:
                record_caught('source.request')
                await self.close()
                raise SourceError('Telegram-клиент недоступен') from None

    async def close(self) -> None:
        self.generation += 1
        process, self.process = self.process, None
        if process and process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        shutil.rmtree(self.spool, ignore_errors=True)


class LocalTelegram(_TelegramOperations):
    """Own one lazy pipe per verified person; configuration remains ClientRegistry's."""
    def __init__(self, env_file: Path, root: Path) -> None:
        self.env_file = (env_file / '.env' if env_file.is_dir() else env_file).resolve()
        self.root = root.resolve()
        self.allowed: Callable[[], bool] = lambda: True
        self.expected_users: dict[str, int] = {}
        self.generation = 0
        self.workers: dict[int, _TelegramWorker] = {}
        self._identity_lock = asyncio.Lock()
        self._account_locks: dict[str, asyncio.Lock] = {}

    async def accounts(self) -> list[str]:
        from oko.collection.telegram.client import ClientRegistry
        try:
            return ClientRegistry(self.env_file).accounts()
        except (ValueError, OSError):
            raise SourceError('Окружение Telegram-клиента недоступно') from None

    async def identity(self, account: str) -> int:
        async with self._account_locks.setdefault(account, asyncio.Lock()):
            user = self.expected_users.get(account)
            if user is not None and user in self.workers:
                return await self.workers[user].identity(account)
            # A probe releases its session before the collection worker starts.
            async with self._identity_lock:
                probe = _TelegramWorker(self.env_file, self.root / '_probe')
                probe.allowed = self.allowed
                try:
                    return await probe.identity(account)
                finally:
                    await probe.close()

    async def request(self, operation: str, **args: Any) -> Any:
        if operation == 'accounts':
            return await self.accounts()
        if operation == 'identity':
            return await self.identity(args['account'])
        account = args['account']
        user = self.expected_users.get(account)
        if user is None:
            user = await self.identity(account)
            self.expected_users[account] = user
        # A profile probe and collection must never open this session together.
        async with self._account_locks.setdefault(account, asyncio.Lock()):
            worker = self.workers.get(user)
            if worker is None:
                worker = _TelegramWorker(self.env_file, self.root)
                worker.spool = self.root / 'staging' / '_telegram' / str(user)
                self.workers[user] = worker
            worker.allowed = self.allowed
            worker.expected_users[account] = user
        return await worker.request(operation, **args)


    async def download_stream(self, account: str, peer: int, identity: int, output: BinaryIO, *, expected_identity: str | None = None) -> int:
        size: int=await self.request('download',account=account,peer=peer,id=identity,_output=output,expected_media_identity=expected_identity)
        return size

    async def close_account(self, account: str) -> None:
        user = self.expected_users.get(account)
        worker = self.workers.pop(user, None) if user is not None else None
        if worker:
            await worker.close()

    async def close(self) -> None:
        self.generation += 1
        workers, self.workers = self.workers, {}
        await asyncio.gather(*(worker.close() for worker in workers.values()))


class TelegramSource:
    changes = 'Telegram: при сборе перечитываются известные непросмотренные сообщения и вложения. Отсутствующие сообщения сохраняются локально; исчезновение может означать удаление или потерю доступа.'
    def __init__(self, client: TelegramClient | WebTelegram, account: str, peer: int, initial_days: int, username: str | None = None, *, http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.http_transport = client.transport if isinstance(client,WebTelegram) else http_transport
        self.adapter: Adapter = 'web' if isinstance(client, WebTelegram) else 'client'
        if isinstance(client, WebTelegram):
            self.changes = 'Telegram web: видимая публичная история; скрытые сообщения и неизвестная смена username могут быть не обнаружены.'
        self.identity_info: dict[str, Any] | None = None
        self.username = username
        self.client, self.account, self.peer, self.initial_days = client, account, peer, initial_days
        self.initial_history: dict[str, Any] | None = None
        self.more = False
        self.gap: str | None = None

    async def resolve(self) -> None:
        # StringSession does not persist Telethon's entity/access-hash cache.
        # Refill it in the current worker, but never change the feed's identity.
        if isinstance(self.client, WebTelegram):
            return
        info = await self.client.info(self.account, '@' + self.username if self.username else self.peer)
        self.identity_info = info
        if info['peer'] != self.peer:
            raise SourceError('Username Telegram теперь принадлежит другому источнику; проверьте привязку', code='source_identity_conflict')



    def publication_adapter(self) -> PublicationAdapter:
        raise NotImplementedError

    async def discover(self,cursor: str | None) -> DiscoveryResult:
        raise NotImplementedError

    async def fetch(self,publication: Publication) -> FetchResult:
        raise NotImplementedError

    def observation(self) -> SourceObservation:
        return SourceObservation(self.gap, self.changes, SourceIdentity.from_info(self.identity_info), (self.identity_info or {}).get('description'))
