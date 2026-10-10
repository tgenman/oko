"""Reader actions and owned processing lifecycle behind one module interface.

Store remains responsible for durable identity/content/recovery. Network waits
happen outside the action lock; admission, transitions and result commits share it.
"""
import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Any, Literal, TypeVar

import httpx

from oko.collection.rss import RSSSource
from oko.collection.contracts import SourceError
from oko.collection.contracts import Source
from oko.processing.llamacpp import LlamaCpp, ProcessorConfig
from oko.processing.contracts import ProcessingError, ProcessingRequest, EventsResult, FilterResult, Theses
from oko.processing.codex import Codex, CodexClient, LocalCodex, status as codex_status
from oko.reviews.digests import DigestRequest
from oko.reader.composition import FolderContents
from oko.storage.store import Job, Store
from oko.collection.telegram.source import LocalTelegram, TelegramClient, TelegramSource, ConnectTelegram
from oko.subscriptions.subscriptions import ImportRequest, Overrides, FeedPatch
from oko.collection.youtube.source import YouTubeSource, CaptionsClient, LocalCaptions

T = TypeVar('T')


class ActionError(Exception):
    def __init__(self, kind: Literal['conflict', 'invalid', 'missing', 'unavailable'], detail: str | dict[str, Any]) -> None:
        self.kind, self.detail = kind, detail
        super().__init__(str(detail))


