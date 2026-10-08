import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from .sources import Source
from .store import Job, Store
from .subscriptions import Subscriptions, ImportRequest, Overrides, FeedPatch, preview


class Runtime:
    def __init__(self, root: Path, source: Source | None, clock: Callable[[], float],
                 fault: Callable[[str], None], shutdown: Callable[[], None],
                 lease: float, grace: float, source_feed: str) -> None:
        self.root, self.source, self.clock, self.fault = root, source, clock, fault
        self.shutdown, self.lease, self.grace = shutdown, lease, grace
        self.source_feed = source_feed
        self.tabs: dict[str, float] = {}
        self.stopped = False
        self.idle_since: float | None = None
        self.store: Store | None = None
        self.error: str | None = None
        self.active: asyncio.Task[None] | None = None
        self.detached: set[asyncio.Task[None]] = set()
        self.previous_tick = clock()
        self.previous_wall = time.time()

    def heartbeat(self, tab: str) -> None:
        if self.stopped:
            raise HTTPException(409, 'Око остановлено')
        self.tabs[tab] = self.clock()
        self.idle_since = None

    def live(self) -> bool:
        now = self.clock()
        self.tabs = {t: stamp for t, stamp in self.tabs.items() if now-stamp < self.lease}
        return bool(self.tabs) and not self.stopped

    async def cancel(self) -> None:
        # Invalidate before waiting: even an adapter suppressing cancellation may
        # not publish its old generation when another tab is still alive.
        if self.store:
            self.store.interrupted()
        task, self.active = self.active, None
        if task:
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=.25)
            if task in done:
                if not task.cancelled():
                    task.exception()
            else:
                self.detached.add(task)

                def completed(finished: asyncio.Task[None]) -> None:
                    self.detached.discard(finished)
                    if not finished.cancelled():
                        finished.exception()

                task.add_done_callback(completed)

    async def execute(self, job: Job) -> None:
        assert self.store is not None
        try:
            if job.kind == 'delete':
                self.store.delete(job)
            elif self.source and job.kind == 'discover':
                items, cursor = await self.source.discover(self.store.cursor(self.source_feed))
                if self.live() and self.store.valid(job):
                    self.store.discovered(items, cursor, job, self.source_feed)
            elif self.source and job.kind == 'fetch':
                publication = self.store.publication(job.item)
                if publication:
                    material = await self.source.fetch(publication)
                    if self.live():
                        self.store.save(job, material)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never persist exception text: providers can put content/secrets in it.
            if not self.stopped and self.store.valid(job):
                self.store.retry(job, self.clock(), 'Ошибка удаления' if job.kind == 'delete' else 'Ошибка сбора')

    async def run(self) -> None:
        try:
            self.store = Store(self.root, self.fault)
            config = self.store.subscriptions
            if (self.source and self.source_feed == 'demo') or any(c['feed_id'] == 'demo' for c in self.store.cards()):
                config.ensure_demo()
            if self.source:
                self.store.subscriptions.check_owner('feed', self.source_feed)
                self.store.enqueue_discovery(self.source_feed)
        except (sqlite3.DatabaseError, OSError):
            self.error = 'Хранилище недоступно или повреждено. Состояние сохранено; сбор остановлен.'
        self.idle_since = self.clock()
        try:
            while not self.stopped:
                now, wall = self.clock(), time.time()
                # Invalidate leases after suspended execution (including platforms
                # whose monotonic clock does not advance during machine sleep).
                if max(now-self.previous_tick, wall-self.previous_wall) > self.lease:
                    self.tabs.clear()
                self.previous_tick, self.previous_wall = now, wall
                if not self.live():
                    await self.cancel()
                    if self.idle_since is None:
                        self.idle_since = now
                    if now-self.idle_since >= self.grace:
                        self.stopped = True
                        break
                elif self.store:
                    self.idle_since = None
                    if self.active and self.active.done():
                        await self.active
                        self.active = None
                    if self.active is None:
                        status = self.store.status()
                        job = self.store.next_job(now, deleting_only=status['deleting'] or self.source is None, source_feed=self.source_feed)
                        if job:
                            self.active = asyncio.create_task(self.execute(job))
                await asyncio.sleep(.02)
        finally:
            await self.cancel()
            if self.store:
                self.store.db.close()
                self.store = None
            self.shutdown()


