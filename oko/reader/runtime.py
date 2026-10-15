"""Reader actions and owned processing lifecycle behind one module interface.

Store remains responsible for durable identity/content/recovery. Network waits
happen outside the action lock; admission, transitions and result commits share it.
"""
import asyncio
from oko.reviews.coordination import ProcessingWait
from collections.abc import Awaitable, Callable
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Any, Literal, TypeVar

import httpx

from oko.collection.rss import RSSSource, feed_title, normalized
from oko.collection.contracts import SourceError, SourceDeleted, SourceSkipped
from oko.collection.contracts import Source
from oko.processing.llamacpp import LlamaCpp, ProcessorConfig
from oko.processing.contracts import ProcessingError
from oko.processing.execution import CodexSettings
from oko.processing.codex import Codex, CodexClient, LocalCodex, status as codex_status
from oko.reviews.coordination import PreparedWork
from oko.reader.composition import FolderContents
from oko.storage.store import Job, Store
from oko.collection.telegram.source import LocalTelegram, TelegramClient, TelegramSource, ConnectTelegram, TelegramPeer
from oko.subscriptions.subscriptions import AddFeed, FeedURL, ImportRequest, Overrides, FeedPatch, identify
from oko.collection.youtube.source import YouTubeSource, CaptionsClient, LocalCaptions
from oko.collection.youtube.history import HistoryClient
from oko.subscriptions.history import InitialHistory
from oko.collection.youtube.access import CaptionsBlocked, CaptionsDeferred, proxy_url
from oko.diagnostics import Journal, active_journal, diagnostic_source, diagnostic_materials, capture_scope, record_caught

T = TypeVar('T')


class ActionError(Exception):
    def __init__(self, kind: Literal['conflict', 'invalid', 'missing', 'unavailable'], detail: str | dict[str, Any], *, expected: bool = False) -> None:
        self.kind, self.detail = kind, detail
        self.diagnostic_skip = expected
        super().__init__(str(detail))


