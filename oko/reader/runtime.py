"""Reader actions and owned processing lifecycle behind one module interface.

Store remains responsible for durable identity/content/recovery. Network waits
happen outside the action lock; admission, transitions and result commits share it.
"""
from oko.collection.request_observation import Capture
from oko.storage.reading import reading_snapshot
import asyncio
from dataclasses import dataclass
from oko.reviews.coordination import ProcessingWait
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
import secrets
import sqlite3
import shutil
import time
from typing import Any, Literal, TypeVar

import httpx

from oko.collection.rss import RSSSource, feed_title, normalized
from oko.collection.sources import Sources, SourceSettings, TelegramAccess
from oko.collection.contracts import FetchResources, SourceError, SourceDeleted, SourceSkipped, SourceDeferred, DownloadStage
from oko.collection.contracts import Source
from oko.collection.adapters import AdapterException, AdapterErrorKind, AdapterIssue
from oko.processing.llamacpp import LlamaCpp, ProcessorConfig
from oko.processing.contracts import ProcessingError
from oko.processing.execution import CodexSettings
from oko.processing.codex import Codex, CodexClient, LocalCodex, status as codex_status
from oko.reviews.coordination import PreparedWork
from oko.reader.telegram_http import TelegramHTTP
from oko.reader.composition import FolderContents
from oko.storage.media import MediaOutput
from oko.storage.store import Job, Store
from oko.collection.telegram.contracts import TelegramBinding, ClientReadBinding
from oko.collection.telegram.web import WebTelegram
from oko.collection.telegram.source import LocalTelegram, TelegramClient, TelegramSource, ConnectTelegram
from oko.subscriptions.subscriptions import AddFeed, FeedURL, Overrides, FeedPatch, FolderConflict, identify, GLOBAL_SERVICE_SETTINGS
from oko.collection.youtube.source import LocalCaptions
from oko.collection.contracts import CaptionsClient
from oko.collection.youtube.history import HistoryClient
from oko.subscriptions.history import InitialHistory
from oko.subscriptions.telegram import TelegramConflict
from oko.collection.youtube.access import CaptionsBlocked, CaptionsDeferred, proxy_url
from oko.diagnostics import Journal, active_journal, diagnostic_source, diagnostic_materials, capture_scope, record_caught, exception_details

T = TypeVar('T')


class ActionError(Exception):
    def __init__(self, kind: Literal['conflict', 'invalid', 'missing', 'unavailable'], detail: str | dict[str, Any], *, expected: bool = False, code: str | None = None) -> None:
        self.kind, self.detail = kind, detail
        self.code = code
        self.diagnostic_skip = expected
        super().__init__(str(detail))


@dataclass(frozen=True)
class StatusSnapshot:
    state: dict[str, Any]
    bindings: dict[str, dict[str, Any]]


@dataclass
class _CollectionAttempt:
    job: Job
    feed: str
    started: float
    capture: Capture
    saved: dict[str, Any]


class _ExternalCheck:
    """One owned external wait and its guarded acceptance, private to Runtime.

    Start is called under the action lock. Waiting releases it; accept reacquires
    it at the scenario's existing commit points, including failures. Snapshots
    preserve each scenario's synchronous preparation point.
    """
    def __init__(self, runtime: 'Runtime', store: Store, epoch: int | None = None) -> None:
        self._runtime = runtime
        self._store = store
        self._epoch = runtime._epoch if epoch is None else epoch

    def start(self, call: Awaitable[T]) -> Awaitable[T]:
        task = asyncio.ensure_future(call)
        self._runtime._requests.add(task)

        async def wait() -> T:
            try:
                return await task
            finally:
                self._runtime._requests.discard(task)

        return wait()

    def follow(self, later: '_ExternalCheck') -> '_ExternalCheck':
        # Telegram's multi-step connection keeps its original Store, but the
        # final info call supplies the acceptance generation. Do not strengthen
        # this to a single generation spanning the whole connection.
        return _ExternalCheck(self._runtime, self._store, later._epoch)

    @asynccontextmanager
    async def accept(self, *, require_work: bool = False, failure: ActionError | None = None) -> AsyncIterator[Store]:
        runtime = self._runtime
        async with runtime._actions:
            if require_work:
                runtime.require_work()
            if failure is not None:
                # A failed identity check may mark a profile unavailable only
                # in its original context; interruption rethrows the same error.
                if failure.diagnostic_skip or runtime._store is not self._store or not runtime._live() or self._epoch != runtime._epoch:
                    raise failure
            else:
                if runtime.stopped or runtime._store is not self._store or not runtime._live():
                    raise ActionError('conflict', 'Око остановлено', expected=True)
                if self._epoch != runtime._epoch:
                    raise ActionError('conflict', 'Проверка подключения прервана; повторите действие', expected=True)
            yield self._store


