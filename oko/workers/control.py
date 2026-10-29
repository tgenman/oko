"""Task ownership shared by current execution and application commands."""
from oko.collection.request_observation import Capture
import asyncio
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Literal, TYPE_CHECKING
from oko.collection.contracts import Source
from oko.storage.store import Store
from oko.storage.job_types import Job
from oko.collection.telegram.source import LocalTelegram, TelegramClient, TelegramSource
from oko.diagnostics import Journal, record_caught
from oko.execution import Execution
from oko.services.queries import ReadingQueries
from oko.storage.job_repository import JobRepository
from oko.services.processing import ProcessingService
if TYPE_CHECKING:
    from oko.observability import Metrics

@dataclass
class _CollectionAttempt:
    job: Job
    feed: str
    started: float
    capture: Capture
    saved: dict[str, Any]


class JobControl:
    def __init__(self, execution: Execution, sources: dict[str, Source], telegram: TelegramClient | None, clock: Callable[[], float]) -> None:
        self.execution, self._sources, self._telegram_client, self._clock = execution, sources, telegram, clock
        self._collection_active: dict[int, _CollectionAttempt] = {}
        self._ai: asyncio.Task[None] | None = None
        self._lanes: dict[str, asyncio.Task[None]] = {}
        self._media_jobs: dict[str, Job] = {}
        self._lane_feeds: dict[str, str] = {}
        self._ai_feed: str | None = None
        self._cancelling = 0
        self._detached: set[asyncio.Task[None]] = set()
        self._detached_ai: set[asyncio.Task[None]] = set()
        self.metrics: 'Metrics | None' = None

    @property
    def _store(self) -> Store | None:
        return self.execution._store

    @property
    def diagnostics(self) -> Journal | None:
        return self.execution.diagnostics

    def invalidate(self, feeds: set[str] | None = None) -> None:
        if self._store:
            with self._store.jobs.cancellation():
                for attempt in list(self._collection_active.values()):
                    if feeds is None or attempt.feed in feeds:
                        self._store.jobs.collection_interrupt(attempt.job, attempt.feed, attempt.saved, max(0., self._clock()-attempt.started))
                self._store.jobs.interrupted(feeds)
                self._store.jobs.invalidate_processing()

    async def cancel(self, feeds: set[str] | None = None, *, invalidate: bool = True) -> None:
        if feeds is None:
            self._cancelling += 1
            self.execution._epoch += 1
            for request in tuple(self.execution._requests):
                request.cancel()
        try:
            # Invalidate before waiting: even an adapter suppressing cancellation may
            # not publish its old generation when another tab is still alive.
            if invalidate:
                self.invalidate(feeds)
            for identity, attempt in list(self._collection_active.items()):
                if feeds is None or attempt.feed in feeds:
                    if self.metrics:
                        from oko.observability import event
                        elapsed = max(0., self._clock()-attempt.started)
                        self.metrics.attempt(attempt.job.kind, 'cancelled', elapsed)
                        event('job_cancelled', job_id=attempt.job.id, kind=attempt.job.kind, elapsed=elapsed)
                    del self._collection_active[identity]
            ai_task = self._ai
            lanes = [lane for lane in self._lanes if feeds is None or self._lane_feeds.get(lane) in feeds]
            cancelled_feeds = {self._lane_feeds[lane] for lane in lanes if lane in self._lane_feeds}
            tasks = [self._lanes.pop(lane) for lane in lanes]
            for media_lane in ('media','captions'):
                if media_lane in lanes:
                    self._media_jobs.pop(media_lane,None)
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


    async def _drain_deletions(self, current: Store) -> None:
        feeds = ReadingQueries(lambda: current).deletion_feeds()
        if feeds:
            await self.cancel(feeds)
        job = current.jobs.next_job(self._clock(), deleting_only=True)
        while job:
            self._delete(job, current)
            job = current.jobs.next_job(self._clock(), deleting_only=True)


    def _delete(self, job: Job, current: Store) -> None:
        try:
            if job.kind == 'feed-delete':
                current.jobs.finish_feed_deletion(job)
            else:
                current.jobs.delete(job)
        except Exception:
            record_caught('runtime._delete')
            if current.jobs.valid(job):
                current.jobs.retry(job, self._clock(), 'Ошибка удаления', code='delete')


    def retry_material(self, identity: str) -> bool:
        return self.execution.require_store().jobs.retry_material(identity)

    def retry_filter(self, identity: str) -> bool:
        return self._retry_processing(identity,'filter')

    def retry_theses(self, identity: str) -> bool:
        return self._retry_processing(identity,'theses')

    def refresh_feed(self, identity: str) -> None:
        self.execution.require_store().jobs.refresh_feed(identity)

    def enqueue_discovery(self, identity: str) -> None:
        self.execution.require_store().jobs.enqueue_discovery(identity)

    def _retry_processing(self, identity: str, kind: Literal['filter','theses']) -> bool:
        store=self.execution.require_store()
        service=ProcessingService(self.execution.require_store)
        repository=JobRepository(store)
        store.processing.reconcile_item(identity)
        repository.reconcile(kind,service.candidates(kind,item=identity))
        if not service.regenerate(identity,kind):
            return True
        accepted = repository.retry_processing(identity,kind)
        if accepted:
            service.refresh(identity)
        return accepted

    def reconcile_cleanup(self) -> None:
        store=self.execution.require_store()
        store.jobs.reconcile_cleanup()
        store.jobs.sync_reads()

    def change_adapter(self, feed: str) -> None:
        self.execution.require_store().jobs.change_adapter(feed)

    def remove_feed(self, feed: str, members: set[str]) -> None:
        self.execution.require_store().jobs.remove_feed(feed,members)

    def cancel_history(self, feed: str) -> None:
        self.execution.require_store().jobs.cancel_history(feed)
