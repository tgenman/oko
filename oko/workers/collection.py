"""Collection cron owns source/media attempts and their adapter calls."""
from oko.domain.publication import PublicationIdentityConflict, PublicationOwnershipConflict
from oko.collection.request_observation import Capture
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timezone
from functools import partial
from collections.abc import Awaitable, Callable
import secrets
from typing import Any

import httpx

from oko.collection.sources import FeedRuntime, Sources
from oko.collection.rss_adapter import RSSAdapter
from oko.collection.telegram.adapter import TelegramWebAdapter
from oko.collection.youtube.adapter import YouTubeAdapter
from oko.collection.contracts import SourceError, SourceUnavailable, SourceSkipped, SourceDeferred, SourceObservation
from oko.collection.adapters import FeedAdapter
from oko.collection.adapters import AdapterException, AdapterErrorKind, AdapterIssue
from oko.collection.telegram.http import TelegramHTTP
from oko.collection.http import SourceHTTP
from oko.storage.media import MediaOutput, MediaDownload
from oko.storage.store import Store
from oko.domain.job import Job
from oko.collection.telegram.contracts import TelegramBinding
from oko.collection.youtube.source import LocalCaptions
from oko.collection.contracts import CaptionsClient
from oko.collection.youtube.history import HistoryClient
from oko.subscriptions.telegram import TelegramConflict
from oko.collection.youtube.access import CaptionsBlocked, CaptionsDeferred, proxy_url
from oko.diagnostics import Journal, diagnostic_source, diagnostic_materials, capture_scope, record_caught, exception_details

from oko.execution import Execution, ActionError
from oko.workers.control import JobControl, _CollectionAttempt
from oko.services.collection import CollectionService
from oko.services.discovery import DiscoveryService
from oko.storage.collection_state import CollectionState
from oko.storage.collection_publications import CollectionPublications
from oko.services.media import MediaService
from oko.application.collection_effects import CollectionApplication


def collection_service(storage: Callable[[], Store]) -> CollectionService:
    return CollectionService(access=lambda: storage().db.access_lock,
        publications=lambda: CollectionPublications(storage()), sources=lambda: CollectionState(storage()),
        collected=lambda: storage().collected_publication)


def media_service(storage: Callable[[], Store]) -> MediaService:
    return MediaService(access=lambda: storage().db.access_lock, repository=lambda: storage().media)