class Runtime:
    def __init__(self, root: Path, source: Source | None, clock: Callable[[], float],
                 fault: Callable[[str], None], shutdown: Callable[[], None],
                 admitted: Callable[[], bool], should_stop: Callable[[], bool], source_feed: str, rss_enabled: bool, rss_transport: httpx.AsyncBaseTransport | None, telegram_client: TelegramClient | None, ai_transport: httpx.AsyncBaseTransport | None = None, codex_client: CodexClient | None = None, youtube_captions: CaptionsClient | None = None, youtube_history: HistoryClient | None = None, wall_clock: Callable[[], float] = time.time) -> None:
        self._youtube_captions = youtube_captions or LocalCaptions()
        self._youtube_history = youtube_history
        self._wall_clock = wall_clock
        self._collection_instance = secrets.token_hex(16)
        self._collection_revision = 0
        self._collection_active: dict[int, _CollectionAttempt] = {}
        self._captions_active = False
        self._codex_client = codex_client or LocalCodex()
        self._codex_status = codex_status()
        self._ai_transport = ai_transport
        self._ai: asyncio.Task[None] | None = None
        self._ai_sync = 0.0
        self._filter_sync = 0.0
        self._summary_sync = 0.0
        self._source_revision: int | None = None
        self._ai_stage = 'Проверка обработчика'
        self._root, self._source, self._clock, self._fault = root, source, clock, fault
        self._shutdown = shutdown
        self._admitted, self._should_stop = admitted, should_stop
        self._source_feed = source_feed
        self._rss_enabled, self._rss_transport = rss_enabled, rss_transport
        self._telegram_client = telegram_client
        self._telegram_http = TelegramHTTP(rss_transport, clock, wall_clock,
            lambda group: self.require_store().telegram_http_state(group),
            self._save_telegram_http_wait,
            lambda: self._live() and not self._cancelling, lambda: self._epoch,
            lambda: float(self.require_store().subscriptions.telegram_http_settings()['value']))
        self._collection = Sources(source, source_feed, rss_enabled, rss_transport, telegram_client,
                                   self._youtube_captions, self._youtube_history, self._telegram_http)
        self._sources = self._collection.sources
        self._telegram_bindings: dict[str, TelegramBinding] = {}
        self._last_collection = clock()
        self._last_media_cleanup = clock()
        self._completions: dict[str, tuple[tuple[str, str], FolderContents | None]] = {}
        self._feed_checks: dict[str, str] = {}
        self._stop_requested = asyncio.Event()
        self._closed = False
        self.enabled = True
        self._actions = asyncio.Lock()
        self._epoch = 0
        self._requests: set[asyncio.Future[Any]] = set()
        self._store: Store | None = None
        self._error: str | None = None
        self._last_problems: dict[str, Any] = dict(current=[], recent=[], count=0, updated_at=self._wall_clock())
        self._viewed_ids: list[str] = []
        self._lanes: dict[str, asyncio.Task[None]] = {}
        self._media_job: Job | None = None
        self._account_retry: dict[int, float] = {}
        self._restored_wait_feeds: set[str] = set()
        self._lane_feeds: dict[str, str] = {}
        self._ai_feed: str | None = None
        self._cancelling = 0
        self._detached: set[asyncio.Task[None]] = set()
        self._detached_ai: set[asyncio.Task[None]] = set()
        self.diagnostics: Journal | None = None
        self.reader_finish: Callable[[], Awaitable[None]] | None = None
        self.reader_advance: Callable[[], None] | None = None
        self._reader_advanced = float('-inf')
        if isinstance(telegram_client, LocalTelegram):
            telegram_client.allowed = lambda: self._live() and not self._cancelling

    async def _save_telegram_http_wait(self, group: str, server_delay: float | None, generation: int) -> float:
        async with self._actions:
            if not self._live() or self._cancelling or generation != self._epoch:
                raise SourceDeferred('Запрос Telegram прерван', code='source_unavailable')
            current = self.require_store()
            state = current.telegram_http_state(group)
            strikes = min(int(state.get('strikes', 0)) + 1, 7)
            delay = max(float(min(60 * 2 ** (strikes - 1), 3600)), server_delay or 0)
            until = max(float(state.get('until', 0)), self._wall_clock() + delay)
            current.telegram_http_wait(group, dict(until=until, strikes=strikes))
            return until

    def _live(self) -> bool:
        return not self.stopped and self.enabled and self._admitted()

    async def admission_changed(self) -> None:
        async with self._actions:
            if not self._live() and not self.stopped:
                await self._cancel()

    async def _cancel(self, feeds: set[str] | None = None) -> None:
        if feeds is None:
            self._cancelling += 1
            self._epoch += 1
            for request in tuple(self._requests):
                request.cancel()
        try:
            # Invalidate before waiting: even an adapter suppressing cancellation may
            # not publish its old generation when another tab is still alive.
            if self._store:
                for identity, attempt in list(self._collection_active.items()):
                    if feeds is None or attempt.feed in feeds:
                        self._store.collection_status.interrupt(attempt.job, attempt.feed, attempt.saved, max(0., self._clock()-attempt.started))
                        del self._collection_active[identity]
                self._store.interrupted(feeds)
            ai_task = self._ai
            lanes = [lane for lane in self._lanes if feeds is None or self._lane_feeds.get(lane) in feeds]
            cancelled_feeds = {self._lane_feeds[lane] for lane in lanes if lane in self._lane_feeds}
            tasks = [self._lanes.pop(lane) for lane in lanes]
            if 'media' in lanes:
                self._media_job = None
            if self._ai and (feeds is None or self._ai_feed in feeds):
                tasks.append(self._ai)
                self._ai = None
            for task in tasks:
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=.25)
                if task in done:
                    if not task.cancelled():
                        error = task.exception()
                        if error and self.diagnostics and not getattr(error, '_oko_diagnostic_group', None):
                            self.diagnostics.capture(error, 'Завершение фоновой задачи')
                else:
                    self._detach_task(task, ai=task is ai_task)
            if self._telegram_client:
                if feeds is None or (tasks and not isinstance(self._telegram_client, LocalTelegram)):
                    await self._telegram_client.close()
                elif isinstance(self._telegram_client,LocalTelegram):
                    accounts = {source.account for feed in cancelled_feeds if isinstance(source := self._sources.get(feed), TelegramSource)}
                    for account in accounts:
                        await self._telegram_client.close_account(account)
        finally:
            if feeds is None:
                self._cancelling -= 1

    def _detach_task(self, task: asyncio.Task[None], *, ai: bool = False) -> None:
        self._detached.add(task)
        if ai:
            self._detached_ai.add(task)
        def completed(finished: asyncio.Task[None]) -> None:
            self._detached.discard(finished)
            self._detached_ai.discard(finished)
            if not finished.cancelled():
                error = finished.exception()
                if error and self.diagnostics and not getattr(error, '_oko_diagnostic_group', None):
                    self.diagnostics.capture(error, 'Завершение отсоединённой задачи')
        task.add_done_callback(completed)

    def _sync_sources(self) -> None:
        if not self._store:
            return
        revision = self._store.subscriptions.source_revision()
        if revision == self._source_revision:
            return
        settings = []
        feeds = self._store.subscriptions.feeds()
        self._store.publications.sync_feeds([feed['id'] for feed in feeds])
        for feed in feeds:
            if feed['id'] == self._source_feed and self._source:
                continue
            binding = self._store.subscriptions.telegram.binding(feed['id'])
            access = None
            if binding:
                profile = self._store.subscriptions.telegram.profile(binding['role']) if binding['kind'] == 'client' else None
                access = TelegramAccess(binding['kind'], binding.get('username', feed.get('telegram_username')),
                    profile['account'] if profile else feed.get('telegram_account'),
                    int(binding['peer_id']) if binding['kind'] == 'client' else feed.get('telegram_peer'))
            elif feed['transport'] == 'telegram':
                access = TelegramAccess('client', feed.get('telegram_username'), feed.get('telegram_account'), feed.get('telegram_peer'))
            settings.append(SourceSettings(feed['id'], feed['transport'], feed['original_urls'][0],
                feed['initial_days'], feed['initial_history'], feed['connection'] == 'deleting', access,
                self._store.subscriptions.youtube_theses(feed['id']) if feed['transport'] == 'youtube' else False,
                self._store.publications.feed(feed['id'])))
        updates = self._collection.configure(settings)
        for identity in updates.created:
            self._store.enqueue_discovery(identity)
        for identity, want_captions in updates.captions:
            self._store.prepare_youtube(identity, want_captions)
        self._telegram_bindings = {}
        for feed in feeds:
            binding = self._store.subscriptions.telegram.binding(feed['id'])
            bound_source = self._sources.get(feed['id'])
            if bound_source and binding and binding['kind'] == 'web' and feed['connection'] != 'deleting':
                self._telegram_bindings[feed['id']] = TelegramBinding(bound_source, 'telegram-web', 'common')
            if bound_source and binding and binding['kind'] == 'client' and feed['connection'] != 'deleting':
                profile = self._store.subscriptions.telegram.profile(binding['role'])
                if profile:
                    state = self._store.db.execute('SELECT initial FROM telegram_read_state WHERE feed=?', (feed['id'],)).fetchone()
                    read = ClientReadBinding(profile['account'], profile['user_id'], int(binding['peer_id']), profile['generation'], (state['initial'] or 0) if state else 0)
                    self._telegram_bindings[feed['id']] = TelegramBinding(bound_source, 'telegram', binding['role'], read)
                    wait = self._store.db.execute('SELECT value FROM state WHERE key=?', ('telegram-retry:'+str(profile['user_id']),)).fetchone()
                    if wait and feed['id'] not in self._restored_wait_feeds:
                        deadline = self._account_retry.setdefault(profile['user_id'], self._clock() + max(0, float(wait[0]) - self._wall_clock()))
                        self._store.restore_telegram_wait(feed['id'], deadline)
                        self._restored_wait_feeds.add(feed['id'])
        self._source_revision = revision

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

    def collection_snapshot(self) -> dict[str, Any]:
        current = self.require_store()
        feeds = current.subscriptions.feeds()
        for feed in feeds:
            binding = current.subscriptions.telegram.binding(feed['id']) if feed['kind']=='telegram' else None
            feed['collection_access'] = 'Веб' if binding and binding['kind']=='web' else 'Личный аккаунт' if binding and binding.get('role')=='personal' else 'Аккаунт для сбора' if binding and binding.get('role')=='collection' else feed.get('transport', feed['kind'])
        sources = current.collection_status.snapshot(feeds, self._live(),
            'Око выключено' if not self.enabled else 'Ожидание присутствия читателя' if not self._admitted() else 'Работа приостановлена',
            {identity: dict(generation=attempt.job.generation, elapsed_seconds=max(0.,self._clock()-attempt.started),stage=attempt.capture.stage)
             for identity, attempt in self._collection_active.items()})
        self._collection_revision += 1
        return dict(instance_id=self._collection_instance, revision=self._collection_revision,
                    observed_at=self._wall_clock(), work_allowed=self._live(), sources=self._clean_collection(sources))

    def status(self) -> dict[str, Any]:
        return self.status_snapshot().state

    def status_snapshot(self) -> StatusSnapshot:
        candidates = None
        with reading_snapshot():
            if self._store and not self.stopped:
                candidates = self._store.problems.candidates(self._wall_clock())
            feeds = {f['id']: f for f in self._store.subscriptions.feeds()} if self._store else {}
            bindings = {identity: binding for identity in feeds if self._store and not self.stopped and (binding := self._store.subscriptions.telegram.binding(identity))}
            state = self._status()
        if candidates is not None and self._store:
            self._last_problems = self._store.problems.reconcile(candidates, self._wall_clock(), feeds)
            if not self._error:
                state['problems'] = self._last_problems
        return StatusSnapshot(state, bindings)

    def _status(self) -> dict[str, Any]:
        state: dict[str, Any] = self._store.status() if self._store and not self.stopped else dict(jobs=[], errors=[], deleting=False)
        state['youtube'] = self.youtube_status() if self._store and not self.stopped else None
        if self._store and not self.stopped:
            self._viewed_ids = [row['id'] for row in self._store.db.execute('SELECT id FROM items WHERE viewed=1')]
        state['problems'] = self._last_problems
        if self._error:
            state['problems'] = dict(current=[dict(id='runtime:storage',code='storage-unavailable',operation='producer:storage',section='action',title='Хранилище недоступно',
                consequence='Сбор и обработка остановлены.',next_step='Проверьте доступность хранилища; состояние не сбрасывается.',
                objects=[],action=None,created_at=self._wall_clock(),next_at=None,state='current',finished_at=None,outcome=None)],recent=[],count=1,updated_at=self._wall_clock())
        state['viewed_ids'] = self._viewed_ids
        pending = any(j['status'] in ('pending', 'running') for j in state['jobs'])
        source_states = self._store.source_statuses() if self._store else []
        names = {f['id']: f['title'] for f in self._store.subscriptions.feeds()} if self._store else {}
        source_states = [dict(s, title=names.get(s['feed_id'], s['feed_id']), changes=self._collection.description(s['feed_id'])) for s in source_states if s['feed_id'] in self._sources]
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
            stage = 'Сбор с ошибками' if any(not task.done() for task in self._lanes.values()) else 'Ожидание повтора'
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

    async def report_action(self, identity: str, context: dict[str, Any], result: str) -> str:
        async with self._actions:
            if any(obj['kind'] == 'material' and self.material_was_viewed(obj['id']) for obj in context['objects']):
                # Keep the neutral observation outcome while forgetting UI resources.
                return self.require_store().problems.report_action(identity, dict(context, objects=[]), 'abandoned', self._wall_clock())
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

    def require_work(self) -> None:
        self.require_store()
        if not self.enabled:
            raise ActionError('conflict', 'Око выключено; сначала включите Око', expected=True, code='runtime_disabled')

    async def set_power(self, enabled: bool) -> None:
        async with self._actions:
            current = self.require_store()
            current.set_work_enabled(enabled)
            self.enabled = enabled
            if not enabled:
                self._feed_checks.clear()
                await self._cancel()

    async def collect(self, feed: str | None = None, *, folder: str | None = None) -> None:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            self._sync_sources()
            if feed is not None:
                saved = next((entry for entry in current.subscriptions.feeds() if entry['id']==feed),None)
                if saved is None:
                    raise ActionError('missing','Фид недоступен')
                if saved['connection']=='deleting':
                    raise ActionError('conflict','Фид удаляется',code='deletion_in_progress')
            if folder is not None and not any(f['id']==folder for f in current.subscriptions.folders()):
                raise ActionError('missing','Папка недоступна')
            selected = {f['id'] for f in current.subscriptions.feeds() if f['connection']!='deleting' and (folder is None or f['folder_id']==folder)}
            for source_feed in selected:
                if feed is None or feed==source_feed:
                    current.refresh_feed(source_feed)

    async def retry_material(self, item: str) -> None:
        async with self._actions:
            self.require_work()
            if not self.require_store().retry_material(item):
                raise ActionError('missing', 'Материал недоступен')
            await self._cancel({self.require_store().item_feed(item)})

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

    def _completion_preview(self, folder: str, current: Store, scope: Literal['folder', 'feed'] = 'folder') -> dict[str, Any]:
        try:
            contents = current.reading.feed_contents(folder) if scope == 'feed' else current.folder_contents(folder)
        except LookupError as error:
            record_caught('runtime._completion_preview')
            raise ActionError('missing', str(error)) from None
        token = secrets.token_urlsafe(32)
        # Confirmation handles contain only service IDs, never publication text.
        # Bound their lifetime to this run and their number to 128 previews.
        if len(self._completions) >= 128:
            del self._completions[next(iter(self._completions))]
        self._completions[token] = ((scope, folder), contents)
        return dict(token=token, cards=len(contents.cards), originals=len(contents.members))

    async def preview_completion(self, folder: str) -> dict[str, Any]:
        async with self._actions:
            return self._completion_preview(folder, self.require_store())

    async def preview_feed_completion(self, feed: str) -> dict[str, Any]:
        async with self._actions:
            return self._completion_preview(feed, self.require_store(), 'feed')

    async def complete_feed(self, feed: str, token: str) -> None:
        await self.complete_folder(feed, token, scope='feed')

    async def complete_folder(self, folder: str, token: str, *, scope: Literal['folder', 'feed'] = 'folder') -> None:
        async with self._actions:
            current = self.require_store()
            saved = self._completions.get(token)
            if not saved or saved[0] != (scope, folder):
                raise ActionError('conflict', dict(message='Подтверждение устарело. Проверьте количество снова.', preview=self._completion_preview(folder, current, scope)))
            contents = saved[1]
            if contents is not None:
                try:
                    latest = current.reading.feed_contents(folder) if scope == 'feed' else current.folder_contents(folder)
                except LookupError as error:
                    record_caught('runtime.complete_folder')
                    raise ActionError('missing', str(error)) from None
                if latest != contents:
                    raise ActionError('conflict', dict(message='Состав фида изменился. Подтвердите новое количество.' if scope == 'feed' else 'Состав папки изменился. Подтвердите новое количество.', preview=self._completion_preview(folder, current, scope)))
                current.viewed_set([identity for identity, _ in contents.members])
                if self.diagnostics:
                    self.diagnostics.forget_materials({identity for identity, _ in contents.members} | set(contents.cards))
                self._completions[token] = ((scope, folder), None)
            await self._drain_deletions(current)
            if current.status()['deleting']:
                raise ActionError('unavailable', 'Удаление не завершено. Око повторит попытку; ошибка видна в статусе.')

    async def _drain_deletions(self, current: Store) -> None:
        feeds = {row[0] for row in current.db.execute("SELECT DISTINCT feed_id FROM items WHERE id IN (SELECT item FROM jobs WHERE kind='delete' AND status!='done')")}
        if feeds:
            await self._cancel(feeds)
        job = current.next_job(self._clock(), deleting_only=True)
        while job:
            self._delete(job, current)
            job = current.next_job(self._clock(), deleting_only=True)

    async def pause_history(self, identity: str, paused: bool) -> dict[str, Any]:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            if paused:
                raise ActionError('conflict', 'Ручная пауза истории удалена; используйте обновление фида')
            try:
                result = current.pause_history(identity, False)
            except LookupError as error:
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                raise ActionError('invalid', str(error)) from None
            self._sync_sources()
            current.refresh_feed(identity)
            return result

    async def change_history(self, identity: str, selection: InitialHistory | None) -> dict[str, Any]:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            try:
                result = current.change_history(identity, selection)
            except LookupError as error:
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                raise ActionError('conflict', str(error), expected=True) from None
            await self._cancel()
            self._sync_sources()
            current.enqueue_discovery(identity)
            return result

    async def rename_folder(self, identity: str, title: str) -> dict[str, Any]:
        async with self._actions:
            try:
                return self.require_store().subscriptions.rename_folder(identity, title)
            except LookupError as error:
                raise ActionError('missing', str(error), expected=True) from None
            except FolderConflict as error:
                raise ActionError('conflict', str(error), code=error.code, expected=True) from None

    async def remove_folder(self, identity: str) -> None:
        async with self._actions:
            try:
                self.require_store().subscriptions.remove_folder(identity)
            except LookupError as error:
                raise ActionError('missing', str(error), expected=True) from None
            except FolderConflict as error:
                raise ActionError('conflict', str(error), code=error.code, expected=True) from None

    async def patch_feed(self, identity: str, body: FeedPatch) -> dict[str, Any]:
        async with self._actions:
            current = self.require_store()
            try:
                known = current.subscriptions.feeds(identity)
                if not known:
                    raise LookupError('Фид не найден')
                previous = known[0]
                result = current.subscriptions.update_feed(identity, body)
            except LookupError as error:
                record_caught('runtime.patch_feed')
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                record_caught('runtime.patch_feed')
                raise ActionError('invalid', str(error)) from None
            if result != previous:
                if body.model_fields_set & {'folder_id','new_folder','importance','public_ai','transport'}:
                    current.settings_changed('feed', identity)
                if body.model_fields_set - {'title'}:
                    await self._cancel({identity})
            if 'transport' in body.model_fields_set:
                self._sync_sources()
            return result

    async def add_feed(self, body: AddFeed) -> dict[str, Any]:
        async with self._actions:
            self.require_work()
            try:
                current = self.require_store()
                if not self._live():
                    raise ActionError('conflict', 'Откройте читатель для добавления фида', expected=True)
                url = normalized(body.url)
                if self._feed_checks.get(body.verification) != url:
                    raise ActionError('invalid', 'Сначала проверьте текущий URL фида', expected=True, code='verification_expired')
                return current.subscriptions.add_feed(body.model_copy(update={'url': url}))
            except ValueError as error:
                record_caught('runtime.add_feed')
                raise ActionError('invalid', str(error)) from error

    async def verify_feed(self, body: FeedURL) -> dict[str, Any]:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для проверки фида')
            check = _ExternalCheck(self, current)
            try:
                url = normalized(body.url)
                _, kind, _ = identify(url)
                if kind not in ('rss', 'youtube'):
                    raise ValueError('Для Telegram используйте подключение Telegram-источника')
                diagnostic_source.set('Проверка фида · ' + url)
                existing = current.subscriptions.existing_feed(url)
            except (ValueError, SourceError) as error:
                raise ActionError('invalid', error.label if isinstance(error, SourceError) else str(error)) from error
            async def download_title() -> str:
                async with asyncio.timeout(25):
                    raw, _, _ = await RSSSource(url, 1, self._rss_transport).download(url)
                    return feed_title(raw)
            pending = check.start(download_title()) if not existing else None
        try:
            title = existing['title'] if existing else await pending if pending else ''
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller and caller.cancelling():
                raise
            raise ActionError('conflict', 'Проверка фида прервана; повторите действие', expected=True) from None
        except (SourceError, TimeoutError, httpx.InvalidURL) as error:
            raise ActionError('invalid', error.label if isinstance(error, SourceError) else 'Неверный URL фида' if isinstance(error, httpx.InvalidURL) else 'Проверка фида не завершилась за 25 секунд', code='source_unavailable' if isinstance(error, (SourceError, TimeoutError)) else None) from error
        async with check.accept() as current:
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
                previous = current.subscriptions.effective(scope, owner)
                result = current.subscriptions.update_settings(scope, owner, body)
                self._collection_interval = current.subscriptions.feed_refresh_minutes() * 60
                if body.model_fields_set - GLOBAL_SERVICE_SETTINGS and result != previous:
                    current.settings_changed(scope, owner)
                    affected = None if scope == 'oko' else {f['id'] for f in current.subscriptions.feeds(owner if scope == 'feed' else None, folder=owner if scope == 'folder' else None)}
                    await self._cancel(affected)
                return result
            except LookupError as error:
                record_caught('runtime.update_settings')
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                record_caught('runtime.update_settings')
                raise ActionError('invalid', str(error)) from None

    async def configure_processor(self, config: ProcessorConfig) -> None:
        async with self._actions:
            if self.require_store().configure_processor(config):
                await self._cancel()

    async def check_processor(self) -> dict[str, Any]:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для подключения')
            config = current.processor_config()
            if not config:
                raise ActionError('conflict', 'llama.cpp не настроен')
            check = _ExternalCheck(self, current)
            pending = check.start(LlamaCpp(config, self._ai_transport).check())
        try:
            result = await pending
        except ProcessingError as error:
            record_caught('runtime.check_processor')
            raise ActionError('conflict', error.label) from None
        except asyncio.CancelledError:
            raise ActionError('conflict', 'Проверка прервана', expected=True) from None
        async with check.accept():
            return result

    def codex_status(self) -> dict[str, Any]:
        self.require_store()
        config = self.require_store().codex_config()
        return dict(self._codex_status, config=config.model_dump() if config else None)

    async def codex_models(self) -> list[dict[str, Any]]:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для подключения')
            check = _ExternalCheck(self, current)
            pending = check.start(self._codex_client.catalog())
        try:
            result = await pending
        except ProcessingError as error:
            raise ActionError('conflict', error.label) from None
        except asyncio.CancelledError:
            raise ActionError('conflict', 'Загрузка каталога прервана', expected=True) from None
        async with check.accept():
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
            self.require_work()
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для подключения')
            check = _ExternalCheck(self, current)
            pending = check.start(self._codex_client.check())
        try:
            result = await pending
        except asyncio.CancelledError:
            raise ActionError('conflict', 'Проверка Codex прервана', expected=True) from None
        async with check.accept():
            self._codex_status = result
        return result

    async def restore_filter(self, item: str) -> None:
        async with self._actions:
            if self.require_store().publications.owns(item):
                raise ActionError('conflict','Восстановление фильтра недоступно; исходник сохранён',expected=True)
            if not self.require_store().filters.restore(item):
                raise ActionError('missing', 'Обработанный материал недоступен')
            await self._cancel()

    async def retry_filter(self, item: str) -> None:
        async with self._actions:
            self.require_work()
            if not self.require_store().filters.retry(item):
                raise ActionError('missing', 'Фильтрация не включена')
            await self._cancel()

    def processing_wait(self, item: str, kind: Literal['filter', 'theses']) -> 'ProcessingWait | None':
        return self.require_store().processing.waiting(item, kind, self._clock(), self._wall_clock())

    async def retry_theses(self, item: str) -> None:
        async with self._actions:
            self.require_work()
            if not self.require_store().processing.retry_theses(item):
                raise ActionError('missing', 'Тезисы для материала не включены')

    async def regenerate(self, item: str) -> None:
        async with self._actions:
            self.require_work()
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

    async def telegram_profiles(self) -> dict[str, Any]:
        current = self.require_store()
        accounts = (await self._call_telegram(lambda client: client.accounts()))[0] if self._telegram_client and self._live() else []
        from oko.reader.api_v3 import timestamp
        result: dict[str, Any] = dict(configurations=[dict(configuration_ref=a, label=a) for a in accounts])
        for role in ('personal', 'collection'):
            profile = current.subscriptions.telegram.profile(role)
            if not profile:
                result[role] = dict(role=role, state='unconfigured')
                continue
            value = dict(role=role, state=profile['state'], configuration_ref=profile['account'],
                         verified_user_id=str(profile['user_id']), checked_at=timestamp(profile['checked']).root)
            if profile['state'] != 'ready':
                value.update(reason='account_identity_mismatch' if profile['state'] == 'identity_mismatch' else 'profile_unavailable', message=profile['message'] or 'Профиль недоступен')
            result[role] = value
        return result

    async def save_telegram_profile(self, role: str, account: str) -> dict[str, Any]:
        from oko.subscriptions.telegram import TelegramConflict
        async with self._actions:
            self.require_work()
            current, _ = self._telegram_ready()
            old = current.subscriptions.telegram.profile(role)
            generation = old['generation'] if old else 0
        try:
            user_id, check = await self._call_telegram(lambda client: client.identity(account))
        except ActionError:
            raise TelegramConflict('profile_unavailable', dict(retry_at=None), 'Не удалось проверить существующую сессию', 503) from None
        if type(user_id) is not int or user_id <= 0:
            raise TelegramConflict('profile_unavailable', dict(retry_at=None), 'Клиент не подтвердил user ID', 503)
        async with check.accept() as current:
            current.subscriptions.telegram.save_profile(role, account, user_id, generation, self._wall_clock())
            await self._cancel()
            self._sources.clear()
            self._sync_sources()
        result = (await self.telegram_profiles())[role]
        assert isinstance(result, dict)
        return result

    async def _verify_telegram_profile(self, role: str) -> dict[str, Any]:
        from oko.subscriptions.telegram import TelegramConflict
        current = self.require_store()
        profile = current.subscriptions.telegram.profile(role)
        if not profile:
            raise TelegramConflict('profile_not_configured', dict(role=role), 'Профиль Telegram не настроен')
        verification = _ExternalCheck(self, current)
        try:
            user_id, check = await self._call_telegram(lambda client: client.identity(profile['account']))
        except ActionError as error:
            async with verification.accept(failure=error) as current:
                current.subscriptions.telegram.checked(role, profile['generation'], None, self._wall_clock())
            raise TelegramConflict('profile_unavailable', dict(retry_at=None), 'Сессия Telegram недоступна', 503) from None
        async with check.accept():
            if not current.subscriptions.telegram.checked(role, profile['generation'], user_id, self._wall_clock()):
                raise TelegramConflict('account_identity_mismatch', dict(role=role), 'Личность аккаунта изменилась')
            if isinstance(self._telegram_client, LocalTelegram):
                self._telegram_client.expected_users[profile['account']] = user_id
        return profile

    async def connect_telegram_binding(self, source: str, folder: str, history: InitialHistory | None, role: str | None, auto_read: bool = False) -> dict[str, Any]:
        from oko.subscriptions.telegram import locator, TelegramConflict
        kind, address = locator(source)
        async with self._actions:
            self.require_work()
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для подключения', expected=True)
            duplicates = current.subscriptions.telegram.owners([(kind, str(address))])
            if duplicates:
                raise TelegramConflict('duplicate_source', current.subscriptions.telegram.duplicates(duplicates), 'Источник уже подключён')
            connection = _ExternalCheck(self, current)
        profile = await self._verify_telegram_profile(role) if role else None
        if profile:
            info, check = await self._call_telegram(lambda client: client.info(profile['account'], '@' + str(address) if kind == 'username' else address))
            connection = connection.follow(check)
            binding = dict(kind='client', role=role, account_user_id=str(profile['user_id']), peer_id=str(info['peer']))
        else:
            if kind != 'username':
                raise ValueError('Веб-доступ требует публичный username')
            from oko.collection.telegram.web import preview
            async with connection.accept(require_work=True):
                pending = connection.start(preview(str(address), self._telegram_http))
            try:
                info = await pending
            except SourceError as error:
                raise TelegramConflict('source_unavailable', dict(retry_at=None), error.label, 503) from None
            binding = dict(kind='web', username=info['username'], preview_url=info['preview_url'])
        async with connection.accept() as current:
            if profile:
                latest = current.subscriptions.telegram.profile(str(role))
                if not latest or latest['generation'] != profile['generation']:
                    raise TelegramConflict('profile_unavailable', dict(retry_at=None), 'Профиль изменился во время подключения', 503)
            result = current.subscriptions.telegram.connect(binding, info, source, folder, history, auto_read, current.subscriptions)
            self._sync_sources()
            return result

    def _telegram_ready(self) -> tuple[Store, TelegramClient]:
        current = self.require_store()
        if not self._live():
            raise ActionError('conflict', 'Откройте читатель для подключения')
        if not self._telegram_client:
            raise ActionError('conflict', 'Telegram-клиент не настроен')
        return current, self._telegram_client

    async def _call_telegram(self, call: Callable[[TelegramClient], Awaitable[T]]) -> tuple[T, _ExternalCheck]:
        async with self._actions:
            self.require_work()
            current, client = self._telegram_ready()
            check = _ExternalCheck(self, current)
            pending = check.start(call(client))
        try:
            # Network work is deliberately outside the mutation seam.
            result = await pending
        except SourceError as error:
            record_caught('runtime._call_telegram')
            raise ActionError('conflict', error.label) from None
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller and caller.cancelling():
                raise
            raise ActionError('conflict', 'Око остановлено' if self.stopped else 'Подключение Telegram прервано; повторите действие', expected=True) from None
        async with check.accept():
            return result, check

    async def telegram_accounts(self) -> list[str]:
        result, _ = await self._call_telegram(lambda client: client.accounts())
        return result

    async def connect_telegram(self, body: ConnectTelegram) -> dict[str, Any]:
        # Transitional endpoint requires the same explicit profile confirmation.
        from oko.subscriptions.telegram import TelegramConflict
        current = self.require_store()
        profile = next((current.subscriptions.telegram.profile(role) for role in ('personal', 'collection')
                        if (current.subscriptions.telegram.profile(role) or {}).get('account') == body.account), None)
        if not profile:
            raise ActionError('conflict', 'Сначала сопоставьте аккаунт профилю Telegram', expected=True)
        try:
            if not body.feed_id:
                return await self.connect_telegram_binding(body.peer, body.folder, body.initial_history, profile['role'], body.auto_read)
            profile = await self._verify_telegram_profile(profile['role'])
            if profile['account'] != body.account:
                raise ActionError('conflict', 'Профиль изменился; повторите подключение', expected=True)
            info, check = await self._call_telegram(lambda client: client.info(body.account, int(body.peer) if body.peer.startswith('-') else body.peer))
            async with check.accept(require_work=True) as current:
                latest = current.subscriptions.telegram.profile(profile['role'])
                if not latest or latest['generation'] != profile['generation']:
                    raise ActionError('conflict', 'Профиль изменился; повторите подключение', expected=True)
                result = current.subscriptions.telegram.adopt(body.feed_id, profile, info, body.auto_read, current.subscriptions)
                await self._cancel()
                self._sources.pop(body.feed_id, None)
                self._sync_sources()
                return result
        except TelegramConflict as error:
            raise ActionError('conflict', str(error), expected=True, code=error.code) from None


    async def remove_feed(self, identity: str) -> None:
        async with self._actions:
            current = self.require_store()
            members = {r[0] for r in current.db.execute('SELECT id FROM items WHERE feed_id=?', (identity,))}
            current.remove_feed(identity)
            if self.diagnostics:
                self.diagnostics.forget_materials(members)
            await self._cancel({identity})
            self._sources.pop(identity, None)
            self._telegram_bindings.pop(identity, None)

    def _delete(self, job: Job, current: Store) -> None:
        try:
            if job.kind == 'feed-delete':
                current.finish_feed_deletion(job)
            else:
                current.delete(job)
        except Exception:
            record_caught('runtime._delete')
            if current.valid(job):
                current.retry(job, self._clock(), 'Ошибка удаления', code='delete')

    async def _execute_read(self, job: Job, current: Store, source: Source) -> None:
        binding = self._telegram_bindings.get(job.item)
        client = self._telegram_client
        if not client or not binding or not binding.read or not isinstance(source, TelegramSource):
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
        state = await client.read_state(source.account, source.peer, request['since'], )
        state['read_max'] = max(state['read_max'], current.read_sync.confirmed(job.item))
        window: dict[str, Any] = {}
        plan = current.read_sync.plan(request, state)
        confirmed = plan.confirmed
        if plan.until > confirmed:
            window = await client.read_window(source.account, source.peer, confirmed, plan.until, )
            plan = current.read_sync.plan(request, state, window)
        async with self._actions:
            if source.identity_info and self._store is current and current.valid(job):
                current.subscriptions.telegram.refresh(job.item, source.identity_info)
            latest = current.read_sync.request(job.item) if self._store is current else None
            if not self._live() or not current.valid(job) or not latest or current.read_sync.signature(latest) != signature:
                return
        if plan.candidate > confirmed:
            if 'topics' in window:
                topics: dict[int, int] = {}
                for member in window['ids']:
                    if member <= plan.candidate and member not in window.get('read_ids', []):
                        topic = int(window['topics'][str(member)])
                        topics[topic] = max(topics.get(topic, 0), member)
                for topic, maximum in sorted(topics.items()):
                    async with self._actions:
                        latest = current.read_sync.request(job.item) if self._store is current else None
                        if not self._live() or not current.valid(job) or not latest or current.read_sync.signature(latest) != signature:
                            return
                        if current.read_sync.topic_confirmed(job.item, topic) >= maximum:
                            continue
                    await client.acknowledge(source.account, source.peer, maximum, topic_id=topic)
                    async with self._actions:
                        if self._store is current and self._live() and current.valid(job):
                            current.read_sync.topic_completed(job.item, topic, maximum)
                    self._fault('after_telegram_acknowledge')
            else:
                await client.acknowledge(source.account, source.peer, plan.candidate, )
                self._fault('after_telegram_acknowledge')
            confirmed = plan.candidate
        async with self._actions:
            if self._store is current and self._live() and current.valid(job):
                current.read_sync.completed(job, plan, confirmed, self._clock())

    def _lane(self, feed: str) -> str:
        binding = self._telegram_bindings.get(feed)
        return binding.lane if binding else 'common'

    def _account_wait(self, feed: str, error: SourceError | None, job: Job | None = None) -> None:
        binding = self._telegram_bindings.get(feed)
        if not error or error.remote_type != 'FloodWaitError' or not binding or not binding.read or not self._store:
            return
        ready = self._clock() + error.delay
        self._account_retry[binding.read.user_id] = ready
        with self._store.db:
            self._store.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('telegram-retry:'+str(binding.read.user_id), str(self._wall_clock() + error.delay)))
            if job:
                self._store.db.execute("UPDATE jobs SET problem_code=? WHERE id=? AND generation=? AND status='pending'", (job.kind+':FloodWaitError', job.id, job.generation))

    def _clean_collection(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.diagnostics.clean(value) if self.diagnostics else value
        if isinstance(value, list):
            return [self._clean_collection(item) for item in value]
        if isinstance(value, dict):
            result = {key: self._clean_collection(item) for key, item in value.items()}
            if 'url' in value and result['url'] != value['url']:
                result['redacted'] = True
            return result
        return value

    def _finish_collection(self, job: Job, current: Store, error: BaseException | None = None) -> None:
        attempt = self._collection_active.get(job.id)
        if not attempt or attempt.job.generation != job.generation:
            return
        del self._collection_active[job.id]
        capture = attempt.capture
        details = capture.error or (exception_details(error) if error else None)
        if isinstance(error, SourceError) and error.remote_type:
            details = dict(error_type=error.remote_type, message=error.remote_message, traceback=error.remote_traceback, origin='worker')
        observation = self._clean_collection(dict(error=details, requests=capture.requests, request_count=capture.request_count))
        current.collection_status.finish(job, attempt.feed, attempt.saved, max(0., self._clock()-attempt.started), observation)

    async def _execute(self, job: Job) -> None:
        capture = Capture()
        with capture.scope():
            await self._execute_observed(job, capture)

    async def _execute_observed(self, job: Job, capture: Capture) -> None:
        if job.kind == 'download_media':
            await self._execute_media(job)
            return
        diagnostic_materials.set((job.item,) if job.kind in ('fetch','delete') else ())
        capture_scope.set(secrets.token_hex(16))
        current: Store | None = None
        feed = ''
        staging: DownloadStage | None = None
        collection_error: BaseException | None = None
        try:
            async with self._actions:
                current = self._store
                if not current or not current.valid(job) or not self._live():
                    return
                if job.kind in ('delete', 'feed-delete'):
                    diagnostic_source.set('Удаление · ' + job.item)
                    self._delete(job, current)
                    return
                feed = (job.item or 'demo') if job.kind in ('discover', 'read') else current.item_feed(job.item)
                if job.kind in ('discover','fetch'):
                    self._collection_active[job.id] = _CollectionAttempt(job,feed,self._clock(),capture,current.collection_status.start(job,feed))
                diagnostic_source.set('Сбор · ' + feed + ' · ' + job.kind)
                source = self._sources.get(feed)
                staging = current.download_stage(job,lambda:self._store is current and self._live() and current.valid(job)) if job.kind == 'fetch' and source and source.fetch_requirements.staging else None
                publication = current.publication(job.item, staging) if job.kind == 'fetch' else None
                binding = self._telegram_bindings.get(feed)
                namespace = binding.cursor_key if binding else ''
                cursor = current.cursor(feed, namespace) if job.kind == 'discover' else None
                access = self._collection.telegram_access(feed)
                caption_attempt = CaptionAttempt(self, current, job) if job.kind == 'fetch' and source and source.fetch_requirements.captions else None
                resources = FetchResources(staging, caption_attempt, not current.youtube.terminal(job.item) if caption_attempt else True)
            if not source:
                return
            if access is not None:
                saved_binding = current.subscriptions.telegram.binding(feed)
                if not saved_binding or saved_binding['kind'] not in ('client', 'web'):
                    raise SourceError('Сопоставьте старый аккаунт профилю Telegram', code='identity-unconfirmed')
                if saved_binding['kind'] == 'client':
                    await self._verify_telegram_profile(saved_binding['role'])

            if job.kind == 'read':
                await self._execute_read(job, current, source)
            elif job.kind == 'discover':
                outcome = await source.discover(cursor)
                async with self._actions:
                    if self._store is current and self._live() and current.valid(job):
                        if outcome.observation.identity:
                            current.subscriptions.telegram.refresh(feed, outcome.observation.identity.as_info())
                        collection_error = outcome.retry_error
                        self._account_wait(feed, outcome.retry_error)
                        current.discovered(list(outcome.publications), outcome.cursor, job, feed, namespace, description=outcome.observation.description, successful=outcome.retry_error is None)
                        if outcome.continuation is not None:
                            delay = outcome.continuation
                            current.enqueue_discovery(feed, self._clock() + delay if delay else 0)
                        current.feed_status(feed, error=None, gap=outcome.observation.gap, changes=outcome.observation.changes)
                        self._finish_collection(job, current, collection_error)
            elif publication:
                fetched = await source.fetch(publication, resources)
                material = fetched.material
                collection_error = material.collection_error
                async with self._actions:
                    if self._store is current and self._live() and current.valid(job):
                        if fetched.observation.identity:
                            current.subscriptions.telegram.refresh(feed, fetched.observation.identity.as_info())
                        current.save(job, material, self._clock(), staging)
                        self._account_wait(feed, material.collection_error, job)
                        if caption_attempt and caption_attempt.attempted and not caption_attempt.blocked:
                            current.youtube.completed(job.item, material.captions == 'available')
                        if caption_attempt and caption_attempt.retry:
                            current.youtube.defer(job, self._clock(), current.youtube.ready_at() - self._wall_clock(), 'Получение субтитров приостановлено: YouTube ограничил запросы', blocked=True)
                        current.read_sync.sync()
                        current.feed_status(feed, error=None, gap=fetched.observation.gap, changes=fetched.observation.changes)
                        self._finish_collection(job, current, collection_error)
        except CaptionsDeferred:
            async with self._actions:
                if current and self._store is current and not self.stopped:
                    self._finish_collection(job,current)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            collection_error = error
            record_caught('runtime._execute')
            async with self._actions:
                if current and self._store is current and not self.stopped and current.valid(job):
                    if isinstance(error, SourceSkipped) and job.kind == 'fetch':
                        collection_error = None
                        current.viewed(job.item)
                        self._finish_collection(job,current)
                        return
                    if isinstance(error, SourceDeleted) and job.kind == 'fetch':
                        collection_error = None
                        current.source_removed(job)
                        self._finish_collection(job,current)
                        return
                    label = error.label if isinstance(error, SourceError) else str(error) if isinstance(error, TelegramConflict) else 'Ошибка синхронизации Telegram' if job.kind == 'read' else 'Ошибка сбора'
                    if isinstance(error, SourceError):
                        self._account_wait(feed, error)
                    if isinstance(error, SourceError):
                        code = error.code or 'source-unavailable'
                    elif isinstance(error, TelegramConflict):
                        code = error.code
                    elif job.kind == 'fetch' and isinstance(error, FileNotFoundError):
                        code = 'material-read-failed'
                    elif job.kind == 'fetch' and isinstance(error, OSError):
                        code = 'material-save-failed'
                    else:
                        code = job.kind + ':' + type(error).__name__
                    label = self.diagnostics.clean(label) if self.diagnostics else label
                    current.retry(job, self._clock(), label, error.delay if isinstance(error, SourceError) else 1,code=code,
                        terminal=isinstance(error, SourceError) and not error.retryable or job.kind=='fetch' and current.publications.owns(job.item) and isinstance(error,(ValueError,sqlite3.IntegrityError)),
                        issue=error.adapter_issue if isinstance(error,SourceError) else None)
                    if job.kind == 'read':
                        current.read_sync.failed(feed, label)
                    elif isinstance(error, SourceError):
                        current.feed_status(feed, error=label, gap=None)
                    self._finish_collection(job, current, error)

        finally:
            attempt = self._collection_active.get(job.id)
            if attempt and attempt.job.generation == job.generation:
                del self._collection_active[job.id]
            if staging:
                shutil.rmtree(staging.path,ignore_errors=True)

    async def _execute_media(self, job: Job) -> None:
        async with self._actions:
            current = self._store
            if not current or not self._live() or not current.valid(job):
                return
            download = current.media.begin(job)
            if download is None:
                current.finish(job)
                return
            adapter = self._collection.publication_adapter(current.item_feed(job.item))
        try:
            if adapter is None:
                raise ValueError('Publication adapter unavailable')
            with MediaOutput(download.temporary,lambda:self._store is current and self._live() and current.valid(job)) as output:
                actual = await adapter.download_media(download.publication, download.media, output)
            async with self._actions:
                if self._store is current and self._live() and current.valid(job):
                    current.media.complete(job, download, actual)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            async with self._actions:
                if self._store is current and self._live() and current.valid(job):
                    issue = error.issue if isinstance(error, AdapterException) else AdapterIssue('media-download',
                        'Не удалось сохранить вложение' if isinstance(error,OSError) else 'Нарушен контракт медиа',
                        AdapterErrorKind.temporary if isinstance(error,OSError) else AdapterErrorKind.permanent)
                    current.media.failed(job, issue)
        finally:
            current.media.release(download)

    async def run(self) -> None:
        journal_context = active_journal.set(self.diagnostics)
        try:
            self._store = Store(self._root, self._fault, wall_clock=self._wall_clock, clock=self._clock)
            self.enabled = self._store.work_enabled()
            viewed = {row['id'] for row in self._store.db.execute('SELECT id FROM items WHERE viewed=1')}
            if viewed and self.diagnostics:
                self.diagnostics.forget_materials(viewed)
            config = self._store.subscriptions
            self._collection_interval = config.feed_refresh_minutes() * 60
            if (self._source and self._source_feed == 'demo') or any(c['feed_id'] == 'demo' for c in self._store.cards()):
                config.ensure_demo()
            if self._source:
                config.check_owner('feed', self._source_feed)
                self._store.enqueue_discovery(self._source_feed)
        except (sqlite3.DatabaseError, OSError) as error:
            record_caught('runtime.run')
            self._error = str(error) if str(error).startswith('Telegram:') else 'Хранилище недоступно или повреждено. Состояние сохранено; сбор остановлен.'
        try:
            while not self.stopped:
                async with self._actions:
                    now = self._clock()
                    if self._store and self.reader_advance and now-self._reader_advanced >= .25:
                        self.reader_advance()
                        self._reader_advanced = now
                    if not self._live():
                        if not self._admitted():
                            await self._cancel()
                        if self._should_stop():
                            self._stop_requested.set()
                            break
                    elif self._store:
                        if now-self._last_media_cleanup>=3600:
                            self._store.media.cleanup()
                            self._last_media_cleanup=now
                        if now - self._ai_sync >= .5:
                            self._store.read_sync.sync()
                            self._store.processing.sync()
                            self._ai_sync = now
                        if now-self._filter_sync>=.5:
                            self._store.processing.sync_stage('filter')
                            self._filter_sync=now
                        if now-self._summary_sync>=.5:
                            self._store.processing.sync_stage('theses')
                            self._summary_sync=now
                        if self._ai and self._ai.done():
                            await self._ai
                            self._ai = None
                        if self._ai is None and not self._detached_ai and not self._store.status()['deleting']:
                            ai_job = self._store.next_job(now, ai_only=True)
                            if ai_job:
                                self._ai_feed = self._store.item_feed(ai_job.item)
                                self._ai = asyncio.create_task(self._execute_ai(ai_job))
                        self._sync_sources()
                        if now-self._last_collection >= self._collection_interval:
                            for feed in self._sources:
                                self._store.enqueue_discovery(feed)
                            self._last_collection = now
                        state = self._store.status()
                        for lane in ('common', 'personal', 'collection', 'media'):
                            task = self._lanes.get(lane)
                            if lane=='media' and task and not task.done() and self._media_job and not self._store.valid(self._media_job):
                                task.cancel()
                                self._detach_task(task)
                                del self._lanes[lane]
                                self._media_job = None
                                task = None
                            if task and task.done():
                                await task
                                del self._lanes[lane]
                                if lane=='media':
                                    self._media_job = None
                            profile = self._store.subscriptions.telegram.profile(lane) if lane in ('personal','collection') else None
                            ready_at = self._account_retry.get(profile['user_id'], 0) if profile else 0
                            if lane in self._lanes or now < ready_at:
                                continue
                            feeds = [feed for feed in self._sources if self._collection.publication_adapter(feed) is not None] if lane=='media' else [feed for feed in self._sources if self._lane(feed) == lane]
                            job = self._store.next_job(now, deleting_only=state['deleting'] or not feeds,
                                                       feeds=feeds, include_deletions=lane == 'common', media_only=lane=='media')
                            if job:
                                if lane=='media':
                                    self._media_job = job
                                self._lane_feeds[lane] = job.item if job.kind in ('discover', 'read', 'feed-delete') else self._store.item_feed(job.item)
                                self._lanes[lane] = asyncio.create_task(self._execute(job))
                await asyncio.sleep(.02)
        except Exception:
            record_caught('Главный цикл Око')
            raise
        finally:
            self._stop_requested.set()
            async with self._actions:
                await self._cancel()
                if self._store:
                    if self.reader_finish:
                        await self.reader_finish()
                    self._store.db.close()
                    self._store = None
                self._closed = True
            await self._telegram_http.close()
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