class Runtime:
    def __init__(self, root: Path, source: Source | None, clock: Callable[[], float],
                 fault: Callable[[str], None], shutdown: Callable[[], None],
                 admitted: Callable[[], bool], should_stop: Callable[[], bool], source_feed: str, rss_enabled: bool, rss_transport: httpx.AsyncBaseTransport | None, telegram_client: TelegramClient | None, ai_transport: httpx.AsyncBaseTransport | None = None, codex_client: CodexClient | None = None, youtube_captions: CaptionsClient | None = None, youtube_history: HistoryClient | None = None, wall_clock: Callable[[], float] = time.time) -> None:
        self._youtube_captions = youtube_captions or LocalCaptions()
        self._youtube_history = youtube_history
        self._wall_clock = wall_clock
        self._captions_active = False
        self._codex_client = codex_client or LocalCodex()
        self._codex_status = codex_status()
        self._ai_transport = ai_transport
        self._ai: asyncio.Task[None] | None = None
        self._ai_sync = 0.0
        self._ai_stage = 'Проверка обработчика'
        self._root, self._source, self._clock, self._fault = root, source, clock, fault
        self._shutdown = shutdown
        self._admitted, self._should_stop = admitted, should_stop
        self._source_feed = source_feed
        self._rss_enabled, self._rss_transport = rss_enabled, rss_transport
        self._telegram_client = telegram_client
        self._sources: dict[str, Source] = {source_feed: source} if source else {}
        self._last_collection = clock()
        self._completions: dict[str, tuple[str, FolderContents | None]] = {}
        self._feed_checks: dict[str, str] = {}
        self._stop_requested = asyncio.Event()
        self._closed = False
        self._actions = asyncio.Lock()
        self._epoch = 0
        self._requests: set[asyncio.Future[Any]] = set()
        self._store: Store | None = None
        self._error: str | None = None
        self._last_problems: dict[str, Any] = dict(current=[], recent=[], count=0, updated_at=self._wall_clock())
        self._viewed_ids: list[str] = []
        self._active: asyncio.Task[None] | None = None
        self._cancelling = 0
        self._detached: set[asyncio.Task[None]] = set()
        self._detached_ai: set[asyncio.Task[None]] = set()
        self.diagnostics: Journal | None = None
        if isinstance(telegram_client, LocalTelegram):
            telegram_client.allowed = lambda: self._live() and not self._cancelling

    def _live(self) -> bool:
        return not self.stopped and self._admitted()

    async def admission_changed(self) -> None:
        async with self._actions:
            if not self._live() and not self.stopped:
                await self._cancel()

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
                        error = task.exception()
                        if error and self.diagnostics and not getattr(error, '_oko_diagnostic_group', None):
                            self.diagnostics.capture(error, 'Завершение фоновой задачи')
                else:
                    self._detached.add(task)
                    if task is ai_task:
                        self._detached_ai.add(task)

                    def completed(finished: asyncio.Task[None]) -> None:
                        self._detached.discard(finished)
                        self._detached_ai.discard(finished)
                        if not finished.cancelled():
                            error = finished.exception()
                            if error and self.diagnostics and not getattr(error, '_oko_diagnostic_group', None):
                                self.diagnostics.capture(error, 'Завершение отсоединённой задачи')

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
                    self._sources[feed['id']] = YouTubeSource(feed['original_urls'][0], feed['initial_days'], self._youtube_captions, self._rss_transport, self._youtube_history)
                    self._store.enqueue_discovery(feed['id'])
                youtube = self._sources[feed['id']]
                if isinstance(youtube, YouTubeSource):
                    youtube.initial_days = feed['initial_days']
                    youtube.initial_history = feed['initial_history']
                    youtube.want_captions = self._store.subscriptions.youtube_theses(feed['id'])
                    self._store.prepare_youtube(feed['id'], youtube.want_captions)
                continue
            if feed['transport'] == 'telegram' and self._telegram_client and feed.get('telegram_peer'):
                if not isinstance(self._sources.get(feed['id']), TelegramSource):
                    self._sources[feed['id']] = TelegramSource(self._telegram_client, feed['telegram_account'], feed['telegram_peer'], feed['initial_days'], feed.get('telegram_username'), feed.get('telegram_topic'))
                    self._store.enqueue_discovery(feed['id'])
                telegram_source = self._sources[feed['id']]
                if isinstance(telegram_source, TelegramSource):
                    telegram_source.initial_days = feed['initial_days']
                    telegram_source.initial_history = feed['initial_history']
                continue
            if feed['transport'] != 'rss' or not self._rss_enabled:
                continue
            if not isinstance(self._sources.get(feed['id']), RSSSource):
                self._sources[feed['id']] = RSSSource(feed['original_urls'][0], feed['initial_days'], self._rss_transport)
                self._store.enqueue_discovery(feed['id'])
            source = self._sources[feed['id']]
            if isinstance(source, (RSSSource, TelegramSource)):
                source.initial_days = feed['initial_days']
                if isinstance(source, RSSSource):
                    source.initial_history = feed['initial_history']
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

    def material_was_viewed(self, identity: str) -> bool:
        if self._store:
            row = self._store.db.execute('SELECT viewed FROM items WHERE id=?', (identity,)).fetchone()
            return bool(row and row['viewed'])
        return identity in self._viewed_ids

    def status(self) -> dict[str, Any]:
        state: dict[str, Any] = self._store.status() if self._store and not self.stopped else dict(jobs=[], errors=[], deleting=False)
        state['youtube'] = self.youtube_status() if self._store and not self.stopped else None
        if self._store and not self.stopped:
            self._last_problems = self._store.problems.snapshot(self._wall_clock())
            self._viewed_ids = [row['id'] for row in self._store.db.execute('SELECT id FROM items WHERE viewed=1')]
        state['problems'] = self._last_problems
        if self._error:
            state['problems'] = dict(current=[dict(id='runtime:storage',code='storage-unavailable',section='action',title='Хранилище недоступно',
                consequence='Сбор и обработка остановлены.',next_step='Проверьте доступность хранилища; состояние не сбрасывается.',
                objects=[],action=None,created_at=self._wall_clock(),next_at=None,state='current',finished_at=None,outcome=None)],recent=[],count=1,updated_at=self._wall_clock())
        state['viewed_ids'] = self._viewed_ids
        pending = any(j['status'] in ('pending', 'running') for j in state['jobs'])
        source_states = self._store.source_statuses() if self._store else []
        names = {f['id']: f['title'] for f in self._store.subscriptions.feeds()} if self._store else {}
        source_states = [dict(s, title=names.get(s['feed_id'], s['feed_id']), changes=getattr(self._sources[s['feed_id']], 'changes', None)) for s in source_states if s['feed_id'] in self._sources]
        if self.stopped:
            stage = 'Остановлено'
        elif self._error:
            stage = 'Ошибка хранилища'
        elif state['deleting']:
            stage = 'Удаление'
        elif any(j['kind'] in ('theses', 'filter') and j['status'] == 'running' for j in state['jobs']):
            stage = self._ai_stage
        elif any(j['kind'] in ('theses', 'filter') and j['status'] == 'pending' for j in state['jobs']) and not self._sources:
            stage = 'Ожидание обработчика' if state['errors'] else 'Подготовка тезисов'
        elif pending and self._sources and all(j['kind'] == 'read' for j in state['jobs'] if j['status'] in ('pending', 'running')):
            stage = 'Ожидание синхронизации Telegram'
        elif pending and not self._sources:
            stage = 'Источник не подключён'
        elif pending and state['errors']:
            stage = 'Сбор с ошибками' if self._active and not self._active.done() else 'Ожидание повтора'
        elif pending:
            stage = 'Сбор'
        else:
            stage = 'Завершено с ошибками' if state['errors'] else 'Готово'
        return dict(**state, initial_histories=[dict(feed_id=f['id'], **f['initial_history']) for f in self._store.subscriptions.feeds() if f['initial_history']] if self._store else [], telegram_reads=self._store.read_sync.statuses() if self._store and not self.stopped else [], sources=source_states, error=self._error, stopped=self.stopped,
                    stage=stage,
                    ready_sources=self._store.ready_sources(list(self._sources)) if self._store and not self.stopped else 0,
                    total_sources=len(self._sources))

    async def action_started(self, identity: str, context: dict[str, Any], *, restart: bool = False, expected_identity: str | None = None) -> None:
        async with self._actions:
            if self._store and not self.stopped:
                self._store.problems.action_started(identity, context, self._wall_clock(), restart=restart, expected_identity=expected_identity)

    async def action_failed(self, identity: str, error_json: str) -> None:
        async with self._actions:
            if self._store and not self.stopped:
                self._store.problems.command_error(identity, error_json)

    async def action_finished(self, identity: str, succeeded: bool) -> None:
        async with self._actions:
            if self._store and not self.stopped:
                self._store.problems.action_finished(identity, succeeded, self._wall_clock())
                self._last_problems = self._store.problems.snapshot(self._wall_clock())

    async def report_action(self, identity: str, context: dict[str, Any], result: str) -> str:
        async with self._actions:
            if any(obj['kind'] == 'material' and self.material_was_viewed(obj['id']) for obj in context['objects']):
                return 'abandoned'
            try:
                return self.require_store().problems.report_action(identity, context, result, self._wall_clock())
            except ValueError:
                raise

    async def abandon_problem(self, identity: str) -> None:
        async with self._actions:
            if not self.require_store().problems.abandon(identity, self._wall_clock()):
                raise ActionError('invalid', 'Отмена доступна только ошибке соответствующего черновика', expected=True)

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

    def youtube_status(self) -> dict[str, Any]:
        return self.require_store().youtube.status(self._wall_clock())

    async def configure_youtube(self, body: Any) -> dict[str, Any]:
        async with self._actions:
            current = self.require_store()
            try:
                current.youtube.configure(body)
            except ValueError:
                raise ActionError('invalid', 'Некорректные настройки прокси: нужен HTTP/HTTPS-адрес без учётных данных, пути и параметров', expected=True) from None
            await self._cancel()
            return self.youtube_status()


    async def viewed(self, item: str) -> None:
        async with self._actions:
            current = self.require_store()
            members = set(current.reading.members(item)) | {item}
            if not current.viewed(item):
                raise ActionError('missing', 'Материал не найден')
            if self.diagnostics:
                self.diagnostics.forget_materials(members)
            await self._drain_deletions(current)

    def _completion_preview(self, folder: str, current: Store) -> dict[str, Any]:
        try:
            contents = current.folder_contents(folder)
        except LookupError as error:
            record_caught('runtime._completion_preview')
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
                    record_caught('runtime.complete_folder')
                    raise ActionError('missing', str(error)) from None
                if latest != contents:
                    raise ActionError('conflict', dict(message='Состав папки изменился. Подтвердите новое количество.', preview=self._completion_preview(folder, current)))
                current.viewed_set([identity for identity, _ in contents.members])
                if self.diagnostics:
                    self.diagnostics.forget_materials({identity for identity, _ in contents.members} | set(contents.cards))
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

    async def pause_history(self, identity: str, paused: bool) -> dict[str, Any]:
        async with self._actions:
            current = self.require_store()
            try:
                result = current.pause_history(identity, paused)
            except LookupError as error:
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                raise ActionError('invalid', str(error)) from None
            # Invalidate in-flight generations before cancellation. An adapter
            # ignoring cancellation cannot publish an old history result.
            await self._cancel()
            self._sync_sources()
            current.enqueue_discovery(identity)
            return result

    async def change_youtube_history(self, identity: str, selection: InitialHistory | None) -> dict[str, Any]:
        async with self._actions:
            current = self.require_store()
            try:
                result = current.change_youtube_history(identity, selection)
            except LookupError as error:
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                raise ActionError('conflict', str(error), expected=True) from None
            await self._cancel()
            self._sync_sources()
            current.enqueue_discovery(identity)
            return result

    async def patch_feed(self, identity: str, body: FeedPatch) -> dict[str, Any]:
        async with self._actions:
            current = self.require_store()
            try:
                result = current.subscriptions.update_feed(identity, body)
            except LookupError as error:
                record_caught('runtime.patch_feed')
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                record_caught('runtime.patch_feed')
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
                record_caught('runtime.import_subscriptions')
                raise ActionError('invalid', str(error)) from None
            if 'conflicts' in result:
                raise ActionError('conflict', result)
            return result

    async def add_feed(self, body: AddFeed) -> dict[str, Any]:
        async with self._actions:
            try:
                current = self.require_store()
                if not self._live():
                    raise ActionError('conflict', 'Откройте читатель для добавления фида', expected=True)
                url = normalized(body.url)
                if self._feed_checks.get(body.verification) != url:
                    raise ValueError('Сначала проверьте текущий URL фида')
                return current.subscriptions.add_feed(body.model_copy(update={'url': url}))
            except ValueError as error:
                record_caught('runtime.add_feed')
                raise ActionError('invalid', str(error)) from error

    async def verify_feed(self, body: FeedURL) -> dict[str, Any]:
        async with self._actions:
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для проверки фида')
            epoch = self._epoch
            try:
                url = normalized(body.url)
                _, kind, _ = identify(url)
                if kind not in ('rss', 'youtube'):
                    raise ValueError('Для Telegram используйте подключение Telegram-источника')
                diagnostic_source.set('Проверка фида · ' + url)
                existing = current.subscriptions.existing_feed(url)
            except (ValueError, SourceError) as error:
                raise ActionError('invalid', error.label if isinstance(error, SourceError) else str(error)) from error
            async def check() -> str:
                async with asyncio.timeout(25):
                    raw, _, _ = await RSSSource(url, 1, self._rss_transport).download(url)
                    return feed_title(raw)
            task = asyncio.create_task(check()) if not existing else None
            if task:
                self._requests.add(task)
        try:
            title = existing['title'] if existing else await task if task else ''
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller and caller.cancelling():
                raise
            raise ActionError('conflict', 'Проверка фида прервана; повторите действие', expected=True) from None
        except (SourceError, TimeoutError, httpx.InvalidURL) as error:
            raise ActionError('invalid', error.label if isinstance(error, SourceError) else 'Неверный URL фида' if isinstance(error, httpx.InvalidURL) else 'Проверка фида не завершилась за 25 секунд') from error
        finally:
            if task:
                self._requests.discard(task)
        async with self._actions:
            self._check_request(current, epoch)
            verification = secrets.token_urlsafe(24)
            self._feed_checks[verification] = url
            if len(self._feed_checks) > 100:
                del self._feed_checks[next(iter(self._feed_checks))]
            return dict(url=url, title=title, kind=kind, verification=verification,
                        existing=current.subscriptions.existing_feed(url))

    async def update_settings(self, scope: str, owner: str, body: Overrides) -> dict[str, Any]:
        async with self._actions:
            try:
                current = self.require_store()
                result = current.subscriptions.update_settings(scope, owner, body)
                current.settings_changed()
                await self._cancel()
                return result
            except LookupError as error:
                record_caught('runtime.update_settings')
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                record_caught('runtime.update_settings')
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
            record_caught('runtime.check_processor')
            raise ActionError('conflict', error.label) from None
        except asyncio.CancelledError:
            raise ActionError('conflict', 'Проверка прервана', expected=True) from None
        finally:
            self._requests.discard(task)
        async with self._actions:
            self._check_request(current, epoch)
        return result

    def codex_status(self) -> dict[str, Any]:
        self.require_store()
        config = self.require_store().codex_config()
        return dict(self._codex_status, config=config.model_dump() if config else None)

    async def codex_models(self) -> list[dict[str, Any]]:
        async with self._actions:
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для подключения')
            epoch = self._epoch
            task = asyncio.create_task(self._codex_client.catalog())
            self._requests.add(task)
        try:
            result = await task
        except ProcessingError as error:
            raise ActionError('conflict', error.label) from None
        except asyncio.CancelledError:
            raise ActionError('conflict', 'Загрузка каталога прервана', expected=True) from None
        finally:
            self._requests.discard(task)
        async with self._actions:
            self._check_request(current, epoch)
        return result

    async def configure_codex(self, config: CodexSettings) -> None:
        # Validate against fresh catalog outside the action lock. Saving cannot
        # stop the in-flight generation or increase any freshness generation.
        current = self.require_store()
        models = await self.codex_models()
        try:
            config.validate_catalog(models)
        except ProcessingError as error:
            raise ActionError('invalid', error.label) from None
        async with self._actions:
            if self._store is not current or not self._live():
                raise ActionError('conflict', 'Сохранение настроек прервано', expected=True)
            current.configure_codex(config)

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
            raise ActionError('conflict', 'Проверка Codex прервана', expected=True) from None
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

    def processing_wait(self, item: str, kind: Literal['filter', 'theses']) -> 'ProcessingWait | None':
        return self.require_store().processing.waiting(item, kind, self._clock(), self._wall_clock())

    async def retry_theses(self, item: str) -> None:
        async with self._actions:
            if not self.require_store().processing.retry_theses(item):
                raise ActionError('missing', 'Тезисы для материала не включены')

    async def regenerate(self, item: str) -> None:
        async with self._actions:
            current = self.require_store()
            accepted = current.processing.retry_theses(item)
            if not accepted:
                raise ActionError('missing', 'Результат недоступен для пересоздания')

    async def _execute_ai(self, job: Job) -> None:
        capture_scope.set(secrets.token_hex(16))
        diagnostic_materials.set((job.item,))
        current: Store | None = None
        work: PreparedWork | None = None
        try:
            async with self._actions:
                current = self._store
                if not current or not current.valid(job) or not self._live():
                    return
                diagnostic_source.set('Обработка · ' + job.kind + ' · ' + job.item)
                work = current.processing.prepare(job)
                diagnostic_materials.set(tuple(source.id for source in work.request.sources))
                diagnostic_source.set('Обработка · ' + job.kind + ' · ' + ','.join(sorted({current.item_feed(source.id) for source in work.request.sources})))
                config = current.processor_config()
                request = work.request
                adapter: Codex | LlamaCpp
                if request.processor == 'codex':
                    adapter = Codex(self._codex_client)
                elif request.processor in ('llamacpp', 'llamacpp:' + (config.model if config else '')) and config:
                    adapter = LlamaCpp(config, self._ai_transport)
                else:
                    raise ProcessingError('Ожидание выбранного обработчика; llama.cpp не настроен или не выбран', code='processor-config')
            self._ai_stage = 'Проверка обработчика'
            checked = await adapter.check()
            async with self._actions:
                if self._store is not current or not self._live() or not work.current():
                    return
                if not checked['local'] and not work.public_ai:
                    raise ProcessingError('Запрещена публичная AI-обработка; выберите локальную модель', code='ai-permission')
            self._ai_stage = work.stage
            result = (await adapter.generate(request, self._root / 'staging' / job.item)
                      if isinstance(adapter, Codex) else await adapter.generate(request))
            async with self._actions:
                if self._store is current and self._live():
                    work.accept(result)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            record_caught('runtime._execute_ai')
            async with self._actions:
                if current and self._store is current and not self.stopped and current.valid(job):
                    current.retry(job, self._clock(), error.label if isinstance(error, ProcessingError) else 'Ошибка AI-обработки', 30,
                                  code=error.code if isinstance(error, ProcessingError) else 'processing-failed')
        finally:
            if work:
                work.close()

    def _telegram_ready(self) -> tuple[Store, TelegramClient]:
        current = self.require_store()
        if not self._live():
            raise ActionError('conflict', 'Откройте читатель для подключения')
        if not self._telegram_client:
            raise ActionError('conflict', 'Telegram-клиент не настроен')
        return current, self._telegram_client

    def _check_request(self, current: Store, epoch: int) -> None:
        if self.stopped or self._store is not current or not self._live():
            raise ActionError('conflict', 'Око остановлено', expected=True)
        if epoch != self._epoch:
            raise ActionError('conflict', 'Проверка подключения прервана; повторите действие', expected=True)

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
            record_caught('runtime._call_telegram')
            raise ActionError('conflict', error.label) from None
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller and caller.cancelling():
                raise
            raise ActionError('conflict', 'Око остановлено' if self.stopped else 'Подключение Telegram прервано; повторите действие', expected=True) from None
        finally:
            self._requests.discard(task)
        async with self._actions:
            self._check_request(current, epoch)
        return result, current, epoch

    async def telegram_accounts(self) -> list[str]:
        result, _, _ = await self._call_telegram(lambda client: client.accounts())
        return result

    async def telegram_topics(self, body: TelegramPeer) -> list[dict[str, Any]]:
        result, _, _ = await self._call_telegram(
            lambda client: client.topics(body.account, int(body.peer) if body.peer.startswith('-') else body.peer))
        return result

    async def connect_telegram(self, body: ConnectTelegram) -> dict[str, Any]:
        info, current, epoch = await self._call_telegram(
            lambda client: client.info(body.account, int(body.peer) if body.peer.startswith('-') else body.peer, **({'topic_id': body.topic_id} if body.topic_id is not None else {})))
        async with self._actions:
            self._check_request(current, epoch)
            if info.get('forum') and body.topic_id is None:
                raise ActionError('invalid', 'Выберите тему форумной группы: загрузите список тем')
            if body.topic_id is not None and (not info.get('forum') or info.get('topic_id') != body.topic_id):
                raise ActionError('invalid', 'Тема недоступна в этой группе')
            if info['kind'] not in ('channel', 'group'):
                raise ActionError('invalid', 'Поддерживаются каналы и группы')
            try:
                result = current.subscriptions.connect_telegram(body, info)
            except (LookupError, ValueError) as error:
                record_caught('runtime.connect_telegram')
                raise ActionError('invalid', str(error)) from None
            await self._cancel()
            self._sources.pop(result['id'], None)
            self._sync_sources()
            return result

    def _delete(self, job: Job, current: Store) -> None:
        try:
            current.delete(job)
        except Exception:
            record_caught('runtime._delete')
            if current.valid(job):
                current.retry(job, self._clock(), 'Ошибка удаления', code='delete')

    async def _execute_read(self, job: Job, current: Store, source: Source) -> None:
        if not isinstance(source, TelegramSource):
            async with self._actions:
                if self._store is current and current.valid(job):
                    current.read_sync.suspend(job)
            return
        async with self._actions:
            request = current.read_sync.request(job.item)
            if not request:
                current.read_sync.suspend(job)
                return
            signature = current.read_sync.signature(request)
        await source.resolve()
        state = await source.client.read_state(source.account, source.peer, request['since'], **source.scope)
        plan = current.read_sync.plan(request, state)
        confirmed = plan.confirmed
        if plan.until > confirmed:
            window = await source.client.read_window(source.account, source.peer, confirmed, plan.until, **source.scope)
            plan = current.read_sync.plan(request, state, window)
        async with self._actions:
            latest = current.read_sync.request(job.item) if self._store is current else None
            if not self._live() or not current.valid(job) or not latest or current.read_sync.signature(latest) != signature:
                return
        if plan.candidate > confirmed:
            await source.client.acknowledge(source.account, source.peer, plan.candidate, **source.scope)
            self._fault('after_telegram_acknowledge')
            confirmed = plan.candidate
        async with self._actions:
            if self._store is current and self._live() and current.valid(job):
                current.read_sync.completed(job, plan, confirmed, self._clock())

    async def _execute(self, job: Job) -> None:
        diagnostic_materials.set((job.item,) if job.kind in ('fetch','delete') else ())
        capture_scope.set(secrets.token_hex(16))
        current: Store | None = None
        feed = ''
        try:
            async with self._actions:
                current = self._store
                if not current or not current.valid(job) or not self._live():
                    return
                if job.kind == 'delete':
                    diagnostic_source.set('Удаление · ' + job.item)
                    self._delete(job, current)
                    return
                feed = (job.item or 'demo') if job.kind in ('discover', 'read') else current.item_feed(job.item)
                diagnostic_source.set('Сбор · ' + feed + ' · ' + job.kind)
                source = self._sources.get(feed)
                publication = current.publication(job.item) if job.kind == 'fetch' else None
                namespace = 'telegram' if isinstance(source, TelegramSource) else ''
                cursor = current.cursor(feed, namespace) if job.kind == 'discover' else None
            if not source:
                return
            if job.kind == 'read':
                await self._execute_read(job, current, source)
            elif job.kind == 'discover':
                items, next_cursor = await source.discover(cursor)
                async with self._actions:
                    if self._store is current and self._live() and current.valid(job):
                        current.discovered(items, next_cursor, job, feed, namespace)
                        if getattr(source, 'more', False):
                            # Yield a scheduling turn to saved page materials;
                            # bounded archive discovery must not monopolize fetch.
                            delay = .1 if isinstance(source, TelegramSource) and source.initial_history else 0
                            current.enqueue_discovery(feed, self._clock() + delay if delay else 0)
                        current.feed_status(feed, error=None, gap=getattr(source, 'gap', None), changes=getattr(source, 'changes', None))
            elif publication:
                if isinstance(source, (RSSSource, TelegramSource)) and bool(publication.members) != isinstance(source, TelegramSource):
                    raise SourceError('Материал получен другим способом; верните исходный транспорт для загрузки')
                caption_attempt = CaptionAttempt(self, current, job) if isinstance(source, YouTubeSource) else None
                material = (await source.fetch(publication, captions=caption_attempt, request_captions=not current.youtube.terminal(job.item))
                            if isinstance(source, YouTubeSource) else await source.fetch(publication))
                async with self._actions:
                    if self._store is current and self._live() and current.valid(job):
                        current.save(job, material, self._clock())
                        if caption_attempt and caption_attempt.attempted and not caption_attempt.blocked:
                            current.youtube.completed(job.item, material.captions == 'available')
                        if caption_attempt and caption_attempt.retry:
                            current.youtube.defer(job, self._clock(), current.youtube.ready_at() - self._wall_clock(), 'Получение субтитров приостановлено: YouTube ограничил запросы', blocked=True)
                        current.read_sync.sync()
                        current.feed_status(feed, error=None, gap=getattr(source, 'gap', None), changes=getattr(source, 'changes', None))
        except CaptionsDeferred:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            record_caught('runtime._execute')
            async with self._actions:
                if current and self._store is current and not self.stopped and current.valid(job):
                    if isinstance(error, SourceSkipped) and job.kind == 'fetch':
                        current.viewed(job.item)
                        return
                    if isinstance(error, SourceDeleted) and job.kind == 'fetch':
                        current.source_removed(job)
                        return
                    label = error.label if isinstance(error, SourceError) else 'Ошибка синхронизации Telegram' if job.kind == 'read' else 'Ошибка сбора'
                    current.retry(job, self._clock(), label, error.delay if isinstance(error, SourceError) else 1,
                                  code=error.code if isinstance(error, SourceError) and error.code else job.kind + ':' + (getattr(error, 'remote_type', '') or type(error).__name__))
                    if job.kind == 'read':
                        current.read_sync.failed(feed, label)
                    elif isinstance(error, SourceError):
                        current.feed_status(feed, error=label, gap=None)

    async def run(self) -> None:
        journal_context = active_journal.set(self.diagnostics)
        try:
            self._store = Store(self._root, self._fault, wall_clock=self._wall_clock)
            viewed = {row['id'] for row in self._store.db.execute('SELECT id FROM items WHERE viewed=1')}
            if viewed and self.diagnostics:
                self.diagnostics.forget_materials(viewed)
            config = self._store.subscriptions
            if (self._source and self._source_feed == 'demo') or any(c['feed_id'] == 'demo' for c in self._store.cards()):
                config.ensure_demo()
            if self._source:
                config.check_owner('feed', self._source_feed)
                self._store.enqueue_discovery(self._source_feed)
        except (sqlite3.DatabaseError, OSError):
            record_caught('runtime.run')
            self._error = 'Хранилище недоступно или повреждено. Состояние сохранено; сбор остановлен.'
        try:
            while not self.stopped:
                async with self._actions:
                    now = self._clock()
                    if not self._live():
                        await self._cancel()
                        if self._should_stop():
                            self._stop_requested.set()
                            break
                    elif self._store:
                        if now - self._ai_sync >= .5:
                            self._store.read_sync.sync()
                            self._store.processing.sync()
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
        except Exception:
            record_caught('Главный цикл Око')
            raise
        finally:
            self._stop_requested.set()
            async with self._actions:
                await self._cancel()
                if self._store:
                    self._store.db.close()
                    self._store = None
                self._closed = True
            self._shutdown()
            active_journal.reset(journal_context)


