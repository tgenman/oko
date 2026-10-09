import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
import re
import secrets
import time

import httpx
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from .sources import Source
from .processing import ProcessorConfig
from .codex import CodexClient
from .store import Store
from .telegram import TelegramClient, ConnectTelegram
from .subscriptions import Subscriptions, ImportRequest, Overrides, FeedPatch, preview
from .runtime import Runtime, ActionError
from .youtube import CaptionsClient


def create_app(root: Path, *, source: Source | None = None, token: str | None = None,
               clock: Callable[[], float] = time.monotonic,
               fault: Callable[[str], None] = lambda _: None,
               shutdown: Callable[[], None] = lambda: None,
               lease: float = 6, grace: float = 15, source_feed: str = 'demo',
               rss_enabled: bool = False, rss_transport: httpx.AsyncBaseTransport | None = None,
               telegram_client: TelegramClient | None = None, ai_transport: httpx.AsyncBaseTransport | None = None, codex_client: CodexClient | None = None,
               youtube_captions: CaptionsClient | None = None) -> FastAPI:
    token = token or secrets.token_urlsafe(32)
    runtime = Runtime(root, source, clock, fault, shutdown, lease, grace, source_feed, rss_enabled, rss_transport, telegram_client, ai_transport, codex_client, youtube_captions)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(runtime.run())
        # Initialise storage before accepting the first API call.
        await asyncio.sleep(0)
        yield
        await runtime.stop()
        await task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime

    @app.exception_handler(ActionError)
    async def action_error(request: Request, error: ActionError) -> JSONResponse:
        return JSONResponse({'detail': error.detail}, status_code={'conflict': 409, 'invalid': 400, 'missing': 404, 'unavailable': 503}[error.kind])

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
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def store() -> Store:
        return runtime.require_store()

    @app.get('/api/status')
    async def status() -> dict[str, Any]:
        return runtime.status()

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

    @app.post('/api/materials/{item}/theses/retry')
    async def retry_theses(item: str) -> dict[str, bool]:
        await runtime.retry_theses(item)
        return {'ok': True}

    @app.get('/api/telegram/accounts')
    async def telegram_accounts() -> list[str]:
        return await runtime.telegram_accounts()

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

    @app.post('/api/import')
    async def import_subscriptions(body: ImportRequest) -> dict[str, Any]:
        return await runtime.import_subscriptions(body)

    @app.get('/api/settings/{scope}/{owner}')
    async def effective_settings(scope: str, owner: str) -> dict[str, Any]:
        try:
            return subscriptions().effective(scope, owner)
        except LookupError as error:
            raise HTTPException(404, str(error)) from None

    @app.patch('/api/settings/{scope}/{owner}')
    async def settings_patch(scope: str, owner: str, body: Overrides) -> dict[str, Any]:
        return await runtime.update_settings(scope, owner, body)

    @app.patch('/api/feeds/{identity}')
    async def feed_patch(identity: str, body: FeedPatch) -> dict[str, Any]:
        return await runtime.patch_feed(identity, body)

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

    @app.get('/api/materials/{item}')
    async def material(item: str) -> dict[str, Any]:
        result = store().material(item)
        if not result:
            raise HTTPException(404, 'Материал недоступен')
        return result

    @app.post('/api/materials/{item}/viewed')
    async def viewed(item: str) -> dict[str, bool]:
        await runtime.viewed(item)
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
