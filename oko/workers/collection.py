"""Collection cron owns source/media attempts and their adapter calls."""
from oko.collection.request_observation import Capture
import asyncio
from copy import deepcopy
from datetime import datetime,timezone
from collections.abc import Awaitable, Callable
import secrets
from typing import Any

import httpx

from oko.collection.sources import Sources
from oko.collection.contracts import SourceError, SourceDeleted, SourceSkipped, SourceDeferred
from oko.collection.contracts import Source
from oko.collection.adapters import AdapterException, AdapterErrorKind, AdapterIssue
from oko.collection.telegram.http import TelegramHTTP
from oko.collection.http import SourceHTTP
from oko.storage.media import MediaOutput, MediaDownload
from oko.storage.store import Store
from oko.storage.job_types import Job
from oko.collection.telegram.contracts import TelegramBinding
from oko.collection.telegram.source import LocalTelegram, TelegramClient, TelegramSource
from oko.collection.youtube.source import LocalCaptions
from oko.collection.contracts import CaptionsClient
from oko.collection.youtube.history import HistoryClient
from oko.subscriptions.telegram import TelegramConflict
from oko.collection.youtube.access import CaptionsBlocked, CaptionsDeferred, proxy_url
from oko.diagnostics import Journal, diagnostic_source, diagnostic_materials, capture_scope, record_caught, exception_details

from oko.execution import Execution, ActionError
from oko.workers.control import JobControl, _CollectionAttempt
from oko.services.collection import CollectionService
from oko.services.media import MediaService


