import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
import re
import secrets
import time

import httpx
from typing import Any, Literal

from pydantic import BaseModel, Field

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from oko.collection.contracts import Source
from oko.processing.llamacpp import ProcessorConfig
from oko.processing.codex import CodexClient
from oko.processing.execution import CodexSettings
from oko.reviews.digests import DigestRequest
from oko.storage.store import Store
from oko.collection.telegram.source import TelegramClient, ConnectTelegram, TelegramPeer
from oko.subscriptions.subscriptions import Subscriptions, AddFeed, FeedURL, ImportRequest, Overrides, FeedPatch, preview
from oko.reader.runtime import Runtime, ActionError
from oko.collection.youtube.source import CaptionsClient
from oko.diagnostics import Journal, BrowserError, active_journal, diagnostic_source, capture_scope, record_caught


class HistoryPause(BaseModel):
    paused: bool = Field(strict=True)


class CompletionRequest(BaseModel):
    token: str = Field(min_length=1, max_length=100)


def create_app(root: Path, *, source: Source | None = None, token: str | None = None,
               clock: Callable[[], float] = time.monotonic,
               fault: Callable[[str], None] = lambda _: None,
               shutdown: Callable[[], None] = lambda: None,
               lease: float = 6, grace: float = 15, source_feed: str = 'demo',
               rss_enabled: bool = False, rss_transport: httpx.AsyncBaseTransport | None = None,
               telegram_client: TelegramClient | None = None, ai_transport: httpx.AsyncBaseTransport | None = None, codex_client: CodexClient | None = None,
               youtube_captions: CaptionsClient | None = None, wall_clock: Callable[[], float] = time.time) -> FastAPI:
    token = token or secrets.token_urlsafe(32)
    runtime = Runtime(root, source, clock, fault, shutdown, lease, grace, source_feed, rss_enabled, rss_transport, telegram_client, ai_transport, codex_client, youtube_captions, wall_clock)
    journal = Journal(root, token)
    runtime.diagnostics = journal

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()

        def background_error(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            error = context.get('exception')
            if isinstance(error, BaseException):
                journal.capture(error, 'Фоновая задача asyncio')
            else:
                journal.add('asyncio', str(context.get('message', 'Ошибка фоновой задачи')), '')
            if previous_handler:
                previous_handler(loop, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(background_error)
        task = asyncio.create_task(runtime.run())

        def runtime_finished(finished: asyncio.Task[None]) -> None:
            if not finished.cancelled():
                error = finished.exception()
                if error and not getattr(error, '_oko_diagnostic_group', None):
                    journal.capture(error, 'Главный цикл Око')

        task.add_done_callback(runtime_finished)
        # Initialise storage before accepting the first API call.
        await asyncio.sleep(0)
        try:
            yield
        finally:
            try:
                await runtime.stop()
                await task
            except Exception:
                record_caught('Остановка главного цикла')
                error = task.exception() if task.done() and not task.cancelled() else None
                if error and not getattr(error, '_oko_diagnostic_group', None):
                    journal.capture(error, 'Главный цикл Око')
                raise
            finally:
                loop.set_exception_handler(previous_handler)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime

    @app.exception_handler(ActionError)
    async def action_error(request: Request, error: ActionError) -> JSONResponse:
        group = journal.capture(error, request.method + ' ' + request.url.path)
        return JSONResponse({'detail': error.detail}, headers={'X-Oko-Error': group} if group else {'X-Oko-Expected': '1'} if error.diagnostic_skip else {}, status_code={'conflict': 409, 'invalid': 400, 'missing': 404, 'unavailable': 503}[error.kind])

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> Response:
        group = journal.capture(error, request.method + ' ' + request.url.path)
        response = await http_exception_handler(request, error)
        if group:
            response.headers['X-Oko-Error'] = group
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> Response:
        group = journal.capture(error, request.method + ' ' + request.url.path)
        response = await request_validation_exception_handler(request, error)
        if group:
            response.headers['X-Oko-Error'] = group
        return response

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
        context = active_journal.set(journal)
        source_context = diagnostic_source.set(request.method + ' ' + request.url.path)
        scope_context = capture_scope.set(secrets.token_hex(16))
        try:
            response: Response = await call_next(request)
        except Exception as error:
            group = journal.capture(error, request.method + ' ' + request.url.path)
            response = JSONResponse({'detail': 'Ошибка Око; подробности в диагностике'}, status_code=500,
                                    headers={'X-Oko-Error': group} if group else {})
        finally:
            capture_scope.reset(scope_context)
            diagnostic_source.reset(source_context)
            active_journal.reset(context)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def store() -> Store:
        return runtime.require_store()

    @app.get('/api/debug')
    async def debug_entries() -> dict[str, Any]:
        return dict(groups=journal.entries(), persistence_error=journal.persistence_error)

    @app.delete('/api/debug')
    async def clear_debug() -> dict[str, Any]:
        return dict(ok=journal.clear(), persistence_error=journal.persistence_error)

    @app.post('/api/debug')
    async def debug_browser(body: BrowserError) -> dict[str, Any]:
        group = journal.add(body.source, body.message, body.traceback, error_type=body.error_type,
                            location=body.location, event_id=body.event_id,
                            timestamp=body.occurred_at.isoformat() if body.occurred_at else None,
                            count=body.count, first=body.first_at.isoformat() if body.first_at else None,
                            stream_id=body.stream_id, sequence=body.sequence)
        return dict(ok=not bool(journal.persistence_error), group=group, entry=next((entry for entry in journal.entries() if entry['id'] == group), None), persistence_error=journal.persistence_error)

    @app.get('/api/status')
    async def status() -> dict[str, Any]:
        return dict(runtime.status(), debug_count=len(journal.records), debug_persistence_error=journal.persistence_error)

    @app.post('/api/tabs/{tab}/heartbeat')
    async def heartbeat(tab: str) -> dict[str, bool]:
        if not re.fullmatch(r'[\w-]{1,80}', tab):
            raise HTTPException(400, 'Неверная вкладка')
        await runtime.heartbeat(tab)
        return {'ok': True}

    @app.post('/api/tabs/{tab}/close')
    async def close_tab(tab: str) -> dict[str, bool]:
        await runtime.close_tab(tab)
        return {'ok': True}

    @app.post('/api/stop')
    async def stop() -> dict[str, bool]:
        await runtime.stop()
        return {'ok': True}

    def subscriptions() -> Subscriptions:
        return store().subscriptions

    @app.get('/api/processors/codex')
    async def codex_state() -> dict[str, Any]:
        return runtime.codex_status()

    @app.get('/api/processors/codex/models')
    async def codex_models() -> dict[str, Any]:
        return dict(models=await runtime.codex_models())

    @app.put('/api/processors/codex')
    async def codex_save(body: CodexSettings) -> dict[str, Any]:
        await runtime.configure_codex(body)
        return body.model_dump()

    @app.post('/api/processors/codex/check')
    async def codex_check() -> dict[str, Any]:
        return await runtime.check_codex()

    @app.get('/api/processors/llamacpp')
    async def processor_config() -> dict[str, Any] | None:
        config = store().processor_config()
        return config.model_dump() if config else None

    @app.put('/api/processors/llamacpp')
    async def processor_save(body: ProcessorConfig) -> dict[str, Any]:
        await runtime.configure_processor(body)
        return body.model_dump()

    @app.post('/api/processors/llamacpp/check')
    async def processor_check() -> dict[str, Any]:
        return await runtime.check_processor()

    @app.post('/api/materials/{item}/regenerate')
    async def regenerate(item: str) -> dict[str, bool]:
        await runtime.regenerate(item)
        return {'ok': True}

    @app.post('/api/materials/{item}/theses/retry')
    async def retry_theses(item: str) -> dict[str, bool]:
        await runtime.retry_theses(item)
        return {'ok': True}

    @app.get('/api/telegram/accounts')
    async def telegram_accounts() -> list[str]:
        return await runtime.telegram_accounts()

    @app.post('/api/telegram/topics')
    async def telegram_topics(body: TelegramPeer) -> list[dict[str, Any]]:
        return await runtime.telegram_topics(body)

    @app.post('/api/telegram/connect')
    async def telegram_connect(body: ConnectTelegram) -> dict[str, Any]:
        return await runtime.connect_telegram(body)

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

    @app.post('/api/feeds')
    async def add_feed(body: AddFeed) -> dict[str, Any]:
        return await runtime.add_feed(body)

    @app.post('/api/feeds/verify')
    async def verify_feed(body: FeedURL) -> dict[str, Any]:
        return await runtime.verify_feed(body)

    @app.post('/api/import')
    async def import_subscriptions(body: ImportRequest) -> dict[str, Any]:
        return await runtime.import_subscriptions(body)

    @app.get('/api/youtube')
    async def youtube_settings() -> dict[str, Any]:
        return runtime.youtube_status()

    @app.put('/api/youtube')
    async def youtube_configure(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            raise ActionError('invalid', 'Некорректные настройки прокси', expected=True) from None
        return await runtime.configure_youtube(body)

    @app.get('/youtube-settings.js')
    async def youtube_settings_script() -> FileResponse:
        return FileResponse(web / 'youtube-settings.js', media_type='text/javascript')

    @app.get('/api/settings/{scope}/{owner}')
    async def effective_settings(scope: str, owner: str) -> dict[str, Any]:
        try:
            return subscriptions().effective(scope, owner)
        except LookupError as error:
            raise HTTPException(404, str(error)) from None

    @app.patch('/api/settings/{scope}/{owner}')
    async def settings_patch(scope: str, owner: str, body: Overrides) -> dict[str, Any]:
        return await runtime.update_settings(scope, owner, body)

    @app.patch('/api/feeds/{identity}/history')
    async def history_pause(identity: str, body: HistoryPause) -> dict[str, Any]:
        return await runtime.pause_history(identity, body.paused)

    @app.patch('/api/feeds/{identity}')
    async def feed_patch(identity: str, body: FeedPatch) -> dict[str, Any]:
        return await runtime.patch_feed(identity, body)

    @app.get('/api/folders')
    async def folders() -> list[dict[str, Any]]:
        return store().reading.navigation()['folders']

    @app.get('/api/navigation')
    async def navigation() -> dict[str, Any]:
        return dict(store().reading.navigation())

    @app.get('/api/cards')
    async def cards(folder: str | None = None, feed: str | None = None, sort: Literal['newest', 'oldest', 'importance'] = 'newest', show_hidden: bool = False) -> list[dict[str, Any]]:
        result = store().reading.cards(show_hidden, folder, feed)
        if sort == 'importance':
            result.sort(key=lambda c: (-{'normal': 0, 'elevated': 1, 'high': 2}[c['importance']], -c['published'], c['id']))
        else:
            result.sort(key=lambda c: (not c.get('published_known', True), c['published'] if sort == 'oldest' else -c['published'], c['id']))
        return result

    @app.post('/api/folders/{folder}/completion/preview')
    async def preview_completion(folder: str) -> dict[str, Any]:
        return await runtime.preview_completion(folder)

    @app.post('/api/folders/{folder}/completion')
    async def complete_folder(folder: str, body: CompletionRequest) -> dict[str, bool]:
        await runtime.complete_folder(folder, body.token)
        return {'ok': True}

    @app.post('/api/collect')
    async def collect() -> dict[str, bool]:
        await runtime.collect()
        return {'ok': True}

    @app.post('/api/materials/{item}/retry')
    async def retry_material(item: str) -> dict[str, bool]:
        await runtime.retry_material(item)
        return {'ok': True}

    @app.get('/api/materials/{item}/images/{name}')
    async def image(item: str, name: str) -> Response:
        current = store()
        if not re.fullmatch(r'image-[0-9]+\.(png|jpg|gif|webp)', name) or current.material(item) is None:
            raise HTTPException(404, 'Изображение недоступно')
        path = current.path(item).parent / name
        if not path.is_file():
            raise HTTPException(404, 'Изображение недоступно')
        # Read before yielding: a viewed/delete operation cannot race FileResponse.
        data = path.read_bytes()
        mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}[path.suffix]
        return Response(data, media_type=mime)

    @app.get('/api/materials/{item}/media/{name}')
    async def attachment(item: str, name: str) -> Response:
        current = store()
        if not re.fullmatch(r'media-[0-9]+\.[a-z0-9]+', name):
            raise HTTPException(404, 'Вложение недоступно')
        material = current.material(item)
        media = next((a for a in material.get('attachments', []) if a['url'].endswith('/' + name)), None) if material else None
        path = current.path(item).parent / name
        if not media or not path.is_file():
            raise HTTPException(404, 'Вложение недоступно')
        return Response(path.read_bytes(), media_type=media['mime'], headers={'Content-Disposition': 'inline'})

    @app.get('/api/materials/{item}/versions/{number}')
    async def previous_version(item: str, number: int) -> dict[str, Any]:
        result = store().versions.read(item, number)
        if result is None:
            raise HTTPException(404, 'Версия недоступна')
        return result

    @app.get('/api/materials/{item}/versions/{number}/{kind}/{name}')
    async def previous_media(item: str, number: int, kind: str, name: str) -> Response:
        current = store()
        material = current.versions.read(item, number)
        if kind not in ('images', 'media') or material is None or Path(name).name != name:
            raise HTTPException(404, 'Вложение недоступно')
        images = material['images']
        attachments = material['attachments']
        match = next((a for a in attachments if a['url'].endswith('/'+name)), None)
        if kind == 'images' and any(url.endswith('/'+name) for url in images):
            mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}[Path(name).suffix]
        elif kind == 'media' and match:
            mime = match['mime']
        else:
            raise HTTPException(404, 'Вложение недоступно')
        path = current.versions.path(item, number).parent / name
        if not path.is_file():
            raise HTTPException(404, 'Вложение недоступно')
        return Response(path.read_bytes(), media_type=mime)

    @app.get('/api/materials/{item}')
    async def material(item: str) -> dict[str, Any]:
        result = store().reading.material(item)
        if not result:
            raise HTTPException(404, 'Материал недоступен')
        return result

    @app.post('/api/materials/{item}/filter/restore')
    async def restore_filter(item: str) -> dict[str, bool]:
        await runtime.restore_filter(item)
        return {'ok': True}

    @app.post('/api/materials/{item}/filter/retry')
    async def retry_filter(item: str) -> dict[str, bool]:
        await runtime.retry_filter(item)
        return {'ok': True}

    @app.post('/api/digests/{scope}/{owner}')
    async def create_digest(scope: str, owner: str, body: DigestRequest) -> dict[str, str]:
        return {'id': await runtime.create_digest(scope, owner, body)}

    @app.post('/api/materials/{item}/viewed')
    async def viewed(item: str) -> dict[str, bool]:
        await runtime.viewed(item)
        return {'ok': True}

    web = Path(__file__).resolve().parents[1] / 'web'

    @app.get('/')
    async def index() -> FileResponse:
        return FileResponse(web / 'index.html')

    @app.get('/app.js')
    async def js() -> FileResponse:
        return FileResponse(web / 'app.js', media_type='text/javascript')

    @app.get('/reading-tree.js')
    async def reading_tree_js() -> FileResponse:
        return FileResponse(web / 'reading-tree.js', media_type='text/javascript')

    @app.get('/material-list.js')
    async def material_list_js() -> FileResponse:
        return FileResponse(web / 'material-list.js', media_type='text/javascript')

    @app.get('/debug.js')
    async def debug_js() -> FileResponse:
        return FileResponse(web / 'debug.js', media_type='text/javascript')

    @app.get('/initial-history.js')
    async def initial_history_module() -> FileResponse:
        return FileResponse(web / 'initial-history.js', media_type='text/javascript')

    @app.get('/feed-connection.js')
    async def feed_connection_js() -> FileResponse:
        return FileResponse(web / 'feed-connection.js', media_type='text/javascript')

    @app.get('/opened-material.js')
    async def opened_material_js() -> FileResponse:
        return FileResponse(web / 'opened-material.js', media_type='text/javascript')

    @app.get('/telegram-connection.js')
    def telegram_connection_js() -> FileResponse:
        return FileResponse(web / 'telegram-connection.js', media_type='text/javascript')

    @app.get('/controls.js')
    async def controls_js() -> FileResponse:
        return FileResponse(web / 'controls.js', media_type='text/javascript')

    @app.get('/ui-operations.js')
    async def ui_operations_js() -> FileResponse:
        return FileResponse(web / 'ui-operations.js', media_type='text/javascript')

    @app.get('/menus.js')
    async def menus_js() -> FileResponse:
        return FileResponse(web / 'menus.js', media_type='text/javascript')

    @app.get('/settings-editor.js')
    async def settings_editor_js() -> FileResponse:
        return FileResponse(web / 'settings-editor.js', media_type='text/javascript')

    @app.get('/codex-settings.js')
    async def codex_settings_script() -> FileResponse:
        return FileResponse(web / 'codex-settings.js', media_type='text/javascript')

    @app.get('/model-settings.js')
    async def model_settings_js() -> FileResponse:
        return FileResponse(web / 'model-settings.js', media_type='text/javascript')

    @app.get('/opml-import.js')
    async def opml_import_js() -> FileResponse:
        return FileResponse(web / 'opml-import.js', media_type='text/javascript')

    @app.get('/style.css')
    async def css() -> FileResponse:
        return FileResponse(web / 'style.css', media_type='text/css')

    return app