class CollectionWorker:
    clean_collection: Callable[[dict[str, Any]], dict[str, Any]]
    _collection_interval: float

    def __init__(self, execution: Execution, source: FeedAdapter | None, clock: Callable[[],float],
                 fault: Callable[[str],None], source_feed: str, rss_enabled: bool,
                 rss_transport: httpx.AsyncBaseTransport | None,
                 youtube_captions: CaptionsClient | None, youtube_history: HistoryClient | None,
                 wall_clock: Callable[[],float]) -> None:
        self.execution = execution
        self.service=collection_service(execution.require_store)
        self.discovery=DiscoveryService(lambda: execution.require_store().discovery)
        self.media=media_service(execution.retained_store)
        self._youtube_captions = youtube_captions or LocalCaptions()
        self._youtube_history = youtube_history
        self._wall_clock = wall_clock
        self._captions_active: 'CaptionAttempt | None' = None
        self._started=False
        self._source_revision: int | None = None
        self._prefer_cleanup = True
        self._dependency_feeds: list[str] = []
        self._source,self._clock,self._fault=source,clock,fault
        self._source_feed = source_feed
        self._telegram_http = TelegramHTTP(rss_transport, clock, wall_clock,
            lambda group: self.require_store().source_waits.telegram_state(group),
            self._save_telegram_http_wait,
            lambda: self._live() and not self.jobs._cancelling, lambda: self.execution._epoch,
            lambda: self.service.telegram_http_delay())
        self._source_http=SourceHTTP(rss_transport,wall_clock,
            lambda scope:self.require_store().source_waits.source_until(scope),self._save_source_http_wait,
            lambda:self._live() and not self.jobs._cancelling,lambda:self.execution._epoch)
        self._collection = Sources(source, source_feed, rss_enabled, rss_transport,
                                   self._youtube_captions, self._youtube_history, self._telegram_http,self._source_http)
        self._sources = self._collection.sources
        self.jobs = JobControl(self.execution, clock)
        self._telegram_bindings: dict[str, TelegramBinding] = {}
        self._last_collection = clock()
        # Recover unfinished file cleanup on startup, then run it hourly.
        self._last_media_cleanup = clock()-3600

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
                delay=max(current.subscriptions.retry_settings()['initial_seconds'],server_delay or 0)
                until=max(current.source_waits.source_until(scope),current.wall_clock()+delay)
                current.source_waits.save_source(scope,until)
                return until

    async def _save_telegram_http_wait(self, group: str, server_delay: float | None, generation: int) -> float:
        async with self.execution._actions:
            if not self._live() or self.jobs._cancelling or generation!=self.execution._epoch:
                raise SourceDeferred('Запрос Telegram прерван',code='source_unavailable')
            current=self.require_store()
            with current.jobs.transaction(self._live):
                state=current.source_waits.telegram_state(group)
                strikes=min(int(state.get('strikes',0))+1,7)
                settings=current.subscriptions.retry_settings()
                delay=max(float(min(settings['initial_seconds']*2**(strikes-1),settings['max_seconds'])),server_delay or 0)
                until=max(float(state.get('until',0)),current.wall_clock()+delay)
                current.source_waits.save_telegram(group,until,strikes)
                return until

    def _live(self) -> bool:
        return self.execution._live()

    def _sync_sources(self) -> None:
        if not self.execution._store or self.service.source_revision()==self._source_revision:
            return
        current=self.execution._store
        with current.jobs.transaction(self._live):
            snapshot=self.service.configuration()
        self._dependency_feeds=snapshot.dependencies
        settings=[setting for setting in snapshot.settings if not (setting.feed==self._source_feed and self._source)]
        updates=self._collection.configure(settings)
        if updates.created:
            with current.jobs.transaction(self._live):
                for identity in updates.created:
                    current.jobs.enqueue_discovery(identity)
        if updates.captions:
            with current.jobs.transaction(self._live):
                for identity,want_captions in updates.captions:
                    current.jobs.prepare_youtube(identity,want_captions)
        self._telegram_bindings.clear()
        for setting in snapshot.settings:
            feed=setting.feed
            binding=snapshot.bindings.get(feed)
            source=self._sources.get(feed)
            if source and binding and not setting.deleting:
                if binding['kind']=='web':
                    self._telegram_bindings[feed]=TelegramBinding('telegram-web','common')
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


    def _lane(self, feed: str) -> str:
        binding = self._telegram_bindings.get(feed)
        return binding.lane if binding else 'common'

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
        if job.kind == 'rebuild':
            await self._execute_rebuild(job)
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
                feed = job.item if job.kind in ('discover', 'read') else self.service.feed(job.item)
                if job.kind in ('discover','fetch'):
                    with current.jobs.transaction(self._live):
                        saved=current.jobs.collection_start(job,feed)
                    self.jobs._collection_active[job.id] = _CollectionAttempt(job,feed,self._clock(),capture,saved)
                    if self.jobs.metrics:
                        from oko.observability import event
                        event('job_started', job_id=job.id, kind=job.kind)
                diagnostic_source.set('Сбор · ' + feed + ' · ' + job.kind)
                linked=job.kind=='fetch' and self.service.feedless(job.item)
                source = self._sources.get(feed)
                publication = self.service.reference(job.item) if job.kind == 'fetch' else None
                revision=self.service.revision(job.item) if job.kind=='fetch' else ''
                binding = self._telegram_bindings.get(feed)
                namespace = binding.cursor_key if binding else ''
                cursor = self.service.cursor(feed) if job.kind == 'discover' else None
                access = None if linked else self._collection.telegram_access(feed)
            if not source and not linked:
                return
            if access is not None:
                saved_binding = self.service.binding(feed)
                if not saved_binding or saved_binding['kind'] != 'web':
                    raise SourceError('Недоступная привязка Telegram', code='identity-unconfirmed')

            if job.kind == 'discover':
                assert source is not None
                from oko.workers.feed_discovery import discover_rss,discover_telegram,discover_youtube
                if isinstance(source,FeedRuntime) and isinstance(source.adapter,RSSAdapter):
                    outcome=await discover_rss(source.adapter,source.settings.url,source.settings.initial_history or {},cursor)
                elif isinstance(source,FeedRuntime) and isinstance(source.adapter,TelegramWebAdapter):
                    assert source.settings.telegram and source.settings.telegram.username
                    outcome=await discover_telegram(source.adapter,source.settings.telegram.username,source.settings.initial_history or {},cursor)
                elif isinstance(source,FeedRuntime) and isinstance(source.adapter,YouTubeAdapter):
                    outcome=await discover_youtube(source.adapter,source.settings.url,source.settings.initial_history or {},cursor)
                elif source.kind=='injected':
                    from oko.collection.adapters import ExternalKey,ExternalReference,Platform
                    from oko.collection.contracts import DiscoveryResult
                    reference=ExternalReference(ExternalKey(Platform.other_web,source.settings.url),source.settings.url)
                    page=await source.adapter.discover(reference,cursor or '',50)
                    outcome=DiscoveryResult(page.publications,page.next_cursor,continuation=0 if page.has_more else None)
                else:
                    raise ValueError('Feed adapter unavailable')
                async with self.execution._actions:
                    if self.execution._store is current and self._live() and current.jobs.valid(job):
                        collection_error = outcome.retry_error
                        with current.jobs.transaction(self._live):
                            if not self._live() or not current.jobs.valid(job):
                                return
                            if outcome.observation.identity:
                                self.service.refresh_identity(feed, outcome.observation.identity.as_info())
                            current.jobs.begin_collection(job)
                            effects=self.discovery.accept(list(outcome.publications),outcome.cursor,feed,namespace,description=outcome.observation.description,successful=outcome.retry_error is None)
                            CollectionApplication(current.jobs,current.publications,current.processing).apply(effects,job=job)
                            current.jobs.checkpoint('collection_after_result')
                            current.jobs.finish(job)
                            if current.jobs.consume_restored_discovery(job):
                                current.jobs.enqueue_discovery(feed)
                            current.jobs.checkpoint('collection_after_finish')
                            self._finish_collection(job, current, collection_error)
                            if outcome.continuation is not None:
                                delay = outcome.continuation
                                current.jobs.enqueue_discovery(feed, self._clock() + delay if delay else 0)
                                current.jobs.checkpoint('collection_after_continuation')
                            self.service.feed_status(feed, error=None, gap=outcome.observation.gap, changes=outcome.observation.changes)
            elif publication:
                adapter=self._collection.article_adapter() if linked else self._collection.publication_adapter(feed)
                if adapter is None:
                    raise SourceError('Адаптер публикации недоступен',retryable=False)
                try:
                    value=await adapter.fetch(publication)
                except AdapterException as error:
                    from oko.collection.adapter_errors import source_error
                    raise source_error(error) from error
                observation=SourceObservation(changes=self._collection.description(feed))
                collection_error=None
                async with self.execution._actions:
                    if self.execution._store is current and self._live() and current.jobs.valid(job):
                        with current.jobs.transaction(self._live):
                            if not self._live() or not current.jobs.valid(job):return
                            if not self.service.current(job.item,revision):
                                current.jobs.cancel(job)
                                return
                            if observation.identity:
                                self.service.refresh_identity(feed, observation.identity.as_info())
                            current.jobs.begin_collection(job)
                            collection_error,effects=self.service.collected_content(job.item,value)
                            CollectionApplication(current.jobs,current.publications,current.processing).apply(effects,job=job)
                            current.jobs.checkpoint('collection_after_result')
                            if collection_error:
                                current.jobs.retry(job,self._clock(),collection_error.label,issue=collection_error.adapter_issue or AdapterIssue(collection_error.code or 'fetch-failed',collection_error.label,AdapterErrorKind.temporary if collection_error.retryable else AdapterErrorKind.permanent))
                            else:
                                current.jobs.finish(job)
                            current.jobs.checkpoint('collection_after_finish')
                            self._finish_collection(job, current, collection_error)
                            self.service.feed_status(feed, error=None, gap=observation.gap, changes=observation.changes)
        except CaptionsDeferred:
            async with self.execution._actions:
                if current and self.execution._store is current and not self.stopped:
                    with current.jobs.transaction(lambda: not self.stopped):
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
                            self._finish_collection(job,current)
                        return
                    if isinstance(error, SourceUnavailable) and job.kind == 'fetch':
                        collection_error = None
                        with current.jobs.transaction(self._live):
                            current.jobs.retry(job,self._clock(),error.label,terminal=True,
                                issue=error.adapter_issue or AdapterIssue('source-unavailable',error.label,AdapterErrorKind.unavailable))
                            current.publications.notify_parents(job.item)
                            self._finish_collection(job,current)
                        return
                    label = error.label if isinstance(error, SourceError) else str(error) if isinstance(error, (TelegramConflict, PublicationOwnershipConflict, PublicationIdentityConflict)) else 'Ошибка сбора'
                    if isinstance(error, SourceError):
                        code = error.code or 'source-unavailable'
                    elif isinstance(error, TelegramConflict):
                        code = error.code
                    elif isinstance(error, PublicationOwnershipConflict):
                        code = 'publication-ownership-conflict'
                    elif isinstance(error, PublicationIdentityConflict):
                        code = 'publication-identity-conflict'
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
                            issue=error.adapter_issue if isinstance(error,SourceError) else None,
                            cleanup_backoff=job.kind in ('delete','feed-delete') and not (isinstance(error,SourceError) and not error.retryable))
                        if job.kind=='fetch':
                            current.publications.notify_parents(job.item)
                        if job.kind=='discover':
                            self.service.discovery_wait(feed,label,current.jobs.retry_at(job))
                        if isinstance(error, SourceError):
                            self.service.feed_status(feed, error=label, gap=None)
                        self._finish_collection(job, current, error)

        finally:
            attempt = self.jobs._collection_active.get(job.id)
            if attempt and attempt.job.generation == job.generation:
                del self.jobs._collection_active[job.id]

    async def _execute_rebuild(self, job: Job) -> None:
        import os
        import shutil
        from pathlib import Path
        from uuid import uuid4
        from oko.collection.rebuild_types import PreparedFile,PreparedReplacement
        from oko.storage import files
        from oko.storage.media_paths import existing_file
        from oko.storage.rebuild import RebuildRepository
        from oko.storage.collection_effects import CollectionEffectCapture
        from oko.workers.rebuild import collect

        current=None
        staging: Path | None=None
        created: list[Path]=[]
        active: set[Path]=set()
        committed=[False]
        try:
            async with self.execution._actions:
                current=self.execution._store
                if not current or not self._live() or not current.jobs.valid(job):return
                feed=self.service.feed(job.item)
                reference=self.service.reference(job.item)
                revision=self.service.revision(job.item)
                adapter=self._collection.publication_adapter(feed)
                if reference is None or adapter is None:raise ValueError('Rebuild adapter unavailable')
                staging=current.root/'staging'/job.item/('rebuild-'+str(job.id)+'-'+str(job.generation))
                active=current.media.active_paths
                active.add(staging)
            def admitted() -> bool:
                return bool(current and self.execution._store is current and self._live() and current.jobs.valid(job))
            staged=await collect(adapter,self._collection.article_adapter(),reference,staging,admitted,active)
            if staged[0].value.metadata.reference.key!=reference.key:
                raise ValueError('Rebuild returned another publication identity')
            async with self.execution._actions:
                if not admitted():return
                snapshots=[]
                for index,value in enumerate(staged):
                    ref=value.value.metadata.reference
                    row=current.db.execute('SELECT id FROM publications WHERE platform=%s AND external_id=%s',
                        (ref.key.platform.value,ref.key.external_id)).fetchone()
                    identity=job.item if index==0 else row['id'] if row else str(uuid4())
                    if row and row['id']!=identity:raise ValueError('Rebuild identity changed')
                    old=current.media.publication_rows(identity) if row else []
                    snapshots.append((identity,old,self.service.revision(identity) if row else None))
            replacements=[]
            for value,(identity,old,_) in zip(staged,snapshots,strict=True):
                previous={row['resource_key']:row for row in old}
                prepared=[]
                for staged_file in value.files:
                    media=staged_file.media
                    existing=previous.get(media.key)
                    if existing and media.source_url is None and existing['source_locator']==media.locator:
                        media=replace(media,source_url=existing['source_url'])
                    old_path=existing_file(current.root,existing['storage_key']) if existing else None
                    same=bool(staged_file.path and existing and existing['status']=='ready' and old_path and
                              files.file_digest(staged_file.path)==files.file_digest(old_path))
                    media_id=existing['id'] if same and existing else str(uuid4())
                    path=old_path if same else None
                    size=staged_file.path.stat().st_size if staged_file.path else None
                    if staged_file.path and not same:
                        directory=current.root/'media'/identity
                        directory.mkdir(parents=True,exist_ok=True)
                        path=directory/media_id
                        active.add(path)
                        with staged_file.path.open('rb') as stream:os.fsync(stream.fileno())
                        os.link(staged_file.path,path)
                        created.append(path)
                        files.fsync_directory(directory)
                    prepared.append(PreparedFile(media.key,media_id,str(path.relative_to(current.root)) if path else None,
                                                 media,size,staged_file.issue))
                replacements.append(PreparedReplacement(identity,value.value,tuple(prepared)))
            async with self.execution._actions:
                if not admitted():return
                if self.service.revision(job.item)!=revision:
                    raise AdapterException(AdapterIssue('rebuild-stale','Публикация изменилась во время пересборки',AdapterErrorKind.temporary))
                for replacement,(_,_,old_revision) in zip(replacements[1:],snapshots[1:],strict=True):
                    if old_revision is not None and self.service.revision(replacement.identity)!=old_revision:
                        raise AdapterException(AdapterIssue('rebuild-stale','Связанная публикация изменилась во время пересборки',AdapterErrorKind.temporary))
                repository=RebuildRepository(db=current.db,publications=current.publications,
                    capture=CollectionEffectCapture(current),clock=current.wall_clock,fault=current.fault)
                with current.jobs.transaction(self._live):
                    if not admitted():return
                    current.jobs.after_commit(lambda:committed.__setitem__(0,True))
                    current.jobs.begin_collection(job)
                    effects=repository.accept(job.item,tuple(replacements))
                    CollectionApplication(current.jobs,current.publications,current.processing).apply(effects,job=job)
                    current.jobs.finish(job)
                    for replacement in replacements:
                        current.jobs.after_commit(partial(current.publications.prune,replacement.identity))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            record_caught('runtime._execute_rebuild')
            async with self.execution._actions:
                if current and self.execution._store is current and self._live() and current.jobs.valid(job):
                    if isinstance(error,AdapterException):
                        issue=error.issue
                    elif isinstance(error,SourceDeferred):
                        issue=error.adapter_issue or AdapterIssue(error.code or 'source-wait',error.label,
                            AdapterErrorKind.source_wait,scope=getattr(error,'scope','source:'+feed),
                            retry_at=datetime.fromtimestamp(current.wall_clock()+max(0,error.delay),timezone.utc))
                    elif isinstance(error,SourceError):
                        if error.adapter_issue:
                            issue=error.adapter_issue
                        elif error.retryable and error.delay>0:
                            issue=AdapterIssue(error.code or 'source-unavailable',error.label,AdapterErrorKind.source_wait,
                                scope='source:'+feed,retry_at=datetime.fromtimestamp(current.wall_clock()+error.delay,timezone.utc))
                        else:
                            issue=AdapterIssue(error.code or 'source-unavailable',error.label,
                                AdapterErrorKind.temporary if error.retryable else AdapterErrorKind.permanent)
                    else:
                        issue=AdapterIssue('rebuild-failed',str(error) or 'Ошибка пересборки',AdapterErrorKind.permanent if isinstance(error,ValueError) else AdapterErrorKind.temporary)
                    with current.jobs.transaction(self._live):
                        current.jobs.retry(job,self._clock(),issue.message,issue=issue)
        finally:
            if staging is not None:
                active.discard(staging)
                for path in list(active):
                    if staging in path.parents:
                        active.discard(path)
                shutil.rmtree(staging,ignore_errors=True)
            for path in created:
                active.discard(path)
                if not committed[0]:path.unlink(missing_ok=True)

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
            replacement=self.media.failed(job.item,issue)
            if issue.kind==AdapterErrorKind.notice:
                if current.jobs.valid(job):
                    if issue.code=='subtitles-unavailable':
                        current.jobs.finish(job,reason_code='subtitles_absent',reason_message=issue.message)
                        if replacement and replacement[1]!=job.item:
                            current.jobs.retarget_completed_media(job,replacement[1],replacement[0])
                        if replacement:
                            current.processing.reconcile_accepted_item(replacement[0])
                    else:
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
            media=media_service(lambda:current)
            owner=media.owner(job.item)
            if owner is None:
                with current.jobs.transaction(self._live):current.jobs.finish(job)
                return
            feed=self.service.feed(owner)
            download = media.begin(job.item,str(job.id)+'-'+str(job.generation),current.jobs.manual(job))
            if download is None:
                with current.jobs.transaction(self._live):current.jobs.finish(job)
                return
            diagnostic_materials.set((owner,))
            diagnostic_source.set('Сбор · '+feed+' · download_media')
            capture_scope.set(secrets.token_hex(16))
            with current.jobs.transaction(self._live):
                saved=current.jobs.collection_start(job,feed)
            self.jobs._collection_active[job.id]=_CollectionAttempt(job,feed,self._clock(),capture,saved)
            if self.jobs.metrics:
                from oko.observability import event
                event('job_started', job_id=job.id, kind=job.kind)
            caption_attempt=CaptionAttempt(self,current,job) if download.media.key=='youtube:subtitles' else None
            adapter = self._collection.article_adapter() if self.service.feedless(download.publication_id) else self._collection.publication_adapter(feed,captions=caption_attempt)
        try:
            if adapter is None:
                raise ValueError('Publication adapter unavailable')
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
                        if staged[0]!=job.item:
                            current.jobs.retarget_completed_media(job,staged[0],download.publication_id)
                        if download.media.key=='youtube:subtitles':
                            current.processing.reconcile_accepted_item(download.publication_id)
                        if caption_attempt and caption_attempt.attempted:
                            self.service.caption_completed()
                        self._finish_collection(job,current)
                    current.jobs.checkpoint('after_media_commit')
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
                    if caption_attempt and caption_attempt.blocked:
                        issue=AdapterIssue(issue.code,issue.message,AdapterErrorKind.source_wait,download.publication,download.media.key,
                            'youtube:captions',caption_retry_datetime(self.service.caption_ready()))
                    if issue.kind!=AdapterErrorKind.notice:
                        record_caught('runtime._execute_media')
                    self._media_failure(current,job, issue,None if issue.kind==AdapterErrorKind.notice else source_error or error)
        finally:
            if caption_attempt is not None:
                caption_attempt.release()
            media.release(download)
            attempt=self.jobs._collection_active.get(job.id)
            if attempt and attempt.job.generation==job.generation:
                del self.jobs._collection_active[job.id]

    def initialize(self) -> None:
        if self.execution._store is not None and not self._started:
            self._collection_interval=self.service.startup(self._source is not None,self._source_feed)
            if self._source:
                with self.execution._store.jobs.transaction(lambda: not self.stopped):
                    self.execution._store.jobs.enqueue_discovery(self._source_feed)
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
                for retired,key in self.execution._store.jobs.retired_media_jobs():
                    if retired.id not in self.jobs._collection_active and self.media.retired_file_cleaned(key):
                        with self.execution._store.jobs.transaction(self._live):
                            self.execution._store.jobs.finish_retired_media_cleanup(retired,key)
                self._last_media_cleanup=now
            self._sync_sources()
            if now-self._last_collection >= self._collection_interval:
                with self.execution._store.jobs.transaction(self._live):
                    for feed in self._sources:
                        self.execution._store.jobs.enqueue_discovery(feed)
                self._last_collection = now
            caption_feeds=self._collection.caption_feeds()
            for lane in ('common', 'media','captions'):
                if lane=='captions' and not caption_feeds:
                    continue
                media_lane=lane in ('media','captions')
                task = self.jobs._lanes.get(lane)
                if task and not task.done() and lane in self.jobs._lane_jobs and not self.execution._store.jobs.valid(self.jobs._lane_jobs[lane]):
                    task.cancel()
                    self.jobs._detach_task(task)
                    del self.jobs._lanes[lane]
                    self.jobs._lane_jobs.pop(lane,None)
                    task = None
                if task and task.done():
                    if not task.cancelled():
                        await task
                    del self.jobs._lanes[lane]
                    self.jobs._lane_jobs.pop(lane,None)
                if lane in self.jobs._lanes:
                    continue
                feeds = [feed for feed in self._sources if self._collection.publication_adapter(feed) is not None] if media_lane else [feed for feed in self._sources if self._lane(feed) == lane]
                if lane=='captions':
                    feeds=caption_feeds
                dependencies=self._dependency_feeds if lane in ('common','media') else []
                options: dict[str,Any]=dict(deleting_only=not (feeds or dependencies), feeds=feeds,
                             include_deletions=lane == 'common', media_only=media_lane,
                             captions_only=lane=='captions', dependency_feeds=dependencies,
                             prefer_cleanup=self._prefer_cleanup)
                if not self.execution._store.jobs.has_due_job(now,**options):
                    continue
                with self.execution._store.jobs.transaction(self._live):
                    job = self.execution._store.jobs.next_job(now,**options)
                if job:
                    if lane=='common':
                        self._prefer_cleanup=not self._prefer_cleanup
                    self.jobs._lane_jobs[lane] = job
                    if job.kind=='delete':
                        owner=self.execution._store.db.execute("SELECT payload->>'cleanup_feed' FROM jobs WHERE id=%s",(job.id,)).fetchone()
                        self.jobs._lane_feeds[lane]=owner[0] if owner and owner[0] else job.item
                    else:
                        self.jobs._lane_feeds[lane] = job.item if job.kind in ('discover', 'read', 'feed-delete') else self.service.feed(job.item)
                    self.jobs._lanes[lane] = asyncio.create_task(self._execute(job))