def create_app(root: Path, *, source: Source | None = None, token: str | None = None,
               clock: Callable[[], float] = time.monotonic,
               fault: Callable[[str], None] = lambda _: None,
               shutdown: Callable[[], None] = lambda: None,
               lease: float = 6, grace: float = 15, source_feed: str = 'demo') -> FastAPI:
    token = token or secrets.token_urlsafe(32)
    runtime = Runtime(root, source, clock, fault, shutdown, lease, grace, source_feed)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(runtime.run())
        # Initialise storage before accepting the first API call.
        await asyncio.sleep(0)
        yield
        runtime.stopped = True
        await task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime

    @app.middleware('http')
    async def boundary(request: Request, call_next: Any) -> Response:
        host = request.headers.get('host', '')
        if not re.fullmatch(r'(127\.0\.0\.1|localhost|testserver)(:[0-9]+)?', host):
            return JSONResponse({'detail': 'Недопустимый адрес'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != f'http://{host}':
            return JSONResponse({'detail': 'Посторонняя страница'}, status_code=403)
        if request.url.path.startswith('/api/'):
            supplied = request.headers.get('authorization', '')
            if not secrets.compare_digest(supplied, f'Bearer {token}'):
                return JSONResponse({'detail': 'Требуется локальная сессия'}, status_code=403)
        response: Response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def store() -> Store:
        if runtime.stopped:
            raise HTTPException(409, 'Око остановлено')
        if runtime.error or runtime.store is None:
            raise HTTPException(503, runtime.error or 'Хранилище не готово')
        return runtime.store

    @app.get('/api/status')
    async def status() -> dict[str, Any]:
        state: dict[str, Any] = runtime.store.status() if runtime.store and not runtime.stopped else dict(jobs=[], errors=[], deleting=False)
        pending = any(j['status'] in ('pending', 'running') for j in state['jobs'])
        return dict(**state, error=runtime.error, stopped=runtime.stopped,
                    live_tabs=len(runtime.tabs), stage='Остановлено' if runtime.stopped else
                    'Ошибка хранилища' if runtime.error else 'Завершено с ошибками' if state['errors'] else
                    'Удаление' if state['deleting'] else 'Источник не подключён' if pending and not runtime.source else 'Сбор' if pending else 'Готово',
                    ready_sources=0 if pending or state['errors'] else int(runtime.source is not None),
                    total_sources=int(runtime.source is not None))

    @app.post('/api/tabs/{tab}/heartbeat')
    async def heartbeat(tab: str) -> dict[str, bool]:
        if not re.fullmatch(r'[\w-]{1,80}', tab):
            raise HTTPException(400, 'Неверная вкладка')
        runtime.heartbeat(tab)
        return {'ok': True}

    @app.post('/api/tabs/{tab}/close')
    async def close_tab(tab: str) -> dict[str, bool]:
        runtime.tabs.pop(tab, None)
        return {'ok': True}

    @app.post('/api/stop')
    async def stop() -> dict[str, bool]:
        runtime.stopped = True
        await runtime.cancel()
        return {'ok': True}

    def subscriptions() -> Subscriptions:
        return store().subscriptions

    @app.get('/api/subscriptions')
    async def subscription_list() -> dict[str, Any]:
        config = subscriptions()
        return dict(folders=config.folders(), feeds=config.feeds())

    @app.post('/api/import/preview')
    async def import_preview(body: ImportRequest) -> dict[str, Any]:
        try:
            return preview(body.opml)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @app.post('/api/import')
    async def import_subscriptions(body: ImportRequest) -> dict[str, Any]:
        try:
            result = subscriptions().import_opml(body)
            if 'conflicts' in result:
                raise HTTPException(409, result)
            return result
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @app.get('/api/settings/{scope}/{owner}')
    async def effective_settings(scope: str, owner: str) -> dict[str, Any]:
        try:
            return subscriptions().effective(scope, owner)
        except LookupError as error:
            raise HTTPException(404, str(error)) from None

    @app.patch('/api/settings/{scope}/{owner}')
    async def settings_patch(scope: str, owner: str, body: Overrides) -> dict[str, Any]:
        try:
            return subscriptions().update_settings(scope, owner, body)
        except LookupError as error:
            raise HTTPException(404, str(error)) from None

    @app.patch('/api/feeds/{identity}')
    async def feed_patch(identity: str, body: FeedPatch) -> dict[str, Any]:
        try:
            return subscriptions().update_feed(identity, body)
        except LookupError as error:
            raise HTTPException(404, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @app.get('/api/folders')
    async def folders() -> list[dict[str, Any]]:
        config = subscriptions()
        cards = reader_cards()
        return [dict(**folder, count=sum(c['folder_id'] == folder['id'] for c in cards)) for folder in config.folders()]

    def reader_cards() -> list[dict[str, Any]]:
        feeds = {f['id']: f for f in subscriptions().feeds()}
        return [dict(**card, folder_id=feeds[card['feed_id']]['folder_id'], importance=feeds[card['feed_id']]['importance'])
                for card in store().cards() if card['feed_id'] in feeds]

    @app.get('/api/cards')
    async def cards(folder: str | None = None, sort: Literal['newest', 'oldest', 'importance'] = 'newest') -> list[dict[str, Any]]:
        result = [card for card in reader_cards() if folder is None or card['folder_id'] == folder]
        if sort == 'importance':
            result.sort(key=lambda c: (-{'normal': 0, 'elevated': 1, 'high': 2}[c['importance']], -c['published'], c['id']))
        else:
            result.sort(key=lambda c: (c['published'] if sort == 'oldest' else -c['published'], c['id']))
        return result

    @app.get('/api/materials/{item}')
    async def material(item: str) -> dict[str, Any]:
        result = store().material(item)
        if not result:
            raise HTTPException(404, 'Материал недоступен')
        return result

    @app.post('/api/materials/{item}/viewed')
    async def viewed(item: str) -> dict[str, bool]:
        if not store().viewed(item):
            raise HTTPException(404, 'Материал не найден')
        if runtime.active:
            # Cancelling is an optimisation; generation/viewed checks are the guard.
            await runtime.cancel()
        job = store().next_job(runtime.clock(), deleting_only=True)
        if job:
            await runtime.execute(job)
        return {'ok': True}

    web = Path(__file__).parent / 'web'

    @app.get('/')
    async def index() -> FileResponse:
        return FileResponse(web / 'index.html')

    @app.get('/app.js')
    async def js() -> FileResponse:
        return FileResponse(web / 'app.js', media_type='text/javascript')

    @app.get('/style.css')
    async def css() -> FileResponse:
        return FileResponse(web / 'style.css', media_type='text/css')

    return app