class Runtime:
    def __init__(self, root: Path, source: Source | None, clock: Callable[[], float],
                 fault: Callable[[str], None], shutdown: Callable[[], None],
                 lease: float, grace: float, source_feed: str, rss_enabled: bool, rss_transport: httpx.AsyncBaseTransport | None, telegram_client: TelegramClient | None, ai_transport: httpx.AsyncBaseTransport | None = None, codex_client: CodexClient | None = None, youtube_captions: CaptionsClient | None = None) -> None:
        self._youtube_captions = youtube_captions or LocalCaptions()
        self._codex_client = codex_client or LocalCodex()
        self._codex_status = codex_status()
        self._ai_transport = ai_transport
        self._ai: asyncio.Task[None] | None = None
        self._ai_sync = 0.0
        self._ai_stage = 'Проверка обработчика'
        self._root, self._source, self._clock, self._fault = root, source, clock, fault
        self._shutdown, self._lease, self._grace = shutdown, lease, grace
        self._source_feed = source_feed
        self._rss_enabled, self._rss_transport = rss_enabled, rss_transport
        self._telegram_client = telegram_client
        self._sources: dict[str, Source] = {source_feed: source} if source else {}
        self._last_collection = clock()
        self._tabs: dict[str, float] = {}
        self._completions: dict[str, tuple[str, FolderContents | None]] = {}
        self._stop_requested = asyncio.Event()
        self._closed = False
        self._actions = asyncio.Lock()
        self._epoch = 0
        self._requests: set[asyncio.Future[Any]] = set()
        self._idle_since: float | None = None
        self._store: Store | None = None
        self._error: str | None = None
        self._active: asyncio.Task[None] | None = None
        self._cancelling = 0
        self._detached: set[asyncio.Task[None]] = set()
        self._detached_ai: set[asyncio.Task[None]] = set()
        self._previous_tick = clock()
        self._previous_wall = time.time()
        if isinstance(telegram_client, LocalTelegram):
            telegram_client.allowed = lambda: self._live() and not self._cancelling

    def _heartbeat(self, tab: str) -> None:
        if self.stopped:
            raise ActionError('conflict', 'Око остановлено')
        self._tabs[tab] = self._clock()
        self._idle_since = None

    def _live(self) -> bool:
        now = self._clock()
        self._tabs = {t: stamp for t, stamp in self._tabs.items() if now-stamp < self._lease}
        return bool(self._tabs) and not self.stopped

    async def _cancel(self) -> None:
        self._cancelling += 1
        self._epoch += 1
        for request in tuple(self._requests):
            request.cancel()
        try:
            # Invalidate before waiting: even an adapter suppressing cancellation may
            # not publish its old generation when another tab is still alive.
            if self._store:
                self._store.interrupted()
            ai_task = self._ai
            tasks = [t for t in (self._active, self._ai) if t]
            self._active = self._ai = None
            for task in tasks:
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=.25)
                if task in done:
                    if not task.cancelled():
                        task.exception()
                else:
                    self._detached.add(task)
                    if task is ai_task:
                        self._detached_ai.add(task)

                    def completed(finished: asyncio.Task[None]) -> None:
                        self._detached.discard(finished)
                        self._detached_ai.discard(finished)
                        if not finished.cancelled():
                            finished.exception()

                    task.add_done_callback(completed)
            if self._telegram_client:
                await self._telegram_client.close()
        finally:
            self._cancelling -= 1

    def _sync_sources(self) -> None:
        if not self._store:
            return
        for feed in self._store.subscriptions.feeds():
            if feed['id'] == self._source_feed and self._source:
                continue
            if feed['transport'] == 'youtube' and self._rss_enabled:
                if not isinstance(self._sources.get(feed['id']), YouTubeSource):
                    self._sources[feed['id']] = YouTubeSource(feed['original_urls'][0], feed['initial_days'], self._youtube_captions, self._rss_transport)
                    self._store.enqueue_discovery(feed['id'])
                youtube = self._sources[feed['id']]
                if isinstance(youtube, YouTubeSource):
                    youtube.initial_days = feed['initial_days']
                    youtube.want_captions = self._store.subscriptions.youtube_theses(feed['id'])
                    self._store.prepare_youtube(feed['id'], youtube.want_captions)
                continue
            if feed['transport'] == 'telegram' and self._telegram_client and feed.get('telegram_peer'):
                if not isinstance(self._sources.get(feed['id']), TelegramSource):
                    self._sources[feed['id']] = TelegramSource(self._telegram_client, feed['telegram_account'], feed['telegram_peer'], feed['initial_days'], feed.get('telegram_username'))
                    self._store.enqueue_discovery(feed['id'])
                telegram_source = self._sources[feed['id']]
                if isinstance(telegram_source, TelegramSource):
                    telegram_source.initial_days = feed['initial_days']
                continue
            if feed['transport'] != 'rss' or not self._rss_enabled:
                continue
            if not isinstance(self._sources.get(feed['id']), RSSSource):
                self._sources[feed['id']] = RSSSource(feed['original_urls'][0], feed['initial_days'], self._rss_transport)
                self._store.enqueue_discovery(feed['id'])
            source = self._sources[feed['id']]
            if isinstance(source, (RSSSource, TelegramSource)):
                source.initial_days = feed['initial_days']
        active = {f['id'] for f in self._store.subscriptions.feeds() if (f['transport'] in ('rss', 'youtube') and self._rss_enabled) or (f['transport'] == 'telegram' and self._telegram_client and f.get('telegram_peer'))}
        self._sources = {key: source for key, source in self._sources.items() if key in active or key == self._source_feed and self._source}

    @property
    def stopped(self) -> bool:
        return self._stop_requested.is_set()

    def require_store(self) -> Store:
        """Existing read interface; callers do not own its lifetime."""
        if self.stopped:
            raise ActionError('conflict', 'Око остановлено')
        if self._error or self._store is None:
            raise ActionError('unavailable', self._error or 'Хранилище не готово')
        return self._store

    def status(self) -> dict[str, Any]:
        state: dict[str, Any] = self._store.status() if self._store and not self.stopped else dict(jobs=[], errors=[], deleting=False)
        pending = any(j['status'] in ('pending', 'running') for j in state['jobs'])
        source_states = self._store.source_statuses() if self._store else []
        names = {f['id']: f['title'] for f in self._store.subscriptions.feeds()} if self._store else {}
        source_states = [dict(**s, title=names.get(s['feed_id'], s['feed_id'])) for s in source_states if s['feed_id'] in self._sources]
        if self.stopped:
            stage = 'Остановлено'
        elif self._error:
            stage = 'Ошибка хранилища'
        elif state['deleting']:
            stage = 'Удаление'
        elif any(j['kind'] in ('theses', 'filter', 'events', 'digest') and j['status'] == 'running' for j in state['jobs']):
            stage = self._ai_stage
        elif any(j['kind'] in ('theses', 'filter', 'events', 'digest') and j['status'] == 'pending' for j in state['jobs']) and not self._sources:
            stage = 'Ожидание обработчика' if state['errors'] else 'Подготовка тезисов'
        elif pending and not self._sources:
            stage = 'Источник не подключён'
        elif pending and state['errors']:
            stage = 'Сбор с ошибками' if self._active and not self._active.done() else 'Ожидание повтора'
        elif pending:
            stage = 'Сбор'
        else:
            stage = 'Завершено с ошибками' if state['errors'] else 'Готово'
        return dict(**state, sources=source_states, error=self._error, stopped=self.stopped,
                    live_tabs=len(self._tabs), stage=stage,
                    ready_sources=self._store.ready_sources(list(self._sources)) if self._store and not self.stopped else 0,
                    total_sources=len(self._sources))

    async def heartbeat(self, tab: str) -> None:
        async with self._actions:
            self._heartbeat(tab)

    async def close_tab(self, tab: str) -> None:
        async with self._actions:
            self._tabs.pop(tab, None)
            if not self._live() and not self.stopped:
                await self._cancel()
                if self._idle_since is None:
                    self._idle_since = self._clock()

    async def stop(self) -> None:
        # Stop intent closes admission immediately, even with queued actions.
        # The owned cleanup and storage close still run in serial order.
        self._stop_requested.set()
        async with self._actions:
            if not self._closed:
                await self._cancel()

    async def collect(self) -> None:
        async with self._actions:
            current = self.require_store()
            self._sync_sources()
            for feed in self._sources:
                current.enqueue_discovery(feed)

    async def retry_material(self, item: str) -> None:
        async with self._actions:
            if not self.require_store().retry_material(item):
                raise ActionError('missing', 'Материал недоступен')

    async def create_digest(self, scope: str, owner: str, body: DigestRequest) -> str:
        async with self._actions:
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель')
            try:
                return current.digests.create(scope, owner, body, time.time())
            except LookupError as error:
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                raise ActionError('invalid', str(error)) from None

    async def viewed(self, item: str) -> None:
        async with self._actions:
            current = self.require_store()
            if not current.viewed(item):
                raise ActionError('missing', 'Материал не найден')
            await self._drain_deletions(current)

    def _completion_preview(self, folder: str, current: Store) -> dict[str, Any]:
        try:
            contents = current.folder_contents(folder)
        except LookupError as error:
            raise ActionError('missing', str(error)) from None
        token = secrets.token_urlsafe(32)
        # Confirmation handles contain only service IDs, never publication text.
        # Bound their lifetime to this run and their number to 128 previews.
        if len(self._completions) >= 128:
            del self._completions[next(iter(self._completions))]
        self._completions[token] = (folder, contents)
        return dict(token=token, cards=len(contents.cards), originals=len(contents.members))

    async def preview_completion(self, folder: str) -> dict[str, Any]:
        async with self._actions:
            return self._completion_preview(folder, self.require_store())

    async def complete_folder(self, folder: str, token: str) -> None:
        async with self._actions:
            current = self.require_store()
            saved = self._completions.get(token)
            if not saved or saved[0] != folder:
                raise ActionError('conflict', dict(message='Подтверждение устарело. Проверьте количество снова.', preview=self._completion_preview(folder, current)))
            contents = saved[1]
            if contents is not None:
                try:
                    latest = current.folder_contents(folder)
                except LookupError as error:
                    raise ActionError('missing', str(error)) from None
                if latest != contents:
                    raise ActionError('conflict', dict(message='Состав папки изменился. Подтвердите новое количество.', preview=self._completion_preview(folder, current)))
                current.viewed_set([identity for identity, _ in contents.members])
                self._completions[token] = (folder, None)
            await self._drain_deletions(current)
            if current.status()['deleting']:
                raise ActionError('unavailable', 'Удаление не завершено. Око повторит попытку; ошибка видна в статусе.')

    async def _drain_deletions(self, current: Store) -> None:
        if self._active or self._ai or self._requests:
            await self._cancel()
        job = current.next_job(self._clock(), deleting_only=True)
        while job:
            self._delete(job, current)
            job = current.next_job(self._clock(), deleting_only=True)

    async def patch_feed(self, identity: str, body: FeedPatch) -> dict[str, Any]:
        async with self._actions:
            current = self.require_store()
            try:
                result = current.subscriptions.update_feed(identity, body)
            except LookupError as error:
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                raise ActionError('invalid', str(error)) from None
            current.settings_changed()
            await self._cancel()
            if 'transport' in body.model_fields_set:
                self._sync_sources()
            return result

    async def import_subscriptions(self, body: ImportRequest) -> dict[str, Any]:
        async with self._actions:
            try:
                result = self.require_store().subscriptions.import_opml(body)
            except ValueError as error:
                raise ActionError('invalid', str(error)) from None
            if 'conflicts' in result:
                raise ActionError('conflict', result)
            return result

    async def update_settings(self, scope: str, owner: str, body: Overrides) -> dict[str, Any]:
        async with self._actions:
            try:
                current = self.require_store()
                result = current.subscriptions.update_settings(scope, owner, body)
                current.settings_changed()
                await self._cancel()
                return result
            except LookupError as error:
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                raise ActionError('invalid', str(error)) from None

    async def configure_processor(self, config: ProcessorConfig) -> None:
        async with self._actions:
            self.require_store().configure_processor(config)
            await self._cancel()

    async def check_processor(self) -> dict[str, Any]:
        async with self._actions:
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для подключения')
            config = current.processor_config()
            if not config:
                raise ActionError('conflict', 'llama.cpp не настроен')
            epoch = self._epoch
            task = asyncio.create_task(LlamaCpp(config, self._ai_transport).check())
            self._requests.add(task)
        try:
            result = await task
        except ProcessingError as error:
            raise ActionError('conflict', error.label) from None
        except asyncio.CancelledError:
            raise ActionError('conflict', 'Проверка прервана') from None
        finally:
            self._requests.discard(task)
        async with self._actions:
            self._check_request(current, epoch)
        return result

    def codex_status(self) -> dict[str, Any]:
        self.require_store()
        return self._codex_status.copy()

    async def check_codex(self) -> dict[str, Any]:
        async with self._actions:
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для подключения')
            epoch = self._epoch
            task = asyncio.create_task(self._codex_client.check())
            self._requests.add(task)
        try:
            result = await task
        except asyncio.CancelledError:
            raise ActionError('conflict', 'Проверка Codex прервана') from None
        finally:
            self._requests.discard(task)
        async with self._actions:
            self._check_request(current, epoch)
            self._codex_status = result
        return result

    async def restore_filter(self, item: str) -> None:
        async with self._actions:
            if not self.require_store().filters.restore(item):
                raise ActionError('missing', 'Обработанный материал недоступен')
            await self._cancel()

    async def retry_filter(self, item: str) -> None:
        async with self._actions:
            if not self.require_store().filters.retry(item):
                raise ActionError('missing', 'Фильтрация не включена')
            await self._cancel()

    async def retry_theses(self, item: str) -> None:
        async with self._actions:
            if not self.require_store().retry_theses(item):
                raise ActionError('missing', 'Тезисы для материала не включены')

    async def _execute_ai(self, job: Job) -> None:
        current: Store | None = None
        try:
            async with self._actions:
                current = self._store
                if not current or not current.valid(job) or not self._live():
                    return
                policy = None if job.kind in ('events', 'digest') else current.processing_policy(job.item)
                if policy and policy[0] == 'codex':
                    if not policy[1]:
                        raise ProcessingError('Запрещена публичная AI-обработка; выберите локальную модель')

                operations: dict[str, tuple[
                    Callable[[Job], tuple[ProcessingRequest, str, bool] | None],
                    Callable[[str], str | None], str,
                    Callable[[Job, str, Theses | FilterResult | EventsResult], None]]] = {
                    'digest': (current.digests.request, current.digests.signature, 'Подготовка обзора',
                               lambda j, s, r: current.digests.save(j, s, Theses.model_validate(r.model_dump()))),
                    'events': (current.events.request, current.events.signature, 'Объединение событий',
                               lambda j, s, r: current.events.save(j, s, EventsResult.model_validate(r.model_dump()))),
                    'filter': (current.filters.request, current.filters.signature, 'Фильтрация',
                               lambda j, s, r: current.filters.save(j, s, FilterResult.model_validate(r.model_dump()))),
                    'theses': (current.processing_request, current.thesis_signature, 'Подготовка тезисов',
                               lambda j, s, r: current.save_theses(j, s, Theses.model_validate(r.model_dump()))),
                }
                prepare, input_signature, stage, save = operations[job.kind]
                prepared = prepare(job)
                if prepared and prepared[0].processor == 'codex' and not prepared[2]:
                    raise ProcessingError('Запрещена публичная AI-обработка набора; разрешение требуется от всех фидов')
                config = current.processor_config()
                if not prepared:
                    raise ProcessingError('Исходник недоступен для обработки')
                request, signature, public_ai = prepared
                if job.kind == 'digest' and not request.sources:
                    current.digests.save(job, signature, None)
                    return
                adapter: Codex | LlamaCpp
                if request.processor == 'codex':
                    adapter = Codex(self._codex_client)
                elif request.processor in ('llamacpp', 'llamacpp:' + (config.model if config else '')) and config:
                    adapter = LlamaCpp(config, self._ai_transport)
                else:
                    raise ProcessingError('Ожидание выбранного обработчика; llama.cpp не настроен или не выбран')
            self._ai_stage = 'Проверка обработчика'
            checked = await adapter.check()
            async with self._actions:
                if self._store is not current or not self._live() or not current.valid(job) or signature != input_signature(job.item):
                    return
                if not checked['local'] and not public_ai:
                    raise ProcessingError('Запрещена публичная AI-обработка; выберите локальную модель')
            self._ai_stage = stage
            result = (await adapter.generate(request, self._root / 'staging' / job.item)
                      if isinstance(adapter, Codex) else await adapter.generate(request))
            async with self._actions:
                if self._store is current and self._live():
                    save(job, signature, result)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            async with self._actions:
                if current and self._store is current and not self.stopped and current.valid(job):
                    current.retry(job, self._clock(), error.label if isinstance(error, ProcessingError) else 'Ошибка AI-обработки', 30)
        finally:
            if current and job.kind == 'digest':
                current.digests.release(job)

    def _telegram_ready(self) -> tuple[Store, TelegramClient]:
        current = self.require_store()
        if not self._live():
            raise ActionError('conflict', 'Откройте читатель для подключения')
        if not self._telegram_client:
            raise ActionError('conflict', 'Telegram-клиент не настроен')
        return current, self._telegram_client

    def _check_request(self, current: Store, epoch: int) -> None:
        if self.stopped or self._store is not current or not self._live():
            raise ActionError('conflict', 'Око остановлено')
        if epoch != self._epoch:
            raise ActionError('conflict', 'Проверка подключения прервана; повторите действие')

    async def _call_telegram(self, call: Callable[[TelegramClient], Awaitable[T]]) -> tuple[T, Store, int]:
        async with self._actions:
            current, client = self._telegram_ready()
            epoch = self._epoch
            task = asyncio.ensure_future(call(client))
            self._requests.add(task)
        try:
            # Network work is deliberately outside the mutation seam.
            result = await task
        except SourceError as error:
            raise ActionError('conflict', error.label) from None
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller and caller.cancelling():
                raise
            raise ActionError('conflict', 'Око остановлено' if self.stopped else 'Подключение Telegram прервано; повторите действие') from None
        finally:
            self._requests.discard(task)
        async with self._actions:
            self._check_request(current, epoch)
        return result, current, epoch

    async def telegram_accounts(self) -> list[str]:
        result, _, _ = await self._call_telegram(lambda client: client.accounts())
        return result

    async def connect_telegram(self, body: ConnectTelegram) -> dict[str, Any]:
        info, current, epoch = await self._call_telegram(
            lambda client: client.info(body.account, int(body.peer) if body.peer.startswith('-') else body.peer))
        async with self._actions:
            self._check_request(current, epoch)
            if info.get('forum'):
                raise ActionError('conflict', 'Forum topics пока не поддерживаются')
            if info['kind'] not in ('channel', 'group'):
                raise ActionError('invalid', 'Поддерживаются каналы и обычные группы')
            try:
                result = current.subscriptions.connect_telegram(body, info)
            except (LookupError, ValueError) as error:
                raise ActionError('invalid', str(error)) from None
            await self._cancel()
            self._sources.pop(result['id'], None)
            self._sync_sources()
            return result

    def _delete(self, job: Job, current: Store) -> None:
        try:
            current.delete(job)
        except Exception:
            if current.valid(job):
                current.retry(job, self._clock(), 'Ошибка удаления')

    async def _execute(self, job: Job) -> None:
        current: Store | None = None
        feed = ''
        try:
            async with self._actions:
                current = self._store
                if not current or not current.valid(job) or not self._live():
                    return
                if job.kind == 'delete':
                    self._delete(job, current)
                    return
                feed = (job.item or 'demo') if job.kind == 'discover' else current.item_feed(job.item)
                source = self._sources.get(feed)
                publication = current.publication(job.item) if job.kind == 'fetch' else None
                namespace = 'telegram' if isinstance(source, TelegramSource) else ''
                cursor = current.cursor(feed, namespace) if job.kind == 'discover' else None
            if not source:
                return
            if job.kind == 'discover':
                items, next_cursor = await source.discover(cursor)
                async with self._actions:
                    if self._store is current and self._live() and current.valid(job):
                        current.discovered(items, next_cursor, job, feed, namespace)
                        if getattr(source, 'more', False):
                            current.enqueue_discovery(feed)
                        current.feed_status(feed, error=None, gap=getattr(source, 'gap', None))
            elif publication:
                if isinstance(source, (RSSSource, TelegramSource)) and bool(publication.members) != isinstance(source, TelegramSource):
                    raise SourceError('Материал получен другим способом; верните исходный транспорт для загрузки')
                material = await source.fetch(publication)
                async with self._actions:
                    if self._store is current and self._live() and current.valid(job):
                        current.save(job, material)
                        current.feed_status(feed, error=None, gap=getattr(source, 'gap', None))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            async with self._actions:
                if current and self._store is current and not self.stopped and current.valid(job):
                    label = error.label if isinstance(error, SourceError) else 'Ошибка сбора'
                    current.retry(job, self._clock(), label, error.delay if isinstance(error, SourceError) else 1)
                    if isinstance(error, SourceError):
                        current.feed_status(feed, error=label, gap=None)

    async def run(self) -> None:
        try:
            self._store = Store(self._root, self._fault)
            config = self._store.subscriptions
            if (self._source and self._source_feed == 'demo') or any(c['feed_id'] == 'demo' for c in self._store.cards()):
                config.ensure_demo()
            if self._source:
                config.check_owner('feed', self._source_feed)
                self._store.enqueue_discovery(self._source_feed)
        except (sqlite3.DatabaseError, OSError):
            self._error = 'Хранилище недоступно или повреждено. Состояние сохранено; сбор остановлен.'
        self._idle_since = self._clock()
        try:
            while not self.stopped:
                async with self._actions:
                    now, wall = self._clock(), time.time()
                    if max(now-self._previous_tick, wall-self._previous_wall) > self._lease:
                        self._tabs.clear()
                    self._previous_tick, self._previous_wall = now, wall
                    if not self._live():
                        await self._cancel()
                        if self._idle_since is None:
                            self._idle_since = now
                        if now-self._idle_since >= self._grace:
                            self._stop_requested.set()
                            break
                    elif self._store:
                        self._idle_since = None
                        if now - self._ai_sync >= .5:
                            self._store.sync_theses()
                            self._store.digests.schedule(wall)
                            self._ai_sync = now
                        if self._ai and self._ai.done():
                            await self._ai
                            self._ai = None
                        if self._ai is None and not self._detached_ai and not self._store.status()['deleting']:
                            ai_job = self._store.next_job(now, ai_only=True)
                            if ai_job:
                                self._ai = asyncio.create_task(self._execute_ai(ai_job))
                        if self._active and self._active.done():
                            await self._active
                            self._active = None
                        if self._active is None:
                            state = self._store.status()
                            self._sync_sources()
                            if now-self._last_collection >= 300:
                                for feed in self._sources:
                                    self._store.enqueue_discovery(feed)
                                self._last_collection = now
                            job = self._store.next_job(now, deleting_only=state['deleting'] or not self._sources, feeds=list(self._sources))
                            if job:
                                self._active = asyncio.create_task(self._execute(job))
                await asyncio.sleep(.02)
        finally:
            self._stop_requested.set()
            async with self._actions:
                await self._cancel()
                if self._store:
                    self._store.db.close()
                    self._store = None
                self._closed = True
            self._shutdown()