class CollectionWorker:
    clean_collection: Callable[[dict[str, Any]], dict[str, Any]]
    verify_telegram_profile: Callable[[str], Awaitable[Any]]
    _collection_interval: float

    def __init__(self, execution: Execution, source: Source | None, clock: Callable[[],float],
                 fault: Callable[[str],None], source_feed: str, rss_enabled: bool,
                 rss_transport: httpx.AsyncBaseTransport | None, telegram_client: TelegramClient | None,
                 youtube_captions: CaptionsClient | None, youtube_history: HistoryClient | None,
                 wall_clock: Callable[[],float]) -> None:
        self.execution = execution
        self.service=CollectionService(execution.require_store)
        self.media=MediaService(execution.retained_store)
        self._youtube_captions = youtube_captions or LocalCaptions()
        self._youtube_history = youtube_history
        self._wall_clock = wall_clock
        self._captions_active = False
        self._read_sync_at = 0.0
        self._started=False
        self._source_revision: int | None = None
        self._dependency_feeds: list[str] = []
        self._source,self._clock,self._fault=source,clock,fault
        self._source_feed = source_feed
        self._telegram_client = telegram_client
        self._telegram_http = TelegramHTTP(rss_transport, clock, wall_clock,
            lambda group: self.require_store().jobs.telegram_http_state(group),
            self._save_telegram_http_wait,
            lambda: self._live() and not self.jobs._cancelling, lambda: self.execution._epoch,
            lambda: self.service.telegram_http_delay())
        self._source_http=SourceHTTP(rss_transport,wall_clock,
            lambda scope:self.require_store().jobs.source_http_until(scope),self._save_source_http_wait,
            lambda:self._live() and not self.jobs._cancelling,lambda:self.execution._epoch)
        self._collection = Sources(source, source_feed, rss_enabled, rss_transport, telegram_client,
                                   self._youtube_captions, self._youtube_history, self._telegram_http,self._source_http)
        self._sources = self._collection.sources
        self.jobs = JobControl(self.execution, self._sources, telegram_client, clock)
        self._telegram_bindings: dict[str, TelegramBinding] = {}
        self._last_collection = clock()
        self._last_media_cleanup = clock()
        self._account_retry: dict[int, float] = {}
        self._restored_wait_feeds: set[str] = set()
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
            with current.jobs.transaction(self._live):
                return current.jobs.source_http_wait(scope,server_delay)

    async def _save_telegram_http_wait(self, group: str, server_delay: float | None, generation: int) -> float:
        async with self.execution._actions:
            if not self._live() or self.jobs._cancelling or generation!=self.execution._epoch:
                raise SourceDeferred('Запрос Telegram прерван',code='source_unavailable')
            current=self.require_store()
            with current.jobs.transaction(self._live):
                return current.jobs.telegram_http_wait(group,server_delay)

    def _live(self) -> bool:
        return self.execution._live()

    def _sync_sources(self) -> None:
        if not self.execution._store or self.service.source_revision()==self._source_revision:
            return
        current=self.execution._store
        snapshot=self.service.configuration()
        self._dependency_feeds=snapshot.dependencies
        settings=[setting for setting in snapshot.settings if not (setting.feed==self._source_feed and self._source)]
        updates=self._collection.configure(settings)
        for identity in updates.created:
            current.jobs.enqueue_discovery(identity)
        for identity,want_captions in updates.captions:
            current.jobs.prepare_youtube(identity,want_captions)
        self._telegram_bindings.clear()
        for setting in snapshot.settings:
            feed=setting.feed
            binding=snapshot.bindings.get(feed)
            source=self._sources.get(feed)
            if source and binding and not setting.deleting:
                if binding['kind']=='web':
                    self._telegram_bindings[feed]=TelegramBinding(source,'telegram-web','common')
                elif feed in snapshot.reads:
                    read=snapshot.reads[feed]
                    self._telegram_bindings[feed]=TelegramBinding(source,'telegram',binding['role'],read)
                    if read.user_id in snapshot.waits and feed not in self._restored_wait_feeds:
                        deadline=self._account_retry.setdefault(read.user_id,self._clock()+max(0,snapshot.waits[read.user_id]-self._wall_clock()))
                        current.jobs.restore_telegram_wait(feed,deadline)
                        self._restored_wait_feeds.add(feed)
        self._source_revision=snapshot.revision

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


    async def _execute_read(self, job: Job, current: Store, source: Source) -> None:
        binding = self._telegram_bindings.get(job.item)
        client = self._telegram_client
        if not client or not binding or not binding.read or not isinstance(source, TelegramSource):
            async with self.execution._actions:
                if self.execution._store is current and current.jobs.valid(job):
                    with current.jobs.transaction(self._live):current.jobs.finish(job)
            return
        async with self.execution._actions:
            request = self.service.read_request(job.item)
            if not request:
                with current.jobs.transaction(self._live):current.jobs.finish(job)
                return
            signature = self.service.read_signature(request)
        await source.resolve()
        state = await client.read_state(source.account, source.peer, request['since'], )
        state['read_max'] = max(state['read_max'], self.service.read_confirmed(job.item))
        window: dict[str, Any] = {}
        plan = self.service.read_plan(request, state)
        confirmed = plan.confirmed
        if plan.until > confirmed:
            window = await client.read_window(source.account, source.peer, confirmed, plan.until, )
            plan = self.service.read_plan(request, state, window)
        async with self.execution._actions:
            if source.identity_info and self.execution._store is current and current.jobs.valid(job):
                self.service.refresh_identity(job.item, source.identity_info)
            latest = self.service.read_request(job.item) if self.execution._store is current else None
            if not self._live() or not current.jobs.valid(job) or not latest or self.service.read_signature(latest) != signature:
                return
        if plan.candidate > confirmed:
            await client.acknowledge(source.account, source.peer, plan.candidate, )
            self._fault('after_telegram_acknowledge')
            confirmed = plan.candidate
        async with self.execution._actions:
            if self.execution._store is current and self._live() and current.jobs.valid(job):
                with current.jobs.transaction(self._live):
                    self.service.read_completed(job.item,plan,confirmed)
                    current.jobs.finish_read(job,plan.waiting,self._clock())

    def _lane(self, feed: str) -> str:
        binding = self._telegram_bindings.get(feed)
        return binding.lane if binding else 'common'

    def _account_wait(self, feed: str, error: SourceError | None, job: Job | None = None) -> None:
        binding = self._telegram_bindings.get(feed)
        if not error or error.remote_type != 'FloodWaitError' or not binding or not binding.read or not self.execution._store:
            return
        ready = self._clock() + error.delay
        self._account_retry[binding.read.user_id] = ready
        with self.execution._store.jobs.transaction(self._live):
            self.execution._store.jobs.account_wait(binding.read.user_id,self._wall_clock()+error.delay,job)

    def _finish_collection(self, job: Job, current: Store, error: BaseException | None = None) -> None:
        attempt = self.jobs._collection_active.get(job.id)
        if not attempt or attempt.job.generation != job.generation:
            return
        capture = attempt.capture
        details = capture.error or (exception_details(error) if error else None)
        if isinstance(error, SourceError) and error.remote_type:
            details = dict(error_type=error.remote_type, message=error.remote_message, traceback=error.remote_traceback, origin='worker')
        observation = self.clean_collection(dict(error=details, requests=capture.requests, request_count=capture.request_count))
        elapsed = max(0., self._clock()-attempt.started)
        current.jobs.collection_finish(job, attempt.feed, deepcopy(attempt.saved), elapsed, observation)
        def committed() -> None:
            if self.jobs._collection_active.get(job.id) is attempt:
                del self.jobs._collection_active[job.id]
            if self.jobs.metrics:
                from oko.observability import event
                self.jobs.metrics.attempt(job.kind, 'error' if error else 'returned', elapsed)
                event('job_finished', job_id=job.id, kind=job.kind, level='error' if error else 'info', error=error, elapsed=elapsed)
        current.jobs.after_commit(committed)

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
                if not current or not current.jobs.valid(job) or not self._live():
                    return
                if job.kind in ('delete', 'feed-delete'):
                    diagnostic_source.set('Удаление · ' + job.item)
                    self.jobs._delete(job, current)
                    return
                feed = (job.item or 'demo') if job.kind in ('discover', 'read') else self.service.feed(job.item)
                if job.kind in ('discover','fetch'):
                    self.jobs._collection_active[job.id] = _CollectionAttempt(job,feed,self._clock(),capture,current.jobs.collection_start(job,feed))
                    if self.jobs.metrics:
                        from oko.observability import event
                        event('job_started', job_id=job.id, kind=job.kind)
                diagnostic_source.set('Сбор · ' + feed + ' · ' + job.kind)
                linked=job.kind=='fetch' and self.service.feedless(job.item)
                source = self._sources.get(feed)
                publication = self.service.publication(job.item) if job.kind == 'fetch' else None
                revision=self.service.revision(job.item) if job.kind=='fetch' else ''
                binding = self._telegram_bindings.get(feed)
                namespace = binding.cursor_key if binding else ''
                cursor = self.service.cursor(feed) if job.kind == 'discover' else None
                access = None if linked else self._collection.telegram_access(feed)
            if not source and not linked:
                return
            if access is not None:
                saved_binding = self.service.binding(feed)
                if not saved_binding or saved_binding['kind'] not in ('client', 'web'):
                    raise SourceError('Сопоставьте старый аккаунт профилю Telegram', code='identity-unconfirmed')
                if saved_binding['kind'] == 'client':
                    await self.verify_telegram_profile(saved_binding['role'])

            if job.kind == 'read':
                assert source is not None
                await self._execute_read(job, current, source)
            elif job.kind == 'discover':
                assert source is not None
                outcome = await source.discover(cursor)
                async with self.execution._actions:
                    if self.execution._store is current and self._live() and current.jobs.valid(job):
                        if outcome.observation.identity:
                            self.service.refresh_identity(feed, outcome.observation.identity.as_info())
                        collection_error = outcome.retry_error
                        self._account_wait(feed, outcome.retry_error)
                        with current.jobs.transaction(self._live):
                            if not self._live() or not current.jobs.valid(job):
                                return
                            effects=self.service.discovered(list(outcome.publications),outcome.cursor,feed,namespace,description=outcome.observation.description,successful=outcome.retry_error is None,source_order=outcome.source_order)
                            current.jobs.apply_collection(effects)
                            current.jobs.checkpoint('collection_after_result')
                            current.jobs.finish(job)
                            current.jobs.checkpoint('collection_after_finish')
                            self._finish_collection(job, current, collection_error)
                            if outcome.continuation is not None:
                                delay = outcome.continuation
                                current.jobs.enqueue_discovery(feed, self._clock() + delay if delay else 0)
                                current.jobs.checkpoint('collection_after_continuation')
                        self.service.feed_status(feed, error=None, gap=outcome.observation.gap, changes=outcome.observation.changes)
            elif publication:
                if linked:
                    fetched=await self._collection.article_source().fetch(publication)
                else:
                    assert source is not None
                    fetched=await source.fetch(publication)
                material = fetched.material
                collection_error = material.collection_error
                async with self.execution._actions:
                    if self.execution._store is current and self._live() and current.jobs.valid(job):
                        if fetched.observation.identity:
                            self.service.refresh_identity(feed, fetched.observation.identity.as_info())
                        with current.jobs.transaction(self._live):
                            if not self._live() or not current.jobs.valid(job):return
                            if not self.service.current(job.item,revision):
                                current.jobs.cancel(job)
                                return
                            collection_error,effects=self.service.collected(job.item,material,manual=current.jobs.manual(job))
                            current.jobs.apply_collection(effects)
                            current.jobs.checkpoint('collection_after_result')
                            if collection_error:
                                current.jobs.retry(job,self._clock(),collection_error.label,issue=collection_error.adapter_issue or AdapterIssue(collection_error.code or 'fetch-failed',collection_error.label,AdapterErrorKind.temporary if collection_error.retryable else AdapterErrorKind.permanent))
                            else:
                                current.jobs.finish(job)
                            current.jobs.checkpoint('collection_after_finish')
                            self._finish_collection(job, current, collection_error)
                        self._account_wait(feed, material.collection_error, job)
                        current.jobs.sync_reads()
                        self.service.feed_status(feed, error=None, gap=fetched.observation.gap, changes=fetched.observation.changes)
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
                if current and self.execution._store is current and self._live() and current.jobs.valid(job):
                    if job.kind=='fetch' and not self.service.current(job.item,revision):
                        with current.jobs.transaction(self._live):current.jobs.cancel(job)
                        return
                    if isinstance(error, SourceSkipped) and job.kind == 'fetch':
                        collection_error = None
                        with current.jobs.transaction(self._live):
                            self.service.viewed(job.item)
                            current.jobs.reconcile_cleanup()
                            self._finish_collection(job,current)
                        return
                    if isinstance(error, SourceDeleted) and job.kind == 'fetch':
                        collection_error = None
                        with current.jobs.transaction(self._live):
                            self.service.source_removed(job.item)
                            current.jobs.retry(job,self._clock(),error.label,terminal=True,
                                issue=error.adapter_issue or AdapterIssue('source-unavailable',error.label,AdapterErrorKind.unavailable))
                            self.service.notify_parents(job.item)
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
                    with current.jobs.transaction(self._live):
                        current.jobs.retry(job, self._clock(), label, error.delay if isinstance(error, SourceError) else 1,code=code,
                            terminal=isinstance(error, SourceError) and not error.retryable or job.kind in ('discover','fetch') and (isinstance(error,ValueError) or Store.is_integrity_failure(error)),
                            issue=error.adapter_issue if isinstance(error,SourceError) else None)
                        if job.kind=='fetch':
                            self.service.notify_parents(job.item)
                        if job.kind=='discover':
                            self.service.discovery_wait(feed,label,current.jobs.retry_at(job))
                        if job.kind == 'read':
                            self.service.read_failed(feed, label)
                        elif isinstance(error, SourceError):
                            self.service.feed_status(feed, error=label, gap=None)
                        self._finish_collection(job, current, error)

        finally:
            attempt = self.jobs._collection_active.get(job.id)
            if attempt and attempt.job.generation == job.generation:
                del self.jobs._collection_active[job.id]

    def _reject_media_input(self, current: Store, job: Job, download: MediaDownload) -> bool:
        if self.media.current(job.item,download):return False
        with current.jobs.transaction(self._live):
            if current.jobs.valid(job):
                if self.media.owner(job.item) is None:current.jobs.cancel(job)
                else:current.jobs.replace_media_input(job)
        return True

    def _media_failure(self, current: Store, job: Job, issue: AdapterIssue, error: BaseException | None = None) -> None:
        with current.jobs.transaction(self._live):
            if not self._live() or not current.jobs.valid(job):
                return
            self.media.failed(job.item,issue)
            current.jobs.reconcile_cleanup()
            if issue.kind==AdapterErrorKind.notice:
                if current.jobs.valid(job):
                    current.jobs.finish(job)
            else:
                current.jobs.fail_media(job,issue)
                if issue.kind==AdapterErrorKind.media_changed:
                    owner=self.media.owner(job.item)
                    if owner:
                        current.jobs.enqueue_fetch(owner)
            self._finish_collection(job, current, error)

    async def _execute_media(self, job: Job, capture: Capture) -> None:
        async with self.execution._actions:
            current = self.execution._store
            if not current or not self._live() or not current.jobs.valid(job):
                return
            media=MediaService(lambda:current)
            owner=media.owner(job.item)
            if owner is None:
                with current.jobs.transaction(self._live):current.jobs.finish(job)
                return
            feed=self.service.feed(owner)
            binding=self._telegram_bindings.get(feed)
            native=binding if binding and binding.read and not self.service.feedless(owner) else None
            if native and native.read:
                ready=self._account_retry.get(native.read.user_id,0)
                if ready>self._clock():
                    self._media_failure(current,job,AdapterIssue('telegram-flood-wait','Ожидание ограничения Telegram',AdapterErrorKind.source_wait,
                        scope='telegram:account:'+str(native.read.user_id),
                        retry_at=datetime.fromtimestamp(self._wall_clock()+ready-self._clock(),timezone.utc)))
                    return
            download = media.begin(job.item,str(job.id)+'-'+str(job.generation),current.jobs.manual(job))
            if download is None:
                with current.jobs.transaction(self._live):current.jobs.finish(job)
                return
            diagnostic_materials.set((owner,))
            diagnostic_source.set('Сбор · '+feed+' · download_media')
            capture_scope.set(secrets.token_hex(16))
            self.jobs._collection_active[job.id]=_CollectionAttempt(job,feed,self._clock(),capture,current.jobs.collection_start(job,feed))
            if self.jobs.metrics:
                from oko.observability import event
                event('job_started', job_id=job.id, kind=job.kind)
            caption_attempt=CaptionAttempt(self,current,job) if download.media.key=='youtube:subtitles' else None
            adapter = self._collection.article_source().adapter if self.service.feedless(download.publication_id) else self._collection.publication_adapter(feed,captions=caption_attempt)
        try:
            if adapter is None:
                raise ValueError('Publication adapter unavailable')
            if native:
                await self.verify_telegram_profile(native.lane)
                async with self.execution._actions:
                    if self.execution._store is not current or not self._live() or not current.jobs.valid(job):
                        raise asyncio.CancelledError
            with MediaOutput(download.temporary,lambda:self.execution._store is current and self._live() and current.jobs.valid(job)) as output:
                actual = await adapter.download_media(download.publication, download.media, output)
            async with self.execution._actions:
                if self.execution._store is current and self._live() and current.jobs.valid(job):
                    if self._reject_media_input(current,job,download):return
                    staged=media.stage(job.item,download,actual)
                    with current.jobs.transaction(self._live):
                        if not self._live() or not current.jobs.valid(job):
                            return
                        if not media.current(job.item,download):
                            current.jobs.replace_media_input(job)
                            return
                        media.accept(job.item,download,actual,staged)
                        current.jobs.finish(job)
                        self._finish_collection(job,current)
                    current.jobs.checkpoint('after_media_commit')
                    if caption_attempt and caption_attempt.attempted:
                        self.service.caption_completed()
        except CaptionsDeferred:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            async with self.execution._actions:
                if self.execution._store is current and self._live() and current.jobs.valid(job):
                    if self._reject_media_input(current,job,download):return
                    issue = error.issue if isinstance(error, AdapterException) else AdapterIssue('media-download',
                        'Не удалось сохранить вложение' if isinstance(error,OSError) else str(error) if isinstance(error,(ActionError,TelegramConflict)) else 'Нарушен контракт медиа',
                        AdapterErrorKind.temporary if isinstance(error,(OSError,ActionError,TelegramConflict)) else AdapterErrorKind.permanent)
                    source_error=error.__cause__ if isinstance(error.__cause__,SourceError) else None
                    if native and native.read and source_error and source_error.remote_type=='FloodWaitError':
                        issue=AdapterIssue(issue.code,issue.message,AdapterErrorKind.source_wait,download.publication,download.media.key,
                            'telegram:account:'+str(native.read.user_id),datetime.fromtimestamp(self._wall_clock()+source_error.delay,timezone.utc))
                    if caption_attempt and caption_attempt.blocked:
                        issue=AdapterIssue(issue.code,issue.message,AdapterErrorKind.source_wait,download.publication,download.media.key,
                            'youtube:captions',datetime.fromtimestamp(self.service.caption_ready(),timezone.utc))
                    if issue.kind!=AdapterErrorKind.notice:
                        record_caught('runtime._execute_media')
                    self._media_failure(current,job, issue,None if issue.kind==AdapterErrorKind.notice else source_error or error)
                    if native:
                        self._account_wait(feed,source_error,job)
        finally:
            media.release(download)
            attempt=self.jobs._collection_active.get(job.id)
            if attempt and attempt.job.generation==job.generation:
                del self.jobs._collection_active[job.id]

    def initialize(self) -> None:
        if self.execution._store is not None and not self._started:
            self._collection_interval=self.service.startup(self._source is not None,self._source_feed)
            if self._source:self.execution._store.jobs.enqueue_discovery(self._source_feed)
            self._started=True

    async def run(self) -> None:
        while not self.execution.stopped:
            try:
                await self.tick()
            except Exception:
                record_caught('Крон сбора')
            await asyncio.sleep(.02)

    async def tick(self) -> None:
        async with self.execution._actions:
            if not self._live() or self.execution._store is None:
                return
            self.initialize()
            now=self._clock()
            if now-self._last_media_cleanup>=3600:
                self.media.cleanup()
                self._last_media_cleanup=now
            if not self.execution._store.processing.background and now - self._read_sync_at >= .5:
                self.execution._store.jobs.sync_reads()
                self._read_sync_at = now
            self._sync_sources()
            if now-self._last_collection >= self._collection_interval:
                for feed in self._sources:
                    self.execution._store.jobs.enqueue_discovery(feed)
                self._last_collection = now
            state = self.execution._store.status()
            caption_feeds=self._collection.caption_feeds()
            for lane in ('common', 'personal', 'collection', 'media','captions'):
                if lane=='captions' and not caption_feeds:
                    continue
                media_lane=lane in ('media','captions')
                task = self.jobs._lanes.get(lane)
                if media_lane and task and not task.done() and lane in self.jobs._media_jobs and not self.execution._store.jobs.valid(self.jobs._media_jobs[lane]):
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
                profile = self.service.profile(lane) if lane in ('personal','collection') else None
                ready_at = self._account_retry.get(profile['user_id'], 0) if profile else 0
                if lane in self.jobs._lanes or now < ready_at:
                    continue
                feeds = [feed for feed in self._sources if self._collection.publication_adapter(feed) is not None] if media_lane else [feed for feed in self._sources if self._lane(feed) == lane]
                if lane=='captions':
                    feeds=caption_feeds
                dependencies=self._dependency_feeds if lane in ('common','media') else []
                job = self.execution._store.jobs.next_job(now, deleting_only=state['deleting'] or not (feeds or dependencies),
                                           feeds=feeds, include_deletions=lane == 'common', media_only=media_lane,captions_only=lane=='captions',dependency_feeds=dependencies)
                if job:
                    if media_lane:
                        self.jobs._media_jobs[lane] = job
                    self.jobs._lane_feeds[lane] = job.item if job.kind in ('discover', 'read', 'feed-delete') else self.service.feed(job.item)
                    self.jobs._lanes[lane] = asyncio.create_task(self._execute(job))


