"""Technical composition and process lifetime; no pipeline selection or acceptance."""
import asyncio
from collections.abc import Callable
from pathlib import Path
import sqlite3
import time

import httpx

from oko.collection.contracts import Source
from oko.processing.codex import CodexClient,LocalCodex
from oko.storage.store import Store
from oko.collection.telegram.source import TelegramClient
from oko.collection.contracts import CaptionsClient
from oko.collection.youtube.history import HistoryClient
from oko.diagnostics import Journal, active_journal, record_caught

from oko.execution import Execution, ActionError

from oko.workers.collection import CollectionWorker
from oko.workers.processing import FilterWorker, SummaryWorker

class Host:
    clear_feed_checks: Callable[[],None]

    async def admission_changed(self) -> None:
        async with self.execution._actions:
            if not self.execution._live() and not self.stopped:
                await self.jobs.cancel()

    def __init__(self, root: Path, source: Source | None, clock: Callable[[], float],
                 fault: Callable[[str], None], shutdown: Callable[[], None],
                 admitted: Callable[[], bool], should_stop: Callable[[], bool], source_feed: str, rss_enabled: bool, rss_transport: httpx.AsyncBaseTransport | None, telegram_client: TelegramClient | None, ai_transport: httpx.AsyncBaseTransport | None = None, codex_client: CodexClient | None = None, youtube_captions: CaptionsClient | None = None, youtube_history: HistoryClient | None = None, wall_clock: Callable[[], float] = time.time) -> None:
        self.execution = Execution(admitted)
        self.codex=codex_client or LocalCodex()
        self.collection=CollectionWorker(self.execution,source,clock,fault,source_feed,rss_enabled,rss_transport,telegram_client,youtube_captions,youtube_history,wall_clock)
        self.jobs=self.collection.jobs
        self.filters=FilterWorker(self.execution,self.jobs,clock,self.codex,ai_transport)
        self.summaries=SummaryWorker(self.execution,self.jobs,clock,self.codex,ai_transport)
        self._root,self._source,self._source_feed=root,source,source_feed
        self._fault,self._clock,self._wall_clock=fault,clock,wall_clock
        self._shutdown,self._admitted,self._should_stop=shutdown,admitted,should_stop
        self._reader_advanced=float('-inf')
        self._closed=False

    @property
    def diagnostics(self) -> Journal | None:
        return self.execution.diagnostics

    @diagnostics.setter
    def diagnostics(self, value: Journal | None) -> None:
        self.execution.diagnostics = value

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
                self.clear_feed_checks()
                await self.jobs.cancel()


    async def run(self) -> None:
        journal_context = active_journal.set(self.execution.diagnostics)
        try:
            self.execution._store = Store(self._root, self._fault, wall_clock=self._wall_clock, clock=self._clock)
            self.execution.enabled = self.execution._store.work_enabled()
            self.collection.initialize()
            viewed = {row['id'] for row in self.execution._store.db.execute('SELECT id FROM items WHERE viewed=1')}
            if viewed and self.execution.diagnostics:
                self.execution.diagnostics.forget_materials(viewed)
        except (sqlite3.DatabaseError,OSError,ValueError) as error:
            record_caught('runtime.run')
            self.execution._error = str(error) if str(error).startswith('Telegram:') else 'Хранилище недоступно или повреждено. Состояние сохранено; сбор остановлен.'
        crons=[asyncio.create_task(worker.run()) for worker in (self.collection,self.filters,self.summaries)]
        try:
            while not self.stopped:
                async with self.execution._actions:
                    now=self._clock()
                    if self.execution._store and self.execution.reader_advance and now-self._reader_advanced>=.25:
                        self.execution.reader_advance()
                        self._reader_advanced=now
                    if not self.execution._live():
                        if not self._admitted():
                            await self.jobs.cancel()
                        if self._should_stop():
                            self.execution._stop_requested.set()
                await asyncio.sleep(.02)
        finally:
            self.execution._stop_requested.set()
            for task in crons:
                task.cancel()
            await asyncio.gather(*crons,return_exceptions=True)
            async with self.execution._actions:
                await self.jobs.cancel()
                if self.execution._store:
                    if self.execution.reader_finish:
                        await self.execution.reader_finish()
                    self.execution._store.db.close()
                    self.execution._store = None
                self._closed = True
            await self.collection._source_http.close()
            await self.collection._telegram_http.close()
            self._shutdown()
            active_journal.reset(journal_context)
