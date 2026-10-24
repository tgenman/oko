import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

import httpx
from typing import Any


from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse, Response
from oko.collection.contracts import Source
from oko.processing.codex import CodexClient
from oko.collection.telegram.source import TelegramClient
from oko.host import Host
from oko.application.reader import ReaderApplication
from oko.execution import ActionError
from oko.reader.lifetime import Lifetime
from oko.reader.browser_auth import BrowserSessions, COOKIE, MAX_AGE
from oko.reader import api_v3
from oko.reader.http_schemas import SchemaRoute
from oko.reader.generated.models import BrowserSessionRevoked
from oko.collection.contracts import CaptionsClient
from oko.collection.youtube.history import HistoryClient
from oko.diagnostics import Journal, active_journal, diagnostic_source, diagnostic_materials, capture_scope, record_caught


def create_app(root: Path, *, source: Source | None = None, token: str | None = None,
               clock: Callable[[], float] = time.monotonic,
               fault: Callable[[str], None] = lambda _: None,
               shutdown: Callable[[], None] = lambda: None,
               lease: float = 6, grace: float = 15, api_only: bool = False, persistent: bool = False,
               public_url: str | None = None, source_feed: str = 'demo',
               rss_enabled: bool = False, rss_transport: httpx.AsyncBaseTransport | None = None,
               telegram_client: TelegramClient | None = None, ai_transport: httpx.AsyncBaseTransport | None = None, codex_client: CodexClient | None = None,
               youtube_captions: CaptionsClient | None = None, youtube_history: HistoryClient | None = None, wall_clock: Callable[[], float] = time.time) -> FastAPI:
    if not api_only:
        from oko.reader.static import validate_frontend
        validate_frontend()
    public_host: str | None = None
    if public_url:
        parsed = urlsplit(public_url)
        if parsed.scheme != 'http' or parsed.hostname not in ('oko', 'localhost', '127.0.0.1') or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ValueError('Public URL must be an allowed local HTTP origin')
        public_host = parsed.netloc
    lifetime = Lifetime('explicit' if api_only or persistent else 'client_presence', clock=clock, wall_clock=wall_clock, lease=lease, grace=grace)
    browser_sessions = BrowserSessions(root, wall_clock) if persistent else None
    token = token or secrets.token_urlsafe(32)
    instance_id = secrets.token_hex(16)
    runtime = Host(root, source, clock, fault, shutdown, lifetime.admitted, lifetime.should_stop, source_feed, rss_enabled, rss_transport, telegram_client, ai_transport, codex_client, youtube_captions, youtube_history, wall_clock)
    application = ReaderApplication(runtime.execution, runtime.jobs, clock=clock, wall_clock=wall_clock,
        sources=runtime.collection._sources, bindings=runtime.collection._telegram_bindings, sync_sources=runtime.collection._sync_sources,
        interval_changed=lambda value: setattr(runtime.collection, '_collection_interval', value), stage=lambda: runtime.jobs.stage,
        source_description=runtime.collection._collection.description, rss_transport=rss_transport, ai_transport=ai_transport,
        telegram=telegram_client, telegram_http=runtime.collection._telegram_http, codex=runtime.codex,
        stop=runtime.stop, admission_changed=runtime.admission_changed, set_power=runtime.set_power)
    journal = Journal(root, token)
    runtime.collection.clean_collection=application._clean_collection
    runtime.collection.verify_telegram_profile=application._verify_telegram_profile
    runtime.clear_feed_checks=application._feed_checks.clear
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
    app.router.route_class = SchemaRoute
    app.state.host = runtime
    app.state.application = application

    @app.exception_handler(ActionError)
    async def action_error(request: Request, error: ActionError) -> JSONResponse:
        if request.url.path.startswith('/api/v3/'):
            from oko.reader.generated import models as v3
            command_id = v3.CommandId(request.state.command_id) if request.method != 'GET' else None
            if error.code == 'runtime_disabled':
                failure = api_v3.disabled_error(request.state.command_id if request.method != 'GET' else None)
                return api_v3.error_response(failure.status, failure.value)
            if runtime.stopped:
                return api_v3.error_response(409, v3.ErrorEnvelope(v3.RuntimeStoppedError(code='runtime_stopped', message='Око остановлено', details=None, diagnostic_id=None, command_id=command_id)))
            if error.kind == 'unavailable':
                return api_v3.error_response(503, v3.ErrorEnvelope(v3.StorageUnavailableError(code='storage_unavailable', message=str(error.detail), details=v3.StorageUnavailableErrorDetails(retry_at=None), diagnostic_id=None, command_id=command_id)))
            return api_v3.error_response(400, v3.ErrorEnvelope(v3.ActionInvalidError(code='action_invalid', message=str(error.detail), details=v3.ActionInvalidErrorDetails(reason='invalid_state'), diagnostic_id=None, command_id=command_id)))
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
        if request.url.path.startswith('/api/v3/'):
            return api_v3.error_response(422, api_v3.invalid('body', ''))
        group = journal.capture(error, request.method + ' ' + request.url.path)
        response = await request_validation_exception_handler(request, error)
        if group:
            response.headers['X-Oko-Error'] = group
        return response

    @app.middleware('http')
    async def boundary(request: Request, call_next: Any) -> Response:
        api = request.url.path == '/api' or request.url.path.startswith('/api/')
        v3 = request.url.path.startswith('/api/v3/')
        host = request.headers.get('host', '')
        if host != public_host and not re.fullmatch(r'(127\.0\.0\.1|localhost|testserver)(:[0-9]+)?', host):
            return api_v3.error_response(403, api_v3.security_error('host_not_allowed')) if api else JSONResponse({'detail': 'Недопустимый адрес'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != f'http://{host}':
            return api_v3.error_response(403, api_v3.security_error('origin_not_allowed')) if api else JSONResponse({'detail': 'Посторонняя страница'}, status_code=403)
        if api:
            supplied = request.headers.get('authorization', '')
            bearer = secrets.compare_digest(supplied, f'Bearer {token}')
            cookie = request.cookies.get(COOKIE, '')
            remembered = browser_sessions is not None and browser_sessions.valid(cookie)
            if not bearer and (supplied or not remembered):
                return api_v3.error_response(403, api_v3.security_error('session_required')) if api else JSONResponse({'detail': 'Требуется локальная сессия'}, status_code=403)
            if not bearer and (request.headers.get('sec-fetch-site') == 'cross-site' or
                    request.method not in ('GET', 'HEAD') and origin != f'http://{host}'):
                return api_v3.error_response(403, api_v3.security_error('origin_not_allowed'))
        if api and not v3:
            from oko.reader.generated.models import ApiVersionRemovedError, ApiVersionRemovedErrorDetails
            value = ApiVersionRemovedError(code='api_version_removed', message='Прежний API отключён; используйте /api/v3', details=ApiVersionRemovedErrorDetails(api_version='2.0.0'), diagnostic_id=None, command_id=None)
            return JSONResponse(value.model_dump(mode='json'), status_code=410, headers={'Cache-Control':'no-store'})
        if v3 and 'X-Oko-Action' in request.headers and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request.headers['X-Oko-Action']):
            return api_v3.error_response(422, api_v3.invalid('header', 'X-Oko-Action'))
        context = active_journal.set(journal)
        source_context = diagnostic_source.set(request.method + ' ' + request.url.path)
        scope_context = capture_scope.set(secrets.token_hex(16))
        action_id = request.headers.get('X-Oko-Action', '')
        if not re.fullmatch(r'[\w-]{1,100}', action_id):
            action_id = secrets.token_hex(16)
        request.state.command_id = action_id
        material_path = re.match(r'/api/(?:v3/)?materials/([\w-]+)(?:/|$)', request.url.path)
        material_context = diagnostic_materials.set((material_path[1],) if material_path else ())
        try:
            response: Response = await call_next(request)
        except Exception as error:
            group = journal.capture(error, request.method + ' ' + request.url.path)
            if v3:
                if not group:
                    forgotten = diagnostic_materials.set(())
                    try:
                        group = journal.add('HTTP', 'Ошибка Око', '') or secrets.token_hex(16)
                    finally:
                        diagnostic_materials.reset(forgotten)
                from oko.reader.generated.models import CommandId, ErrorEnvelope, InternalErrorError
                response = api_v3.error_response(500, ErrorEnvelope(InternalErrorError(code='internal_error',
                    message='Ошибка Око; подробности в диагностике', details=None, diagnostic_id=group, command_id=CommandId(action_id))))
            else:
                response = JSONResponse({'detail': 'Ошибка Око; подробности в диагностике'}, status_code=500,
                                        headers={'X-Oko-Error': group} if group else {})
        finally:
            diagnostic_materials.reset(material_context)
            capture_scope.reset(scope_context)
            diagnostic_source.reset(source_context)
            active_journal.reset(context)
        response.headers['Cache-Control'] = 'no-store'
        if v3 and request.method in ('POST','PUT','PATCH','DELETE'):
            response.headers['X-Oko-Action'] = action_id
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        if v3:
            response.headers['X-Oko-Instance'] = instance_id
        if browser_sessions and request.url.path == '/api/v3/status' and response.status_code == 200 and bearer and not remembered and request.headers.get('X-Oko-Remember') == '1':
            response.set_cookie(COOKIE, browser_sessions.create(), max_age=MAX_AGE,
                                httponly=True, samesite='strict', path='/')
        if browser_sessions and request.url.path == '/api/v3/status' and response.status_code == 200:
            response.headers['X-Oko-Browser-Session'] = 'remembered'
        return response

    api_v3.register(app, application, lifetime, wall_clock, journal)

    @app.delete('/api/v3/browser-session', response_model=BrowserSessionRevoked)
    async def revoke_browser_session(request: Request) -> BrowserSessionRevoked:
        if request.query_params or await request.body():
            raise api_v3.ApiError(422, api_v3.invalid('query' if request.query_params else 'body', ''))
        if browser_sessions:
            browser_sessions.revoke(request.cookies.get(COOKIE, ''))
        return BrowserSessionRevoked(revoked=True)

    if not api_only:
        from oko.reader.docs import install_docs
        from oko.reader.static import install_frontend
        install_docs(app)
        install_frontend(app)

    return app