class CaptionAttempt:
    """One collection-owned caption call; waiting releases the collection slot."""
    def __init__(self, worker: CollectionWorker, store: Store, job: Job) -> None:
        self.worker, self.store, self.job = worker, store, job
        publication=worker.media.owner(job.item) if job.kind=='download_media' else job.item
        assert publication is not None
        self.publication=publication
        self.attempted = False
        self.blocked = False

    def current(self) -> bool:
        return self.worker.execution._store is self.store and self.worker._live() and self.store.jobs.valid(self.job)

    async def fetch(self, video: str) -> str | None:
        worker, store, job = self.worker, self.store, self.job
        async with worker.execution._actions:
            if not self.current():
                raise asyncio.CancelledError
            delay = worker.service.caption_ready() - worker._wall_clock()
            if delay > 0 or worker._captions_active:
                with store.jobs.transaction(worker._live):
                    store.jobs.defer_captions(job,worker._clock(),max(delay,.1),'Ожидание допуска получения субтитров')
                raise CaptionsDeferred
            config = worker.service.caption_config()
            client = LocalCaptions(proxy_url(config)) if isinstance(worker._youtube_captions, LocalCaptions) else worker._youtube_captions
            worker.service.caption_started(worker._wall_clock())
            self.attempted = True
            worker._captions_active = True
        try:
            try:
                text = await client.fetch(video)
            except CaptionsBlocked as error:
                async with worker.execution._actions:
                    if not self.current():
                        raise asyncio.CancelledError
                    worker.service.caption_blocked(worker._wall_clock(), error.retry_after)
                    self.blocked = True
                raise
            async with worker.execution._actions:
                if not self.current():
                    raise asyncio.CancelledError
            return text
        finally:
            worker._captions_active = False
