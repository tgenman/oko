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
import os
import sys
import time
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from oko.collection.contracts import SourceError, SourceDeleted, DiscoveryResult, FetchResources, FetchResult, FetchRequirements, SourceObservation, SourceIdentity
from oko.collection.contracts import Attachment, Material, Publication, DownloadStage, StagedPayload, SourceManifest, ResourceManifest
from oko.collection.telegram.contracts import HistoryEnvelope, Adapter
from oko.collection.telegram.web import WebTelegram
from oko.collection.telegram.telegraph import enrich
import httpx


class TelegramClient(Protocol):
    async def identity(self, account: str) -> int: ...
    async def accounts(self) -> list[str]: ...
    async def info(self, account: str, peer: str | int) -> dict[str, Any]: ...
    async def page(self, account: str, peer: int, after: int, since: float) -> dict[str, Any]: ...
    async def history_page(self, account: str, peer: int, upper: int, before: int, since: float) -> dict[str, Any]: ...
    async def messages(self, account: str, peer: int, ids: list[int]) -> list[dict[str, Any]]: ...
    async def download(self, account: str, peer: int, id: int) -> bytes: ...
    async def read_state(self, account: str, peer: int, since: float, topic_id: int | None = None) -> dict[str, Any]: ...
    async def read_window(self, account: str, peer: int, after: int, until: int, topic_id: int | None = None) -> dict[str, Any]: ...
    async def acknowledge(self, account: str, peer: int, max_id: int, topic_id: int | None = None) -> None: ...
    async def close(self) -> None: ...


class TelegramPeer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    account: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    peer: str = Field(min_length=1, max_length=100, pattern=r'^(@?[a-zA-Z][a-zA-Z0-9_]{3,31}|-[0-9]+)$')