def caption_retry_datetime(value: float) -> datetime | None:
    """Unrepresentable admission deadlines remain explicitly unknown."""
    try:return datetime.fromtimestamp(value,timezone.utc)
    except (ValueError,OverflowError,OSError):return None


class CaptionAttempt:
    """One collection-owned caption call; waiting releases the collection slot."""
    def __init__(self, worker: CollectionWorker, store: Store, job: Job) -> None:
        self.worker, self.store, self.job = worker, store, job
        publication=worker.media.owner(job.item) if job.kind=='download_media' else job.item
        assert publication is not None
        self.publication=publication
        self.attempted = False
        self.blocked = False
        self.network_finished = False

    def current(self) -> bool:
        return self.worker.execution._store is self.store and self.worker._live() and self.store.jobs.valid(self.job)

    async def fetch(self, video: str) -> str | None:
        worker, store, job = self.worker, self.store, self.job
        async with worker.execution._actions:
            if not self.current():
                raise asyncio.CancelledError
            delay = worker.service.caption_ready() - worker._wall_clock()
            active = worker._captions_active
            if active is not None and active.network_finished and not active.current():
                worker._captions_active = None
            if delay > 0 or worker._captions_active:
                with store.jobs.transaction(worker._live):
                    store.jobs.defer_captions(job,worker._clock(),max(delay,.1),'Ожидание допуска получения субтитров')
                raise CaptionsDeferred
            config = worker.service.caption_config()
            client = LocalCaptions(proxy_url(config)) if isinstance(worker._youtube_captions, LocalCaptions) else worker._youtube_captions
            with store.jobs.transaction(worker._live):
                worker.service.caption_started(worker._wall_clock())
            self.attempted = True
            worker._captions_active = self
        try:
            try:
                text = await client.fetch(video)
            finally:
                self.network_finished = True
        except CaptionsBlocked as error:
            async with worker.execution._actions:
                if not self.current():
                    raise asyncio.CancelledError
                with store.jobs.transaction(worker._live):
                    worker.service.caption_blocked(worker._wall_clock(), error.retry_after)
                self.blocked = True
            raise
        async with worker.execution._actions:
            if not self.current():
                raise asyncio.CancelledError
        return text

    def release(self) -> None:
        if self.worker._captions_active is self:
            self.worker._captions_active = None
