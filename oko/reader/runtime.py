"""Current background scheduler; user scenarios live in the application modules."""
from oko.collection.request_observation import Capture
from oko.storage.reading import reading_snapshot
import asyncio
from datetime import datetime,timezone
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
from oko.collection.contracts import SourceError, SourceDeleted, SourceSkipped, SourceDeferred
from oko.collection.contracts import Source
from oko.collection.adapters import AdapterException, AdapterErrorKind, AdapterIssue
from oko.processing.llamacpp import LlamaCpp, ProcessorConfig
from oko.processing.contracts import ProcessingError
from oko.processing.execution import CodexSettings
from oko.processing.codex import Codex, CodexClient, LocalCodex, status as codex_status
from oko.reviews.coordination import PreparedWork
from oko.reader.telegram_http import TelegramHTTP
from oko.reader.source_http import SourceHTTP
from oko.reader.composition import FolderContents
from oko.storage.media import MediaOutput
from oko.storage.store import Job, Store
from oko.collection.telegram.contracts import TelegramBinding, ClientReadBinding
from oko.collection.telegram.web import WebTelegram
from oko.collection.telegram.source import LocalTelegram, TelegramClient, TelegramSource
from oko.subscriptions.subscriptions import AddFeed, FeedURL, Overrides, FeedPatch, FolderConflict, identify, GLOBAL_SERVICE_SETTINGS
from oko.collection.youtube.source import LocalCaptions
from oko.collection.contracts import CaptionsClient
from oko.collection.youtube.history import HistoryClient
from oko.subscriptions.history import InitialHistory
from oko.subscriptions.telegram import TelegramConflict
from oko.collection.youtube.access import CaptionsBlocked, CaptionsDeferred, proxy_url
from oko.diagnostics import Journal, active_journal, diagnostic_source, diagnostic_materials, capture_scope, record_caught, exception_details

from oko.execution import Execution, ActionError
from oko.application.jobs import JobControl, _CollectionAttempt
from oko.application.reader import ReaderApplication

T = TypeVar('T')