class ConnectTelegram(TelegramPeer):
    folder: str = Field(min_length=1, max_length=200)
    feed_id: str | None = None
    auto_read: bool
    initial_history: InitialHistory | None = None


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
        target = args.pop('_target', None)
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
                        if target is not None:
                            if not isinstance(target,Path) or target.parent.parent.resolve()!=(self.root/'staging'/'_downloads').resolve() or not target.name.startswith('incoming-media-'):
                                raise ValueError('unowned client download target')
                            os.replace(path,target)
                            payload_digest = hashlib.sha256()
                            with target.open('rb') as source:
                                while part := source.read(1024*1024):
                                    payload_digest.update(part)
                            return StagedPayload(target,target.stat().st_size,payload_digest.hexdigest())
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

    async def download_to(self, account: str, peer: int, identity: int, staging: DownloadStage, name: str) -> StagedPayload:
        if not staging.admitted():
            raise asyncio.CancelledError
        payload: StagedPayload = await self.request('download',account=account,peer=peer,id=identity,_target=staging.path/('incoming-'+name))
        return payload

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
    fetch_requirements = FetchRequirements(staging=True)
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
        self.retry_error: SourceError | None = None
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

    def url(self, id: int) -> str:
        return f'https://t.me/c/{-self.peer-1000000000000}/{id}' if self.peer < -1000000000000 else f'tg://openmessage?chat_id={-self.peer}&message_id={id}'

    async def discover(self, cursor: str | None) -> DiscoveryResult:
        previous = HistoryEnvelope.decode(cursor, adapter='web' if isinstance(self.client, WebTelegram) else 'client') if cursor else None
        if previous:
            after = previous.position.get('after')
            if previous.selection.since is None or type(after) is not int or after<0:
                raise SourceError('Несовместимый курсор Telegram',code='telegram-cursor-incompatible')
        legacy = json.dumps(previous.progress_state()) if previous else None
        publications, raw = await self._discover(legacy)
        if isinstance(self.client, WebTelegram):
            self.identity_info = self.client.identity_info
        envelope = HistoryEnvelope.from_progress(json.loads(raw), adapter=self.adapter)
        selection = envelope.selection.model_copy(update={'paused': bool((self.initial_history or {}).get('paused'))})
        return DiscoveryResult(tuple(publications), envelope.model_copy(update={'selection': selection}).encode(),
            self.observation(), (.1 if self.initial_history else 0) if self.more else None, self.retry_error)

    def observation(self) -> SourceObservation:
        return SourceObservation(self.gap, self.changes, SourceIdentity.from_info(self.identity_info), (self.identity_info or {}).get('description'))

    async def _page(self, state: dict[str, Any]) -> dict[str, Any]:
        if isinstance(self.client, WebTelegram):
            batch = await self.client.page(self.account, self.peer, state['after'], state['since'], position=state.get('web'))
            state['web'] = batch['position']
            return batch
        return await self.client.page(self.account, self.peer, state['after'], state['since'])

    def _after(self, state: dict[str, Any], batch: dict[str, Any], publications: list[Publication]) -> int:
        if isinstance(self.client, WebTelegram):
            return int(batch['next_after'])
        return int(max([state['after'], batch.get('next_after', 0)] + [m for p in publications for m in p.members]))

    async def _discover(self, cursor: str | None) -> tuple[list[Publication], str]:
        self.retry_error = None
        await self.resolve()
        if self.initial_history:
            return await self.discover_history(cursor)
        state: dict[str, Any] = json.loads(cursor) if cursor else dict(after=0, since=time.time()-self.initial_days*86400)
        batch = await self._page(state)
        self.more, self.gap = batch['more'], batch.get('gap')
        dates = {member:m['date'] for m in batch['messages'] for member in (m['post'].publication.members if 'post' in m else [m['id']])}
        publications = [p for p in reversed(self.publications(batch['messages']))
                        if max(p.members) > state['after'] and max(dates[m] for m in p.members) >= state['since']]
        state['after'] = self._after(state, batch, publications)
        if self.more and not isinstance(self.client, WebTelegram) and state['after'] == (json.loads(cursor)['after'] if cursor else 0):
            raise SourceError('Telegram не продвинул позицию сбора')
        return publications, json.dumps(state)

    def publications(self, rows: list[dict[str, Any]], posts_only: bool = False) -> list[Publication]:
        if isinstance(self.client, WebTelegram):
            return sorted((m['post'].publication for m in rows), key=lambda p:max(p.members), reverse=True)
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
                          Material(text.split('\n')[0] or 'Telegram · медиа', text, False),
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
            batch = await self.client.history_page(self.account, self.peer, 0, 0, state['since'])
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
                batch = await self.client.history_page(self.account, self.peer, state['upper'], state['before'], state['since'])
            except SourceError as error:
                if not state['upper']:
                    raise
                self.retry_error = error
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
            batch = await self._page(state)
            publications = [p for p in self.publications(batch['messages']) if max(p.members) > state['after']]
            state['after'] = self._after(state, batch, publications)
            state['lane'] = 'live' if batch['more'] else 'history'
        self.more = bool(batch.get('more')) or (not state['scan_complete'] and not config.get('paused'))
        self.gap = batch.get('gap')
        return publications, json.dumps(state)

    async def fetch(self, publication: Publication, resources: FetchResources = FetchResources()) -> FetchResult:
        staging = resources.staging
        if staging and not staging.admitted():
            raise asyncio.CancelledError
        material = (await self.client.fetch(publication,staging) if isinstance(self.client,WebTelegram)
                    else await self._fetch_client(publication,staging))
        if staging and not staging.admitted():
            raise asyncio.CancelledError
        return FetchResult(await enrich(material,publication.teaser,staging,self.http_transport), self.observation())

    async def _fetch_client(self, publication: Publication, staging: DownloadStage | None) -> Material:
        if isinstance(self.client,WebTelegram):
            raise ValueError('client adapter required')
        await self.resolve()
        rows = await self.client.messages(self.account, self.peer, list(publication.members) or [int(publication.id)])
        if staging and not staging.admitted():
            raise asyncio.CancelledError
        rows.sort(key=lambda row:row['id'])
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
            if staging and not staging.admitted():
                raise asyncio.CancelledError
            media = msg['media']
            if not media:
                continue
            if media['kind'] == 'unsupported':
                reasons.append('Тип вложения пока не поддерживается')
                continue
            try:
                name = f"media-{msg['id']}.{media['extension']}"
                data: bytes | StagedPayload = (await self.client.download_to(self.account,self.peer,msg['id'],staging,name)
                    if staging and isinstance(self.client,LocalTelegram) else await self.client.download(self.account, self.peer, msg['id']))
                if not data:
                    raise SourceError('Пустое вложение Telegram')
                attachments.append(Attachment(f"media-{msg['id']}.{media['extension']}", data, media['kind'], media['mime']))
            except SourceError as error:
                record_caught('source.fetch')
                reasons.append(error.label)
                if isinstance(error,SourceDeleted) or error.remote_type in ('OverflowError','FileNotFoundError'):
                    error = SourceError(error.label,code='media-too-large' if error.remote_type=='OverflowError' else 'media-not-found',retryable=False)
                if collection_error is None or not error.retryable:
                    collection_error = error
        names = {a.name for a in attachments}
        failed_names = {f"media-{m['id']}.{m['media']['extension']}" for m in rows if m['media'] and m['media']['kind'] != 'unsupported'}
        expected_names = failed_names | {f"media-{member}." for member in set(publication.members)-{m['id'] for m in rows}}
        attachments.extend(a for a in teaser.attachments if a.name not in names and (a.name in failed_names or any(a.name.startswith(prefix) for prefix in expected_names if prefix.endswith('.'))))
        if any(msg['group'] and time.time()-msg['date'] < 5 for msg in rows):
            reasons.append('Альбом ещё публикуется; повторите загрузку')
            if collection_error is None or collection_error.retryable:
                collection_error = SourceError('Альбом ещё публикуется', 5)
        by_name = {attachment.name:attachment for attachment in attachments}
        resources: list[ResourceManifest] = []
        for msg in rows:
            media = msg['media']
            if not media:
                continue
            name = f"media-{msg['id']}.{media['extension']}"
            attachment = by_name.get(name)
            fingerprint = (attachment.data.sha256 if isinstance(attachment.data,StagedPayload) else hashlib.sha256(attachment.data).hexdigest()) if attachment else None
            resources.append(ResourceManifest(key='telegram-client:'+str(self.peer)+':'+str(msg['id']),
                origin='telegram:'+str(self.peer)+'/'+str(msg['id']),order=len(resources),name=name,
                kind=media['kind'],mime=media['mime'],status='loaded' if attachment else 'unsupported' if media['kind']=='unsupported' else 'missing',
                reason='' if attachment else '; '.join(dict.fromkeys(reasons)),sha256=fingerprint))
        manifest = SourceManifest(source_url=self.url(min(m['id'] for m in rows)),post=dict(text=text,baseline_text='\n\n'.join(m.get('original_text',m['text']) for m in rows if m.get('original_text',m['text'])),edits=[dict(id=m['id'],at=m.get('edited')) for m in rows],members=sorted({m['id'] for m in rows}|set(publication.members)),
            resources=[dict(key=r.key,kind=r.kind) for r in resources]),resources=tuple(resources))
        complete = not reasons
        same_source = teaser.source_manifest.post==manifest.post if teaser.source_manifest else teaser.text==text
        if teaser.full and same_source and all(resource.status=='loaded' for resource in resources) and not any(msg['group'] and time.time()-msg['date']<5 for msg in rows):
            complete = True
        return Material(text.split('\n')[0] or 'Telegram · медиа', text, complete,
                        '; '.join(dict.fromkeys(reasons)), attachments=tuple(attachments), members=tuple(sorted({m['id'] for m in rows} | set(publication.members))), collection_error=collection_error, source_manifest=manifest)