class CaptionAttempt:
    """One runtime-owned caption call; waiting releases the collection slot."""
    def __init__(self, runtime: Runtime, store: Store, job: Job) -> None:
        self.runtime, self.store, self.job = runtime, store, job
        self.retry = False
        self.attempted = False
        self.blocked = False

    def current(self) -> bool:
        return self.runtime._store is self.store and self.runtime._live() and self.store.valid(self.job)

    async def fetch(self, video: str) -> str | None:
        runtime, store, job = self.runtime, self.store, self.job
        async with runtime._actions:
            if not self.current():
                raise asyncio.CancelledError
            delay = store.youtube.ready_at() - runtime._wall_clock()
            if delay > 0 or runtime._captions_active:
                store.youtube.defer(job, runtime._clock(), max(delay, .1), 'Ожидание допуска получения субтитров')
                raise CaptionsDeferred
            config = store.youtube.config()
            client = LocalCaptions(proxy_url(config)) if isinstance(runtime._youtube_captions, LocalCaptions) else runtime._youtube_captions
            store.youtube.start(job.item, runtime._wall_clock())
            self.attempted = True
            runtime._captions_active = True
        try:
            try:
                text = await client.fetch(video)
            except CaptionsBlocked as error:
                async with runtime._actions:
                    if not self.current():
                        raise asyncio.CancelledError
                    self.retry = store.youtube.blocked(job, runtime._wall_clock(), error.retry_after)
                    self.blocked = True
                raise
            async with runtime._actions:
                if not self.current():
                    raise asyncio.CancelledError
            return text
        finally:
            runtime._captions_active = False