class Runtime:
    application: ReaderApplication

    def __init__(self, root: Path, source: Source | None, clock: Callable[[], float],
                 fault: Callable[[str], None], shutdown: Callable[[], None],
                 admitted: Callable[[], bool], should_stop: Callable[[], bool], source_feed: str, rss_enabled: bool, rss_transport: httpx.AsyncBaseTransport | None, telegram_client: TelegramClient | None, ai_transport: httpx.AsyncBaseTransport | None = None, codex_client: CodexClient | None = None, youtube_captions: CaptionsClient | None = None, youtube_history: HistoryClient | None = None, wall_clock: Callable[[], float] = time.time) -> None:
        self.execution = Execution(admitted)
        self._youtube_captions = youtube_captions or LocalCaptions()
        self._youtube_history = youtube_history
        self._wall_clock = wall_clock
        self._captions_active = False
        self._codex_client = codex_client or LocalCodex()
        self._ai_transport = ai_transport
        self._ai_sync = 0.0
        self._filter_sync = 0.0
        self._summary_sync = 0.0
        self._source_revision: int | None = None
        self._dependency_feeds: list[str] = []
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
            lambda: self._live() and not self.jobs._cancelling, lambda: self.execution._epoch,
            lambda: float(self.require_store().subscriptions.telegram_http_settings()['value']))
        self._source_http=SourceHTTP(rss_transport,wall_clock,
            lambda scope:self.require_store().source_http_until(scope),self._save_source_http_wait,
            lambda:self._live() and not self.jobs._cancelling,lambda:self.execution._epoch)
        self._collection = Sources(source, source_feed, rss_enabled, rss_transport, telegram_client,
                                   self._youtube_captions, self._youtube_history, self._telegram_http,self._source_http)
        self._sources = self._collection.sources
        self.jobs = JobControl(self.execution, self._sources, telegram_client, clock)
        self._telegram_bindings: dict[str, TelegramBinding] = {}
        self._last_collection = clock()
        self._last_media_cleanup = clock()
        self._closed = False
        self._account_retry: dict[int, float] = {}
        self._restored_wait_feeds: set[str] = set()
        self._reader_advanced = float('-inf')
        if isinstance(telegram_client, LocalTelegram):
            telegram_client.allowed = lambda: self._live() and not self.jobs._cancelling

    @property
    def diagnostics(self) -> Journal | None:
        return self.execution.diagnostics

    @diagnostics.setter
    def diagnostics(self, value: Journal | None) -> None:
        self.execution.diagnostics = value

    async def _save_source_http_wait(self, scope: str, server_delay: float | None, generation: int) -> float:
        async with self.execution._actions:
            if not self._live() or self.jobs._cancelling or generation!=self.execution._epoch:
                raise asyncio.CancelledError
            current=self.require_store()
            delay=max(current.subscriptions.retry_settings()['initial_seconds'],server_delay or 0)
            until=max(current.source_http_until(scope),self._wall_clock()+delay)
            current.source_http_wait(scope,until)
            return until

    async def _save_telegram_http_wait(self, group: str, server_delay: float | None, generation: int) -> float:
        async with self.execution._actions:
            if not self._live() or self.jobs._cancelling or generation != self.execution._epoch:
                raise SourceDeferred('Запрос Telegram прерван', code='source_unavailable')
            current = self.require_store()
            state = current.telegram_http_state(group)
            strikes = min(int(state.get('strikes', 0)) + 1, 7)
            delay = max(float(min(60 * 2 ** (strikes - 1), 3600)), server_delay or 0)
            until = max(float(state.get('until', 0)), self._wall_clock() + delay)
            current.telegram_http_wait(group, dict(until=until, strikes=strikes))
            return until

    def _live(self) -> bool:
        return not self.stopped and self.execution.enabled and self._admitted()

    async def admission_changed(self) -> None:
        async with self.execution._actions:
            if not self._live() and not self.stopped:
                await self.jobs.cancel()



    def _sync_sources(self) -> None:
        if not self.execution._store:
            return
        revision = self.execution._store.subscriptions.source_revision()
        if revision == self._source_revision:
            return
        settings = []
        feeds = self.execution._store.subscriptions.feeds()
        self._dependency_feeds=[feed['id'] for feed in feeds if feed['connection']!='deleting' and (feed['transport'] is not None or (binding:=self.execution._store.subscriptions.telegram.binding(feed['id'])) is not None and binding['kind']=='web')]
        self.execution._store.publications.sync_feeds([feed['id'] for feed in feeds])
        for feed in feeds:
            if feed['id'] == self._source_feed and self._source:
                continue
            binding = self.execution._store.subscriptions.telegram.binding(feed['id'])
            access = None
            if binding:
                profile = self.execution._store.subscriptions.telegram.profile(binding['role']) if binding['kind'] == 'client' else None
                access = TelegramAccess(binding['kind'], binding.get('username', feed.get('telegram_username')),
                    profile['account'] if profile else feed.get('telegram_account'),
                    int(binding['peer_id']) if binding['kind'] == 'client' else feed.get('telegram_peer'))
            settings.append(SourceSettings(feed['id'], feed['transport'], feed['original_urls'][0],
                feed['initial_days'], feed['initial_history'], feed['connection'] == 'deleting', access,
                self.execution._store.subscriptions.youtube_theses(feed['id']) if feed['transport'] == 'youtube' else False,
                ))
        updates = self._collection.configure(settings)
        for identity in updates.created:
            self.execution._store.enqueue_discovery(identity)
        for identity, want_captions in updates.captions:
            self.execution._store.prepare_youtube(identity, want_captions)
        self._telegram_bindings.clear()
        for feed in feeds:
            binding = self.execution._store.subscriptions.telegram.binding(feed['id'])
            bound_source = self._sources.get(feed['id'])
            if bound_source and binding and binding['kind'] == 'web' and feed['connection'] != 'deleting':
                self._telegram_bindings[feed['id']] = TelegramBinding(bound_source, 'telegram-web', 'common')
            if bound_source and binding and binding['kind'] == 'client' and feed['connection'] != 'deleting':
                profile = self.execution._store.subscriptions.telegram.profile(binding['role'])
                if profile:
                    state = self.execution._store.db.execute('SELECT initial FROM telegram_read_state WHERE feed=?', (feed['id'],)).fetchone()
                    read = ClientReadBinding(profile['account'], profile['user_id'], int(binding['peer_id']), profile['generation'], (state['initial'] or 0) if state else 0)
                    self._telegram_bindings[feed['id']] = TelegramBinding(bound_source, 'telegram', binding['role'], read)
                    wait = self.execution._store.db.execute('SELECT value FROM state WHERE key=?', ('telegram-retry:'+str(profile['user_id']),)).fetchone()
                    if wait and feed['id'] not in self._restored_wait_feeds:
                        deadline = self._account_retry.setdefault(profile['user_id'], self._clock() + max(0, float(wait[0]) - self._wall_clock()))
                        self.execution._store.restore_telegram_wait(feed['id'], deadline)
                        self._restored_wait_feeds.add(feed['id'])
        self._source_revision = revision

    @property
    def stopped(self) -> bool:
        return self.execution._stop_requested.is_set()

    def require_store(self) -> Store:
        """Existing read interface; callers do not own its lifetime."""
        if self.stopped:
            raise ActionError('conflict', 'Око остановлено')
        if self.execution._error or self.execution._store is None:
            raise ActionError('unavailable', self.execution._error or 'Хранилище не готово')
        return self.execution._store











    async def stop(self) -> None:
        # Stop intent closes admission immediately, even with queued actions.
        # The owned cleanup and storage close still run in serial order.
        self.execution._stop_requested.set()
        async with self.execution._actions:
            if not self._closed:
                await self.jobs.cancel()

    def require_work(self) -> None:
        self.require_store()
        if not self.execution.enabled:
            raise ActionError('conflict', 'Око выключено; сначала включите Око', expected=True, code='runtime_disabled')

    async def set_power(self, enabled: bool) -> None:
        async with self.execution._actions:
            current = self.require_store()
            current.set_work_enabled(enabled)
            self.execution.enabled = enabled
            if not enabled:
                self.application._feed_checks.clear()
                await self.jobs.cancel()































    async def _execute_ai(self, job: Job) -> None:
        capture_scope.set(secrets.token_hex(16))
        diagnostic_materials.set((job.item,))
        current: Store | None = None
        work: PreparedWork | None = None
        try:
            async with self.execution._actions:
                current = self.execution._store
                if not current or not current.valid(job) or not self._live():
                    return
                diagnostic_source.set('Обработка · ' + job.kind + ' · ' + job.item)
                current.publications.complete_empty(job.item,job.kind)
                if not current.valid(job):
                    with current.db:
                        current.publications.status(job.item)
                    return
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
            async with self.execution._actions:
                if self.execution._store is not current or not self._live() or not work.current():
                    return
                if not checked['local'] and not work.public_ai:
                    raise ProcessingError('Запрещена публичная AI-обработка; выберите локальную модель', code='ai-permission')
            self._ai_stage = work.stage
            result = (await adapter.generate(request, self._root / 'staging' / job.item)
                      if isinstance(adapter, Codex) else await adapter.generate(request))
            async with self.execution._actions:
                if self.execution._store is current and self._live():
                    work.accept(result)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            record_caught('runtime._execute_ai')
            async with self.execution._actions:
                if current and self.execution._store is current and not self.stopped and current.valid(job):
                    current.retry(job, self._clock(), error.label if isinstance(error, ProcessingError) else 'Ошибка AI-обработки', 30,
                                  code=error.code if isinstance(error, ProcessingError) else 'processing-failed')
        finally:
            if work:
                work.close()












    async def _execute_read(self, job: Job, current: Store, source: Source) -> None:
        binding = self._telegram_bindings.get(job.item)
        client = self._telegram_client
        if not client or not binding or not binding.read or not isinstance(source, TelegramSource):
            async with self.execution._actions:
                if self.execution._store is current and current.valid(job):
                    current.read_sync.suspend(job)
            return
        async with self.execution._actions:
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
        async with self.execution._actions:
            if source.identity_info and self.execution._store is current and current.valid(job):
                current.subscriptions.telegram.refresh(job.item, source.identity_info)
            latest = current.read_sync.request(job.item) if self.execution._store is current else None
            if not self._live() or not current.valid(job) or not latest or current.read_sync.signature(latest) != signature:
                return
        if plan.candidate > confirmed:
            await client.acknowledge(source.account, source.peer, plan.candidate, )
            self._fault('after_telegram_acknowledge')
            confirmed = plan.candidate
        async with self.execution._actions:
            if self.execution._store is current and self._live() and current.valid(job):
                current.read_sync.completed(job, plan, confirmed, self._clock())

    def _lane(self, feed: str) -> str:
        binding = self._telegram_bindings.get(feed)
        return binding.lane if binding else 'common'

    def _account_wait(self, feed: str, error: SourceError | None, job: Job | None = None) -> None:
        binding = self._telegram_bindings.get(feed)
        if not error or error.remote_type != 'FloodWaitError' or not binding or not binding.read or not self.execution._store:
            return
        ready = self._clock() + error.delay
        self._account_retry[binding.read.user_id] = ready
        with self.execution._store.db:
            self.execution._store.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('telegram-retry:'+str(binding.read.user_id), str(self._wall_clock() + error.delay)))
            if job:
                self.execution._store.db.execute("UPDATE jobs SET problem_code=? WHERE id=? AND generation=? AND status='pending'", (job.kind+':FloodWaitError', job.id, job.generation))


    def _finish_collection(self, job: Job, current: Store, error: BaseException | None = None) -> None:
        attempt = self.jobs._collection_active.get(job.id)
        if not attempt or attempt.job.generation != job.generation:
            return
        del self.jobs._collection_active[job.id]
        capture = attempt.capture
        details = capture.error or (exception_details(error) if error else None)
        if isinstance(error, SourceError) and error.remote_type:
            details = dict(error_type=error.remote_type, message=error.remote_message, traceback=error.remote_traceback, origin='worker')
        observation = self.application._clean_collection(dict(error=details, requests=capture.requests, request_count=capture.request_count))
        current.collection_status.finish(job, attempt.feed, attempt.saved, max(0., self._clock()-attempt.started), observation)

    async def _execute(self, job: Job) -> None:
        capture = Capture()
        with capture.scope():
            await self._execute_observed(job, capture)

    async def _execute_observed(self, job: Job, capture: Capture) -> None:
        if job.kind == 'download_media':
            await self._execute_media(job,capture)
            return
        diagnostic_materials.set((job.item,) if job.kind in ('fetch','delete') else ())
        capture_scope.set(secrets.token_hex(16))
        current: Store | None = None
        feed = ''
        collection_error: BaseException | None = None
        try:
            async with self.execution._actions:
                current = self.execution._store
                if not current or not current.valid(job) or not self._live():
                    return
                if job.kind in ('delete', 'feed-delete'):
                    diagnostic_source.set('Удаление · ' + job.item)
                    self.jobs._delete(job, current)
                    return
                feed = (job.item or 'demo') if job.kind in ('discover', 'read') else current.item_feed(job.item)
                if job.kind in ('discover','fetch'):
                    self.jobs._collection_active[job.id] = _CollectionAttempt(job,feed,self._clock(),capture,current.collection_status.start(job,feed))
                diagnostic_source.set('Сбор · ' + feed + ' · ' + job.kind)
                from oko.storage import publication_links
                linked=job.kind=='fetch' and publication_links.feedless(current,job.item)
                source = self._sources.get(feed)
                publication = current.publication(job.item) if job.kind == 'fetch' else None
                binding = self._telegram_bindings.get(feed)
                namespace = binding.cursor_key if binding else ''
                cursor = current.cursor(feed) if job.kind == 'discover' else None
                access = None if linked else self._collection.telegram_access(feed)
            if not source and not linked:
                return
            if access is not None:
                saved_binding = current.subscriptions.telegram.binding(feed)
                if not saved_binding or saved_binding['kind'] not in ('client', 'web'):
                    raise SourceError('Сопоставьте старый аккаунт профилю Telegram', code='identity-unconfirmed')
                if saved_binding['kind'] == 'client':
                    await self.application._verify_telegram_profile(saved_binding['role'])

            if job.kind == 'read':
                assert source is not None
                await self._execute_read(job, current, source)
            elif job.kind == 'discover':
                assert source is not None
                outcome = await source.discover(cursor)
                async with self.execution._actions:
                    if self.execution._store is current and self._live() and current.valid(job):
                        if outcome.observation.identity:
                            current.subscriptions.telegram.refresh(feed, outcome.observation.identity.as_info())
                        collection_error = outcome.retry_error
                        self._account_wait(feed, outcome.retry_error)
                        current.discovered(list(outcome.publications), outcome.cursor, job, feed, namespace, description=outcome.observation.description, successful=outcome.retry_error is None,source_order=outcome.source_order)
                        if outcome.continuation is not None:
                            delay = outcome.continuation
                            current.enqueue_discovery(feed, self._clock() + delay if delay else 0)
                        current.feed_status(feed, error=None, gap=outcome.observation.gap, changes=outcome.observation.changes)
                        self._finish_collection(job, current, collection_error)
            elif publication:
                if linked:
                    fetched=await self._collection.article_source().fetch(publication)
                else:
                    assert source is not None
                    fetched=await source.fetch(publication)
                material = fetched.material
                collection_error = material.collection_error
                async with self.execution._actions:
                    if self.execution._store is current and self._live() and current.valid(job):
                        if fetched.observation.identity:
                            current.subscriptions.telegram.refresh(feed, fetched.observation.identity.as_info())
                        current.save(job,material)
                        self._account_wait(feed, material.collection_error, job)
                        current.read_sync.sync()
                        current.feed_status(feed, error=None, gap=fetched.observation.gap, changes=fetched.observation.changes)
                        self._finish_collection(job, current, collection_error)
        except CaptionsDeferred:
            async with self.execution._actions:
                if current and self.execution._store is current and not self.stopped:
                    self._finish_collection(job,current)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            collection_error = error
            record_caught('runtime._execute')
            async with self.execution._actions:
                if current and self.execution._store is current and not self.stopped and current.valid(job):
                    if isinstance(error, SourceSkipped) and job.kind == 'fetch':
                        collection_error = None
                        current.viewed(job.item)
                        self._finish_collection(job,current)
                        return
                    if isinstance(error, SourceDeleted) and job.kind == 'fetch':
                        collection_error = None
                        with current.db:
                            current.db.execute('INSERT OR IGNORE INTO source_deleted VALUES (?)',(job.item,))
                            current.retry(job,self._clock(),error.label,terminal=True,
                                issue=error.adapter_issue or AdapterIssue('source-unavailable',error.label,AdapterErrorKind.unavailable))
                            from oko.storage import publication_links
                            publication_links.notify_parents(current,job.item)
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
                    label = self.execution.diagnostics.clean(label) if self.execution.diagnostics else label
                    current.retry(job, self._clock(), label, error.delay if isinstance(error, SourceError) else 1,code=code,
                        terminal=isinstance(error, SourceError) and not error.retryable or job.kind in ('discover','fetch') and isinstance(error,(ValueError,sqlite3.IntegrityError)),
                        issue=error.adapter_issue if isinstance(error,SourceError) else None)
                    if job.kind=='fetch':
                        from oko.storage import publication_links
                        publication_links.notify_parents(current,job.item)
                    if job.kind=='discover':
                        saved=current.db.execute('SELECT retry_at FROM jobs WHERE id=?',(job.id,)).fetchone()
                        current.subscriptions.history.discovery_state(feed,dict(history_waiting=True,history_error=label,
                            history_retry_at=saved['retry_at'] if saved else None))
                    if job.kind == 'read':
                        current.read_sync.failed(feed, label)
                    elif isinstance(error, SourceError):
                        current.feed_status(feed, error=label, gap=None)
                    self._finish_collection(job, current, error)

        finally:
            attempt = self.jobs._collection_active.get(job.id)
            if attempt and attempt.job.generation == job.generation:
                del self.jobs._collection_active[job.id]

    async def _execute_media(self, job: Job, capture: Capture) -> None:
        async with self.execution._actions:
            current = self.execution._store
            if not current or not self._live() or not current.valid(job):
                return
            from oko.storage import publication_links
            owner=current.media.owner(job.item)
            if owner is None:
                current.finish(job)
                return
            feed=current.item_feed(owner)
            binding=self._telegram_bindings.get(feed)
            native=binding if binding and binding.read and not publication_links.feedless(current,owner) else None
            if native and native.read:
                ready=self._account_retry.get(native.read.user_id,0)
                if ready>self._clock():
                    current.media.failed(job,AdapterIssue('telegram-flood-wait','Ожидание ограничения Telegram',AdapterErrorKind.source_wait,
                        scope='telegram:account:'+str(native.read.user_id),
                        retry_at=datetime.fromtimestamp(self._wall_clock()+ready-self._clock(),timezone.utc)))
                    return
            download = current.media.begin(job)
            if download is None:
                current.finish(job)
                return
            diagnostic_materials.set((owner,))
            diagnostic_source.set('Сбор · '+feed+' · download_media')
            capture_scope.set(secrets.token_hex(16))
            self.jobs._collection_active[job.id]=_CollectionAttempt(job,feed,self._clock(),capture,current.collection_status.start(job,feed))
            caption_attempt=CaptionAttempt(self,current,job) if download.media.key=='youtube:subtitles' else None
            adapter = self._collection.article_source().adapter if publication_links.feedless(current,download.publication_id) else self._collection.publication_adapter(feed,captions=caption_attempt)
        try:
            if adapter is None:
                raise ValueError('Publication adapter unavailable')
            if native:
                await self.application._verify_telegram_profile(native.lane)
                async with self.execution._actions:
                    if self.execution._store is not current or not self._live() or not current.valid(job):
                        raise asyncio.CancelledError
            with MediaOutput(download.temporary,lambda:self.execution._store is current and self._live() and current.valid(job)) as output:
                actual = await adapter.download_media(download.publication, download.media, output)
            async with self.execution._actions:
                if self.execution._store is current and self._live() and current.valid(job):
                    current.media.complete(job, download, actual)
                    if caption_attempt and caption_attempt.attempted:
                        current.youtube.completed()
                    self._finish_collection(job,current)
        except CaptionsDeferred:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            async with self.execution._actions:
                if self.execution._store is current and self._live() and current.valid(job):
                    issue = error.issue if isinstance(error, AdapterException) else AdapterIssue('media-download',
                        'Не удалось сохранить вложение' if isinstance(error,OSError) else str(error) if isinstance(error,(ActionError,TelegramConflict)) else 'Нарушен контракт медиа',
                        AdapterErrorKind.temporary if isinstance(error,(OSError,ActionError,TelegramConflict)) else AdapterErrorKind.permanent)
                    source_error=error.__cause__ if isinstance(error.__cause__,SourceError) else None
                    if native and native.read and source_error and source_error.remote_type=='FloodWaitError':
                        issue=AdapterIssue(issue.code,issue.message,AdapterErrorKind.source_wait,download.publication,download.media.key,
                            'telegram:account:'+str(native.read.user_id),datetime.fromtimestamp(self._wall_clock()+source_error.delay,timezone.utc))
                    if caption_attempt and caption_attempt.blocked:
                        issue=AdapterIssue(issue.code,issue.message,AdapterErrorKind.source_wait,download.publication,download.media.key,
                            'youtube:captions',datetime.fromtimestamp(current.youtube.ready_at(),timezone.utc))
                    if issue.kind!=AdapterErrorKind.notice:
                        record_caught('runtime._execute_media')
                    current.media.failed(job, issue)
                    self._finish_collection(job,current,None if issue.kind==AdapterErrorKind.notice else source_error or error)
                    if native:
                        self._account_wait(feed,source_error,job)
        finally:
            current.media.release(download)
            attempt=self.jobs._collection_active.get(job.id)
            if attempt and attempt.job.generation==job.generation:
                del self.jobs._collection_active[job.id]

    async def run(self) -> None:
        journal_context = active_journal.set(self.execution.diagnostics)
        try:
            self.execution._store = Store(self._root, self._fault, wall_clock=self._wall_clock, clock=self._clock)
            self.execution.enabled = self.execution._store.work_enabled()
            viewed = {row['id'] for row in self.execution._store.db.execute('SELECT id FROM items WHERE viewed=1')}
            if viewed and self.execution.diagnostics:
                self.execution.diagnostics.forget_materials(viewed)
            config = self.execution._store.subscriptions
            self._collection_interval = config.feed_refresh_minutes() * 60
            if (self._source and self._source_feed == 'demo') or any(c['feed_id'] == 'demo' for c in self.execution._store.cards()):
                config.ensure_demo()
            if self._source:
                config.check_owner('feed', self._source_feed)
                self.execution._store.enqueue_discovery(self._source_feed)
        except (sqlite3.DatabaseError,OSError,ValueError) as error:
            record_caught('runtime.run')
            self.execution._error = str(error) if str(error).startswith('Telegram:') else 'Хранилище недоступно или повреждено. Состояние сохранено; сбор остановлен.'
        try:
            while not self.stopped:
                async with self.execution._actions:
                    now = self._clock()
                    if self.execution._store and self.execution.reader_advance and now-self._reader_advanced >= .25:
                        self.execution.reader_advance()
                        self._reader_advanced = now
                    if not self._live():
                        if not self._admitted():
                            await self.jobs.cancel()
                        if self._should_stop():
                            self.execution._stop_requested.set()
                            break
                    elif self.execution._store:
                        if now-self._last_media_cleanup>=3600:
                            self.execution._store.media.cleanup()
                            self._last_media_cleanup=now
                        if now - self._ai_sync >= .5:
                            self.execution._store.read_sync.sync()
                            self.execution._store.processing.sync()
                            self._ai_sync = now
                        if now-self._filter_sync>=.5:
                            self.execution._store.processing.sync_stage('filter')
                            self._filter_sync=now
                        if now-self._summary_sync>=.5:
                            self.execution._store.processing.sync_stage('theses')
                            self._summary_sync=now
                        if self.jobs._ai and self.jobs._ai.done():
                            if not self.jobs._ai.cancelled():
                                await self.jobs._ai
                            self.jobs._ai = None
                        if self.jobs._ai is None and not self.jobs._detached_ai and not self.execution._store.status()['deleting']:
                            ai_job = self.execution._store.next_job(now, ai_only=True)
                            if ai_job:
                                self.jobs._ai_feed = self.execution._store.item_feed(ai_job.item)
                                self.jobs._ai = asyncio.create_task(self._execute_ai(ai_job))
                        self._sync_sources()
                        if now-self._last_collection >= self._collection_interval:
                            for feed in self._sources:
                                self.execution._store.enqueue_discovery(feed)
                            self._last_collection = now
                        state = self.execution._store.status()
                        caption_feeds=self._collection.caption_feeds()
                        for lane in ('common', 'personal', 'collection', 'media','captions'):
                            if lane=='captions' and not caption_feeds:
                                continue
                            media_lane=lane in ('media','captions')
                            task = self.jobs._lanes.get(lane)
                            if media_lane and task and not task.done() and lane in self.jobs._media_jobs and not self.execution._store.valid(self.jobs._media_jobs[lane]):
                                task.cancel()
                                self.jobs._detach_task(task)
                                del self.jobs._lanes[lane]
                                self.jobs._media_jobs.pop(lane,None)
                                task = None
                            if task and task.done():
                                if not task.cancelled():
                                    await task
                                del self.jobs._lanes[lane]
                                if media_lane:
                                    self.jobs._media_jobs.pop(lane,None)
                            profile = self.execution._store.subscriptions.telegram.profile(lane) if lane in ('personal','collection') else None
                            ready_at = self._account_retry.get(profile['user_id'], 0) if profile else 0
                            if lane in self.jobs._lanes or now < ready_at:
                                continue
                            feeds = [feed for feed in self._sources if self._collection.publication_adapter(feed) is not None] if media_lane else [feed for feed in self._sources if self._lane(feed) == lane]
                            if lane=='captions':
                                feeds=caption_feeds
                            dependencies=self._dependency_feeds if lane in ('common','media') else []
                            job = self.execution._store.next_job(now, deleting_only=state['deleting'] or not (feeds or dependencies),
                                                       feeds=feeds, include_deletions=lane == 'common', media_only=media_lane,captions_only=lane=='captions',dependency_feeds=dependencies)
                            if job:
                                if media_lane:
                                    self.jobs._media_jobs[lane] = job
                                self.jobs._lane_feeds[lane] = job.item if job.kind in ('discover', 'read', 'feed-delete') else self.execution._store.item_feed(job.item)
                                self.jobs._lanes[lane] = asyncio.create_task(self._execute(job))
                await asyncio.sleep(.02)
        except Exception:
            record_caught('Главный цикл Око')
            raise
        finally:
            self.execution._stop_requested.set()
            async with self.execution._actions:
                await self.jobs.cancel()
                if self.execution._store:
                    if self.execution.reader_finish:
                        await self.execution.reader_finish()
                    self.execution._store.db.close()
                    self.execution._store = None
                self._closed = True
            await self._source_http.close()
            await self._telegram_http.close()
            self._shutdown()
            active_journal.reset(journal_context)


class CaptionAttempt:
    """One runtime-owned caption call; waiting releases the collection slot."""
    def __init__(self, runtime: Runtime, store: Store, job: Job) -> None:
        self.runtime, self.store, self.job = runtime, store, job
        publication=store.media.owner(job.item) if job.kind=='download_media' else job.item
        assert publication is not None
        self.publication=publication
        self.attempted = False
        self.blocked = False

    def current(self) -> bool:
        return self.runtime.execution._store is self.store and self.runtime._live() and self.store.valid(self.job)

    async def fetch(self, video: str) -> str | None:
        runtime, store, job = self.runtime, self.store, self.job
        async with runtime.execution._actions:
            if not self.current():
                raise asyncio.CancelledError
            delay = store.youtube.ready_at() - runtime._wall_clock()
            if delay > 0 or runtime._captions_active:
                store.youtube.defer(job, runtime._clock(), max(delay, .1), 'Ожидание допуска получения субтитров')
                raise CaptionsDeferred
            config = store.youtube.config()
            client = LocalCaptions(proxy_url(config)) if isinstance(runtime._youtube_captions, LocalCaptions) else runtime._youtube_captions
            store.youtube.start(runtime._wall_clock())
            self.attempted = True
            runtime._captions_active = True
        try:
            try:
                text = await client.fetch(video)
            except CaptionsBlocked as error:
                async with runtime.execution._actions:
                    if not self.current():
                        raise asyncio.CancelledError
                    store.youtube.blocked(runtime._wall_clock(), error.retry_after)
                    self.blocked = True
                raise
            async with runtime.execution._actions:
                if not self.current():
                    raise asyncio.CancelledError
            return text
        finally:
            runtime._captions_active = False
